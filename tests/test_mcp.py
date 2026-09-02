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
    # initialize is the LEGACY handshake, so it must answer a legacy revision.
    # PROTOCOL_VERSION is now the MODERN one and would be wrong here.
    assert r["result"]["protocolVersion"] == mcp.PREFERRED_LEGACY
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
                          "memory_remember", "memory_resolve", "memory_sign"}
    for t in tools.values():
        assert t["inputSchema"]["type"] == "object"
        assert t["inputSchema"]["additionalProperties"] is False
    assert "signer" in tools["memory_verify"]["inputSchema"]["properties"]
    assert "nextCursor" not in r["result"]


def test_verify_tool_flattens_findings(tmp_path):
    s = _state(tmp_path)
    (s.vault / "bad.md").write_text(
        "---\nname: bad\nstatus: hot\nmetadata:\n  status: done\n---\nx\n")
    r = mcp.handle_message(_req("tools/call", name="memory_verify", arguments={}), s)
    text = r["result"]["content"][0]["text"]
    assert "ROT DETECTED" in text and "self_contradiction" in text
    assert not r["result"].get("isError")


def test_verify_tool_passes_signer_pin(tmp_path, monkeypatch):
    s = _state(tmp_path)
    seen = {}

    def fake_verify_vault(vault, *, deep=False, expect_pubkey=None):
        seen["vault"] = vault
        seen["deep"] = deep
        seen["expect_pubkey"] = expect_pubkey
        return {
            "ok": True,
            "score": 100,
            "n_notes": 1,
            "fails": [],
            "warns": [],
        }

    monkeypatch.setattr(mcp.verify, "verify_vault", fake_verify_vault)
    r = mcp.handle_message(_req("tools/call", name="memory_verify",
                                arguments={"deep": True, "signer": "abc123"}), s)

    assert "MEMORY INTACT" in r["result"]["content"][0]["text"]
    assert seen == {"vault": s.vault, "deep": True, "expect_pubkey": "abc123"}


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
    assert by_id[0]["result"]["protocolVersion"] == mcp.PREFERRED_LEGACY
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


# ------------------------------------------------- dual-era support (2026-07-28)
# MCP has two eras. Legacy (2025-11-25 and earlier) opens with an `initialize`
# handshake and holds session state. Modern (2026-07-28+) is stateless: every request
# carries its version in `_meta`, there is no handshake, and `server/discover` is
# mandatory. This server answered ONLY 2024-11-05 until 0.4.1, so a modern client
# failed against it outright.

def _modern(method, mid=1, version=mcp.MODERN_VERSION, **extra):
    """A modern-era request: the protocol version rides in params._meta."""
    params = {"_meta": {"io.modelcontextprotocol/protocolVersion": version}}
    params.update(extra)
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}


def test_server_discover_answers_without_a_handshake(tmp_path):
    """`server/discover` is a MUST, and on stdio it is the probe that decides the era.

    It has to work on a server that has seen no `initialize`, because a modern client
    never sends one. Gating it behind the legacy lifecycle would make this server look
    legacy to every dual-era client.
    """
    s = _state(tmp_path, initialized=False)
    r = mcp.handle_message(_modern("server/discover"), s)
    res = r["result"]
    assert mcp.MODERN_VERSION in res["supportedVersions"]
    assert "tools" in res["capabilities"]
    assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "homestead-memory"


def test_modern_requests_need_no_initialize(tmp_path):
    """Modern is stateless. Telling a modern request "server not initialized" would be
    the legacy era leaking forward into a protocol that removed the handshake."""
    s = _state(tmp_path, initialized=False)
    r = mcp.handle_message(_modern("tools/list"), s)
    assert "error" not in r, r
    assert {t["name"] for t in r["result"]["tools"]}


def test_unsupported_version_returns_the_spec_error(tmp_path):
    """MUST be -32022 with the supported list, so the client can retry on a real version."""
    s = _state(tmp_path, initialized=False)
    r = mcp.handle_message(_modern("tools/list", version="1900-01-01"), s)
    err = r["error"]
    assert err["code"] == mcp.UNSUPPORTED_PROTOCOL_VERSION == -32022
    assert err["data"]["requested"] == "1900-01-01"
    assert mcp.MODERN_VERSION in err["data"]["supported"]


def test_legacy_initialize_echoes_a_version_we_speak(tmp_path):
    """The legacy rule: answer with the client's version when the server supports it."""
    s = _state(tmp_path, initialized=False)
    for asked in mcp.LEGACY_VERSIONS:
        r = mcp.handle_message(_req("initialize", protocolVersion=asked), s)
        assert r["result"]["protocolVersion"] == asked, asked


def test_legacy_initialize_falls_back_when_the_version_is_unknown(tmp_path):
    """An unknown version gets our preferred legacy revision, never the modern one:
    a client that sent `initialize` cannot speak modern by definition."""
    s = _state(tmp_path, initialized=False)
    r = mcp.handle_message(_req("initialize", protocolVersion="1900-01-01"), s)
    assert r["result"]["protocolVersion"] == mcp.PREFERRED_LEGACY
    assert r["result"]["protocolVersion"] != mcp.MODERN_VERSION


def test_legacy_client_still_works_end_to_end(tmp_path):
    """Regression: the old handshake path must keep working, gate included."""
    s = _state(tmp_path, initialized=False)
    mcp.handle_message(_req("initialize"), s)
    assert mcp.handle_message(_req("tools/list"), s)["error"]["code"] == -32002
    mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, s)
    assert "result" in mcp.handle_message(_req("tools/list"), s)


def test_spec_documents_exactly_the_tools_that_exist():
    """docs/MCP_SPEC.md said "Tools (6)" while the server shipped 9.

    Same defect class as the README claiming v0.2 at 0.4.0: a doc quietly describing an
    older product. This one is worse than cosmetic, because the spec is what an
    integrator reads before writing a client.
    """
    import re

    doc = (Path(__file__).resolve().parents[1] / "docs" / "MCP_SPEC.md").read_text()
    documented = set(re.findall(r"\| `(memory_\w+)`", doc))
    shipped = {t["name"] for t in mcp.TOOLS}
    assert documented == shipped, (
        f"MCP_SPEC.md and the server disagree. "
        f"In code but undocumented: {sorted(shipped - documented)}. "
        f"Documented but absent: {sorted(documented - shipped)}."
    )
    heading = re.search(r"^## Tools \((\d+)\)", doc, re.M)
    assert heading and int(heading.group(1)) == len(shipped), (
        f"the Tools heading says {heading.group(1) if heading else '?'} "
        f"but {len(shipped)} tools ship"
    )


def test_spec_does_not_advertise_a_version_the_server_refuses():
    """Every version the spec lists as supported must actually be accepted."""
    import re

    doc = (Path(__file__).resolve().parents[1] / "docs" / "MCP_SPEC.md").read_text()
    listed = set(re.findall(r"`(\d{4}-\d{2}-\d{2})`", doc))
    assert listed, "the spec no longer lists any protocol version"
    assert listed <= set(mcp.SUPPORTED_VERSIONS), (
        f"MCP_SPEC advertises {sorted(listed - set(mcp.SUPPORTED_VERSIONS))}, "
        f"which the server does not accept"
    )
