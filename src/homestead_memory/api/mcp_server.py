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
from ..core import vault as vaultlib

PROTOCOL_VERSION = "2024-11-05"
MAX_TEXT = 50_000
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
                     "properties": {"query": {"type": "string"}, "k": _k_schema()}}},
    {"name": "memory_search",
     "description": "Search the memory: ranked passages (title, path, score, snippet).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["query"],
                     "properties": {"query": {"type": "string"}, "k": _k_schema()}}},
    {"name": "memory_verify",
     "description": "Run the memory-integrity gate (RotBench): score /100 plus every "
                    "failure/warning. Read-only.",
     "inputSchema": {"type": "object", "additionalProperties": False,
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
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "memory_distill",
     "description": "MUTATES local state unless dry=true: run the write-time distilled "
                    "layer (extract cited entity facts from new/changed notes).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"dry": {"type": "boolean", "default": False}}}},
]


# ------------------------------------------------------------- tool execution
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
        res = index.ask(str(args["query"]), vault, k=_clamp_k(args))
        if res["answer"]:
            return _text_result(f"{res['answer']}\n\n— sources ({res['engine']}):\n"
                                + _fmt_hits(res["hits"]))
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
        rep = distill_mod.distill(vault, dry=bool(args.get("dry", False)))
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
        self.initialized = False


def handle_message(msg, state: ServerState):
    """Process ONE decoded JSON-RPC message. Returns a response dict, or None for
    notifications / undecodable structures without an id (per spec: no response)."""
    if not isinstance(msg, dict) or "method" not in msg or msg.get("jsonrpc") != "2.0":
        if isinstance(msg, dict) and "id" in msg:
            return _resp(msg.get("id"), error=_err(-32600, "invalid request"))
        return None
    method, has_id, mid = msg["method"], "id" in msg, msg.get("id")

    if not has_id:                       # notification — NEVER respond
        if method == "notifications/initialized":
            state.initialized = True
        return None                      # unknown notifications (incl. cancelled): swallow

    if method == "initialize":
        return _resp(mid, result={
            "protocolVersion": PROTOCOL_VERSION,   # we answer with OUR version
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "homestead-memory", "version": __version__}})
    if method == "ping":
        return _resp(mid, result={})
    if not state.initialized:
        return _resp(mid, error=_err(-32002, "server not initialized"))

    if method == "tools/list":           # cursor ignored; nextCursor omitted (6 tools)
        return _resp(mid, result={"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not any(t["name"] == name for t in TOOLS):
            return _resp(mid, error=_err(-32602, f"unknown tool: {name}"))
        if not isinstance(args, dict):
            return _resp(mid, error=_err(-32602, "arguments must be an object"))
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
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                out = _resp(None, error=_err(-32700, "parse error"))
            else:
                out = handle_message(msg, state)
            if out is not None:
                sys.stdout.write(json.dumps(out) + "\n")
                sys.stdout.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        pass                              # client went away — quiet exit
    return 0
