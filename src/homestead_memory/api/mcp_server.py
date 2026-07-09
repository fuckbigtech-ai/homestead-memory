#!/usr/bin/env python3
"""
api.mcp_server — MCP (Model Context Protocol) server over stdio. Stdlib-only.

`hsm mcp [vault]` lets Claude Code / Claude Desktop / Cursor use homestead-memory
with one config line:

    claude mcp add homestead-memory -- hsm mcp ~/my-vault

Implements the MCP 2024-11-05 subset tool use requires (initialize / tools/list /
tools/call / ping) as newline-delimited JSON-RPC 2.0 on stdio. Protocol contract:
docs/MCP_SPEC.md (v1.1 addenda). Handlers call CORE functions only — CLI functions
print to stdout, which would corrupt the protocol stream; diagnostics go to stderr.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import __version__
from ..core import distill as distill_mod
from ..core import index, temporal, verify
from ..core import remember as remember_mod
from ..core import resolve as resolve_mod
from ..core import vault as vaultlib

PROTOCOL_VERSION = "2024-11-05"
MAX_TEXT = 50_000
_MAX_LINE = 10_000_000     # inbound line cap — a huge line becomes -32700, not an OOM
_REJECT_NAN = lambda s: (_ for _ in ()).throw(ValueError(f"non-finite: {s}"))  # noqa: E731
_K_MIN, _K_MAX, _K_DEFAULT = 1, 20, 5


# ------------------------------------------------------------------ tool defs
def _k_schema() -> dict:
    return {"type": "integer", "minimum": _K_MIN, "maximum": _K_MAX,
            "default": _K_DEFAULT, "description": "number of passages"}


TOOLS = [
    {"name": "memory_ask",
     "description": "Ask the memory a question: retrieve the most relevant notes "
                    "(and synthesize an answer if a reader is configured via HSM_READER).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["query"],
                     "properties": {"query": {"type": "string"}, "k": _k_schema(),
                                    "type": {"type": "string",
                                             "enum": ["temporal-reasoning", "knowledge-update",
                                                      "multi-session", "default"],
                                             "description": "question type (default: auto-classified)"}}}},
    {"name": "memory_search",
     "description": "Search the memory: ranked passages (title, path, score, snippet).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["query"],
                     "properties": {"query": {"type": "string"}, "k": _k_schema()}}},
    {"name": "memory_verify",
     "description": "Run the memory-integrity gate (RotBench): score /100 plus every "
                    "failure/warning. Read-only.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": [],
                     "properties": {"deep": {"type": "boolean", "default": False,
                                             "description": "also run retrieval-resilience/"
                                                            "fixtures/freshness checks"}}}},
    {"name": "memory_history",
     "description": "A note's recorded change history from its Changelog (temporal layer).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["note"],
                     "properties": {"note": {"type": "string",
                                             "description": "note stem or relative path"},
                                    "as_of": {"type": "string",
                                              "description": "YYYY-MM-DD: what was recorded "
                                                             "on/before this date"}}}},
    {"name": "memory_ingest",
     "description": "MUTATES local state: (re)build the search index (qmd) and the "
                    "temporal sidecar for the vault. Can take a while on large vaults.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": [], "properties": {}}},
    {"name": "memory_distill",
     "description": "MUTATES local state unless dry=true: run the write-time distilled "
                    "layer (extract cited entity facts from new/changed notes).",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": [],
                     "properties": {"dry": {"type": "boolean", "default": False},
                                    "agent": {"type": "string",
                                              "description": "writer identity for provenance"}}}},
    {"name": "memory_remember",
     "description": "MUTATES local state: directly write one provenance-stamped "
                    "distilled fact.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["entity", "field", "value"],
                     "properties": {"entity": {"type": "string"},
                                    "field": {"type": "string"},
                                    "value": {"type": "string"},
                                    "source": {"type": "string"},
                                    "agent": {"type": "string",
                                              "description": "writer identity for provenance"}}}},
    {"name": "memory_resolve",
     "description": "MUTATES local state: resolve duplicate-value conflicts in one "
                    "distilled note, preserving the historical changelog.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["entity"],
                     "properties": {"entity": {"type": "string"},
                                    "field": {"type": "string"},
                                    "strategy": {"type": "string",
                                                 "enum": ["latest", "keep-both"],
                                                 "description": "default: latest"},
                                    "agent": {"type": "string",
                                              "description": "resolver identity for provenance"}}}},
]


# ------------------------------------------------------------- tool execution
_TYPE_MAP = {"string": str, "integer": int, "boolean": bool}


def _validate_args(tool: dict, args: dict) -> str | None:
    """Enforce the advertised inputSchema: required keys, per-key types (bools must
    be REAL bools — 'false' must not silently enable a mutating tool), and
    additionalProperties:false. Returns an error message or None."""
    schema = tool["inputSchema"]
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args:
            return f"missing required argument: {req}"
    for key, val in args.items():
        if key not in props:
            return f"unexpected argument: {key}"
        want = _TYPE_MAP.get(props[key].get("type"))
        if want is bool:
            if not isinstance(val, bool):
                return f"argument {key} must be a boolean"
        elif want is int:
            if isinstance(val, bool) or not isinstance(val, int):
                return f"argument {key} must be an integer"
        elif want is str and not isinstance(val, str):
            return f"argument {key} must be a string"
    return None


def _clamp_k(args: dict) -> int:
    try:
        k = int(args.get("k", _K_DEFAULT))
    except (TypeError, ValueError):
        k = _K_DEFAULT
    return max(_K_MIN, min(_K_MAX, k))


def _text_result(text: str) -> dict:
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n…[truncated]"
    return {"content": [{"type": "text", "text": text}]}


def _error_result(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _fmt_hits(hits: list[dict]) -> str:
    if not hits:
        return "no matches."
    return "\n".join(f"[{h.get('score')}] {h.get('title')} ({h.get('rel')})\n"
                     f"  {str(h.get('snippet', '')).strip()[:400]}" for h in hits)


def call_tool(name: str, args: dict, vault: Path) -> dict:
    """Execute one tool against core functions. Exceptions become isError results
    in the caller — this function may raise."""
    if name == "memory_ask":
        qt = args.get("type")
        res = index.ask(str(args["query"]), vault, k=_clamp_k(args),
                        question_type=str(qt) if qt else None)
        if res["answer"]:
            return _text_result(f"{res['answer']}\n\n— sources ({res['engine']} · "
                                f"{res['question_type']}):\n" + _fmt_hits(res["hits"]))
        return _text_result(f"top passages ({res['engine']}):\n" + _fmt_hits(res["hits"]))
    if name == "memory_search":
        return _text_result(_fmt_hits(index.search(str(args["query"]), vault, k=_clamp_k(args))))
    if name == "memory_verify":
        rep = verify.verify_vault(vault, deep=bool(args.get("deep", False)))
        stamp = "MEMORY INTACT" if rep["ok"] else "ROT DETECTED"
        lines = [f"{stamp} — {rep['score']}/100 ({rep['n_notes']} notes, "
                 f"{len(rep['fails'])} fail / {len(rep['warns'])} warn)"]
        lines += [f"  {f.level.upper()} [{f.check}] {f.note}: {f.detail}"
                  for f in rep["fails"] + rep["warns"]]
        return _text_result("\n".join(lines))
    if name == "memory_history":
        note = str(args["note"])
        as_of = args.get("as_of")
        rows = (temporal.as_of(note, str(as_of), vault=vault) if as_of
                else temporal.history(note, vault=vault))
        if not rows:
            return _text_result(f"no recorded history for '{note}' "
                                f"(run memory_ingest to build the temporal sidecar).")
        return _text_result("\n".join(
            f"{r['valid_date']}"
            + (f" [{r['field']}: {r['old_val']} → {r['new_val']}]" if r["field"] else "")
            + f" {r['text']}" for r in rows))
    if name == "memory_ingest":
        ing = index.ingest(vault)
        t = temporal.build(vault)
        return _text_result(f"index: {ing}\ntemporal: {t['entries']} dated changes "
                            f"across {t['notes_with_history']} notes")
    if name == "memory_distill":
        agent = args.get("agent")
        rep = distill_mod.distill(vault, dry=bool(args.get("dry", False)),
                                  agent=str(agent) if agent is not None else None)
        return _text_result(json.dumps(rep, indent=1))
    if name == "memory_remember":
        rep = remember_mod.remember(
            str(args["entity"]), str(args["field"]), str(args["value"]),
            vault=vault,
            source=str(args["source"]) if args.get("source") is not None else None,
            agent=str(args["agent"]) if args.get("agent") is not None else None,
        )
        return _text_result(json.dumps(rep, indent=1))
    if name == "memory_resolve":
        strategy = str(args.get("strategy", "latest"))
        if strategy not in {"latest", "keep-both"}:
            return _error_result("strategy must be latest or keep-both")
        rep = resolve_mod.resolve(
            str(args["entity"]),
            vault=vault,
            field=str(args["field"]) if args.get("field") is not None else None,
            strategy=strategy,
            agent=str(args["agent"]) if args.get("agent") is not None else None,
        )
        return _text_result(json.dumps(rep, indent=1))
    raise KeyError(name)


# --------------------------------------------------------------- JSON-RPC core
def _resp(mid, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def _err(code: int, message: str) -> dict:
    return {"code": code, "message": message}


class ServerState:
    def __init__(self, vault: Path):
        self.vault = vault
        self.initialize_seen = False
        self.initialized = False


def handle_message(msg, state: ServerState):
    """Process ONE decoded JSON-RPC message. Returns a response dict, or None for
    notifications / undecodable structures without an id (per spec: no response)."""
    if not isinstance(msg, dict):
        return None
    has_id, mid = "id" in msg, msg.get("id")
    # id must be string/number/null — an invalid id shape is structurally invalid
    # and cannot be echoed back: respond -32600 with id null.
    if has_id and not isinstance(mid, (str, int, float, type(None))):
        return _resp(None, error=_err(-32600, "invalid id"))
    method = msg.get("method")
    if msg.get("jsonrpc") != "2.0" or not isinstance(method, str):
        if has_id:
            return _resp(mid, error=_err(-32600, "invalid request"))
        return None

    if not has_id:                       # notification — NEVER respond
        # only a REAL initialize handshake unlocks the server (lifecycle gating)
        if method == "notifications/initialized" and state.initialize_seen:
            state.initialized = True
        return None                      # unknown notifications (incl. cancelled): swallow

    if method == "initialize":
        state.initialize_seen = True
        return _resp(mid, result={
            "protocolVersion": PROTOCOL_VERSION,   # we answer with OUR version
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "homestead-memory", "version": __version__}})
    if method == "ping":
        return _resp(mid, result={})
    if not state.initialized:
        return _resp(mid, error=_err(-32002, "server not initialized"))

    if method == "tools/list":           # cursor ignored; nextCursor omitted
        return _resp(mid, result={"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _resp(mid, error=_err(-32602, "params must be an object"))
        name = params.get("name")
        args = params.get("arguments")
        if args is None:                 # absent/null defaults to {}; any other
            args = {}                    # non-object shape is an error (no `or {}`)
        if not isinstance(args, dict):
            return _resp(mid, error=_err(-32602, "arguments must be an object"))
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            return _resp(mid, error=_err(-32602, f"unknown tool: {name}"))
        bad = _validate_args(tool, args)
        if bad:
            return _resp(mid, error=_err(-32602, bad))
        try:
            return _resp(mid, result=call_tool(name, args, state.vault))
        except Exception as e:           # a tool failure must never kill the loop
            return _resp(mid, result=_error_result(f"{type(e).__name__}: {e}"))
    return _resp(mid, error=_err(-32601, f"method not found: {method}"))


def serve(vault: Path | str | None = None) -> int:
    """The stdio loop: newline-delimited JSON, flush every write, stdout protocol-only."""
    v = vaultlib._resolve(vault)
    if not v.is_dir():
        print(f"hsm mcp: vault is not a directory: {v}", file=sys.stderr)
        return 2
    state = ServerState(v)
    print(f"homestead-memory MCP server on stdio (vault: {v})", file=sys.stderr)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if len(line) > _MAX_LINE:
                print(f"hsm mcp: dropping oversized line ({len(line)} bytes)", file=sys.stderr)
                out = _resp(None, error=_err(-32700, "parse error: line too large"))
            else:
                try:
                    msg = json.loads(line, parse_constant=_REJECT_NAN)  # strict: no NaN/Inf
                except ValueError:
                    out = _resp(None, error=_err(-32700, "parse error"))
                else:
                    out = handle_message(msg, state)
            if out is not None:
                sys.stdout.write(json.dumps(out, allow_nan=False) + "\n")
                sys.stdout.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        pass                              # client went away — quiet exit
    return 0
