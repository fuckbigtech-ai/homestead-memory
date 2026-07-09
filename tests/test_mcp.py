"""api.mcp_server — protocol correctness (per docs/MCP_SPEC.md v1.1) + stdio smoke."""
import json
import subprocess
import sys
from pathlib import Path

from homestead_memory.api import mcp_server as mcp

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "fact.md").write_text(
        "---\nname: fact\nstatus: hot\nupdated: 2026-07-03\n---\n# Fact\n"
        "Allergic to penicillin.\n\n## Changelog\n- 2026-07-03: status active -> hot. ok.\n")
    return tmp_path


def _state(tmp_path, initialized=True):
    s = mcp.ServerState(_vault(tmp_path))
    s.initialized = initialized
    return s


def _req(method, mid=1, **params):
    m = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params:
        m["params"] = params
    return m


# ------------------------------------------------------------ lifecycle rules
def test_initialize_and_id_preserved_exactly(tmp_path):
    s = _state(tmp_path, initialized=False)
    r = mcp.handle_message(_req("initialize", mid="str-id-7"), s)
    assert r["id"] == "str-id-7"                          # id type preserved
    assert r["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert "tools" in r["result"]["capabilities"]


def test_gated_before_initialized_ping_allowed(tmp_path):
    s = _state(tmp_path, initialized=False)
    assert mcp.handle_message(_req("tools/list"), s)["error"]["code"] == -32002
    assert mcp.handle_message(_req("ping"), s)["result"] == {}


def test_notifications_never_get_responses(tmp_path):
    s = _state(tmp_path, initialized=False)
    init_note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    # PREMATURE initialized (no initialize handshake yet) must NOT unlock the server
    assert mcp.handle_message(init_note, s) is None
    assert s.initialized is False
    assert mcp.handle_message(_req("tools/list"), s)["error"]["code"] == -32002
    # real handshake: initialize → initialized → unlocked
    mcp.handle_message(_req("initialize", mid=0), s)
    assert mcp.handle_message(init_note, s) is None
    assert s.initialized is True
    cancelled = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
    assert mcp.handle_message(cancelled, s) is None       # swallowed
    unknown_note = {"jsonrpc": "2.0", "method": "wat/ever"}
    assert mcp.handle_message(unknown_note, s) is None    # unknown notification: silent


def test_invalid_and_unknown(tmp_path):
    s = _state(tmp_path)
    assert mcp.handle_message({"id": 3, "method": "x"}, s)["error"]["code"] == -32600
    assert mcp.handle_message(["not", "a", "dict"], s) is None
    assert mcp.handle_message(_req("no/such/method"), s)["error"]["code"] == -32601


# ------------------------------------------------------------------ tool layer
def test_tools_list_schemas(tmp_path):
    r = mcp.handle_message(_req("tools/list"), _state(tmp_path))
    tools = {t["name"]: t for t in r["result"]["tools"]}
    assert set(tools) == {"memory_ask", "memory_search", "memory_verify",
                          "memory_history", "memory_ingest", "memory_distill",
                          "memory_remember"}
    for t in tools.values():
        assert t["inputSchema"]["type"] == "object"
        assert t["inputSchema"]["additionalProperties"] is False
    assert "nextCursor" not in r["result"]


def test_verify_tool_flattens_findings(tmp_path):
    s = _state(tmp_path)
    (s.vault / "bad.md").write_text(
        "---\nname: bad\nstatus: hot\nmetadata:\n  status: done\n---\nx\n")
    r = mcp.handle_message(_req("tools/call", name="memory_verify", arguments={}), s)
    text = r["result"]["content"][0]["text"]
    assert "ROT DETECTED" in text and "self_contradiction" in text
    assert not r["result"].get("isError")


def test_history_tool(tmp_path):
    s = _state(tmp_path)
    from homestead_memory.core import temporal
    temporal.build(s.vault)
    r = mcp.handle_message(_req("tools/call", name="memory_history",
                                arguments={"note": "fact"}), s)
    assert "status: active → hot" in r["result"]["content"][0]["text"]


def test_unknown_tool_and_bad_args(tmp_path):
    s = _state(tmp_path)
    r = mcp.handle_message(_req("tools/call", name="nope", arguments={}), s)
    assert r["error"]["code"] == -32602
    r = mcp.handle_message(_req("tools/call", name="memory_verify", arguments="x"), s)
    assert r["error"]["code"] == -32602


def test_tool_exception_is_iserror_not_crash(tmp_path, monkeypatch):
    s = _state(tmp_path)
    monkeypatch.setattr(mcp.verify, "verify_vault",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = mcp.handle_message(_req("tools/call", name="memory_verify", arguments={}), s)
    assert r["result"]["isError"] is True
    assert "boom" in r["result"]["content"][0]["text"]


def test_k_clamped(tmp_path):
    assert mcp._clamp_k({"k": 999}) == 20
    assert mcp._clamp_k({"k": -3}) == 1
    assert mcp._clamp_k({"k": "junk"}) == 5


def test_truncation(tmp_path):
    r = mcp._text_result("x" * (mcp.MAX_TEXT + 100))
    assert r["content"][0]["text"].endswith("…[truncated]")


# ------------------------------------------------- real stdio subprocess smoke
def test_stdio_smoke_full_handshake(tmp_path):
    v = _vault(tmp_path)
    lines = "\n".join(json.dumps(m) for m in [
        _req("initialize", mid=0),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _req("tools/list", mid=1),
        _req("tools/call", mid=2, name="memory_verify", arguments={}),
        "this is not json"  # parse error → -32700, id null, loop survives
    ][:4]) + "\nnot-json\n" + json.dumps(_req("ping", mid=3)) + "\n"
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from homestead_memory.api.mcp_server import serve; sys.exit(serve(sys.argv[2]))",
         SRC, str(v)],
        input=lines, capture_output=True, text=True, timeout=60)
    out = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    by_id = {r.get("id"): r for r in out}
    assert by_id[0]["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert {t["name"] for t in by_id[1]["result"]["tools"]} >= {"memory_verify"}
    assert "MEMORY INTACT" in by_id[2]["result"]["content"][0]["text"]
    assert by_id[None]["error"]["code"] == -32700          # parse error, id null
    assert by_id[3]["result"] == {}                        # ping AFTER the bad line
    assert proc.returncode == 0                            # EOF → clean exit


# ------------------------------------------------ audit-driven negative cases
def test_malformed_params_shapes_do_not_crash(tmp_path):
    s = _state(tmp_path)
    r = mcp.handle_message(_req("tools/call") | {"params": "not-an-object"}, s)
    assert r["error"]["code"] == -32602
    for bad_args in ([], "", 0, False):
        r = mcp.handle_message(_req("tools/call", name="memory_verify",
                                    arguments=bad_args), s)
        assert r["error"]["code"] == -32602               # falsy non-objects rejected


def test_non_string_method_and_invalid_id_shape(tmp_path):
    s = _state(tmp_path)
    r = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": 3}, s)
    assert r["error"]["code"] == -32600                   # method must be a string
    r = mcp.handle_message({"jsonrpc": "2.0", "id": {"bad": "shape"}, "method": "ping"}, s)
    assert r["id"] is None and r["error"]["code"] == -32600   # un-echoable id → null


def test_boolean_string_rejected_on_mutating_tool(tmp_path):
    s = _state(tmp_path)
    r = mcp.handle_message(_req("tools/call", name="memory_distill",
                                arguments={"dry": "false"}), s)
    assert r["error"]["code"] == -32602                   # 'false' must NOT coerce truthy
    r = mcp.handle_message(_req("tools/call", name="memory_verify",
                                arguments={"deep": "false"}), s)
    assert r["error"]["code"] == -32602


def test_unexpected_and_wrong_typed_args_rejected(tmp_path):
    s = _state(tmp_path)
    r = mcp.handle_message(_req("tools/call", name="memory_search",
                                arguments={"query": "x", "junk": 1}), s)
    assert r["error"]["code"] == -32602                   # additionalProperties:false
    r = mcp.handle_message(_req("tools/call", name="memory_search",
                                arguments={"query": 42}), s)
    assert r["error"]["code"] == -32602                   # query must be a string
