"""Import a Codex session into the ledger, from its rollout file.

WHY THIS IS AN IMPORT AND NOT A HOOK. Codex does expose PreToolUse and PostToolUse, but
measured on 2026-09-09 they did not fire at all under `codex exec`, and its own migration
reference states they run for shell commands only. Its rollout files, by contrast, record
every tool call with its input, its output and a `call_id` that pairs the two.

WHAT "SHELL COMMANDS ONLY" ACTUALLY MEANS HERE, because the first reading of it was wrong.
That caveat is written for people porting Claude Code hooks, where Read, Edit and Write are
separate tools and losing them is severe. Codex has no such tools. Measured across 60 real
sessions and 848 tool calls on this machine: `exec` accounts for 799 of them, or 94%, and
the rest are conversational (`send_message`, `request_user_input`). Codex does its file work
by shelling out, so shell coverage is near-total coverage FOR CODEX.

WHAT AN IMPORTED RECORD CANNOT CLAIM. A hook observes an action as it happens. This reads a
file the harness wrote afterwards, so it inherits whatever that file says and cannot prove
the record is contemporaneous. Every record therefore carries `meta.source`, and the ledger
stays honest about which of its rows were witnessed and which were imported.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import capture

ROLLOUT_GLOB = "sessions/*/*/*/rollout-*.jsonl"
TOOL_CALL_TYPES = ("custom_tool_call", "function_call", "local_shell_call")
TOOL_OUTPUT_TYPES = ("custom_tool_call_output", "function_call_output",
                     "local_shell_call_output")


def codex_home() -> Path:
    return Path.home() / ".codex"


def find_rollouts(home: Path | None = None) -> list[Path]:
    """Newest first. Empty list rather than an exception when Codex is not installed."""
    root = Path(home) if home else codex_home()
    if not root.exists():
        return []
    return sorted(root.glob(ROLLOUT_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)


def _read(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue          # a torn final line is normal on a live session
    return out


def _session_id(rows: list[dict], path: Path) -> str:
    for r in rows:
        if r.get("type") == "session_meta":
            p = r.get("payload") or {}
            for k in ("id", "session_id", "conversation_id"):
                if isinstance(p.get(k), str):
                    return p[k]
    # the filename carries the uuid: rollout-<iso>-<uuid>.jsonl
    stem = path.stem
    return stem.split("-", 2)[-1] if "-" in stem else stem


def records_from_rollout(path: Path | str) -> list[dict]:
    """Rollout file -> a list of ledger.append() kwargs, in order.

    A tool call becomes TWO records, matching how the Claude Code hook records both
    phases: the invocation (pre_execution) and its result (post_execution). They are
    paired on `call_id`, which Codex supplies, so an output whose call is missing is
    still recorded rather than silently dropped.
    """
    path = Path(path)
    rows = _read(path)
    session = _session_id(rows, path)
    calls: dict[str, str] = {}
    out: list[dict] = []

    for r in rows:
        if r.get("type") != "response_item":
            continue
        p = r.get("payload") or {}
        t = p.get("type")
        call_id = p.get("call_id") or p.get("id")

        if t in TOOL_CALL_TYPES:
            name = p.get("name") or "unknown"
            raw = p.get("input")
            if raw is None:
                raw = p.get("arguments")
            summary = capture.summarize(raw)
            if call_id:
                calls[call_id] = name
            out.append(dict(
                action="tool_call", target=name, phase="pre_execution",
                summary=summary["head"], session=session, agent="codex",
                meta={"source": "codex-rollout", "harness": "codex",
                      "call_id": call_id, "sha256": summary["sha256"],
                      "bytes": summary["bytes"],
                      **({"redacted": summary["redacted"]} if "redacted" in summary else {}),
                      **({"truncated": True} if summary.get("truncated") else {})}))

        elif t in TOOL_OUTPUT_TYPES:
            name = calls.get(call_id or "", "unknown")
            summary = capture.summarize(p.get("output"))
            out.append(dict(
                action="tool_result", target=name, phase="post_execution",
                summary=summary["head"], session=session, agent="codex",
                meta={"source": "codex-rollout", "harness": "codex",
                      "call_id": call_id, "sha256": summary["sha256"],
                      "bytes": summary["bytes"], "orphan": call_id not in calls,
                      **({"redacted": summary["redacted"]} if "redacted" in summary else {}),
                      **({"truncated": True} if summary.get("truncated") else {})}))
    return out
