#!/usr/bin/env python3
"""capture.py — turn an agent tool-call event into a record safe to keep forever.

WHY REDACTION IS THE HARD PART
------------------------------
A `PostToolUse` payload carries `tool_response`: the whole file for a Read, the whole
output for a Bash command. Recording it verbatim would build a credential harvester and
then invite the user to hand the file to an auditor.

Three defences, in order of how much they are trusted:

  truncate   FIRST and always. A 200-char head cannot contain a 400-char private key
             no matter what the pattern list knows. This is the defence that does not
             depend on being clever.
  redact     pattern-match known credential shapes. Useful, and NOT trusted alone:
             measured 2026-07-31 on one real machine, an earlier version of this
             pattern set caught **1 of 8** actual credential files. It matched
             provider prefixes (sk-, ghp_, AKIA) and sailed past .env assignments,
             OAuth token JSON, single-quoted TOML and a raw ed25519 key array. The
             list below is the hardened version that scores 8/8 on those same files.
             It will still miss shapes nobody has thought of.
  hash       SHA-256 of the FULL original, plus its byte length. The evidence value
             survives redaction: you can prove what the output was without storing it.
             An auditor with the original can verify; nobody else can reconstruct it.

So the honest claim is "secret-shaped values are redacted and payloads are truncated",
never "secrets cannot leak". The difference matters when someone bets a compliance
programme on it.
"""
from __future__ import annotations

import hashlib
import json
import re

from .ledger import PHASE_POST, PHASE_PRE

# Head kept from any payload. Long enough to be useful when debugging ("which command
# ran?", "what did it print?"), short enough that a leaked credential is a fragment
# rather than a key.
HEAD_CHARS = 200

REDACTED = "<redacted>"

# Ported from the machine-local guard hardened 2026-07-31 (1/8 -> 8/8 on real
# credential files). Kept as separate compiled patterns rather than one giant
# alternation so a failure is attributable to a specific rule and so each can be
# tested in isolation.
_SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    # Provider-prefixed literals. Narrow, high confidence, no false positives.
    re.compile(r"sk-ant-[A-Za-z0-9_-]{32,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{20,}"),
    # The whole PEM block, not just its header. Matching only the BEGIN line leaves the
    # base64 key material on the following lines, which is the part that matters --
    # caught in testing, where the header vanished and `MIIEow...` sailed through.
    # The END marker is optional because truncation may have already cut it off.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r"(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]*)"
    ),
    # NAME=value assignments, either case. This is the family the original pattern set
    # missed entirely, and it is how API keys actually appear in shell files.
    re.compile(
        r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)\s*[:=]\s*)"
        r"['\"]?[A-Za-z0-9_./*+-]{16,}"
    ),
    # OAuth material in JSON (gcloud ADC, refresh tokens, service accounts).
    re.compile(
        r"(\"(?:refresh_token|access_token|client_secret|private_key)\"\s*:\s*)"
        r"\"[^\"]{16,}\""
    ),
    # Raw ed25519/secp256k1 keypair as a JSON byte array (Solana id.json shape).
    re.compile(r"\[\s*(?:\d{1,3}\s*,\s*){31,}\d{1,3}\s*\]"),
)


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, count). Preserves the assignment's NAME so the record
    still says *which* credential appeared, which is exactly the debugging signal you
    want, while dropping the value."""
    if not text:
        return text or "", 0
    total = 0
    out = text
    for pat in _SECRET_PATTERNS:
        # Patterns with a capture group keep the prefix (NAME= / "key":) and drop only
        # the value. Patterns without one are bare literals and go entirely.
        repl = (r"\1" + REDACTED) if pat.groups else REDACTED
        out, n = pat.subn(repl, out)
        total += n
    return out, total


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def summarize(value, head_chars: int = HEAD_CHARS) -> dict:
    """Reduce an arbitrary payload to something safe and still useful.

    Order matters: hash the ORIGINAL (so the digest is of what really happened, not of
    our redaction), then truncate, then redact the surviving head. Redacting before
    hashing would make the digest unverifiable against the real output, destroying the
    evidence value the hash exists to provide.
    """
    text = _as_text(value)
    raw = text.encode("utf-8", errors="replace")
    head = text[:head_chars]
    head, n = redact(head)
    out = {
        "head": head,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    if len(text) > head_chars:
        out["truncated"] = True
    if n:
        out["redacted"] = n
    return out


# Where the interesting identifier lives for each tool. Falls back to a generic scan,
# so a tool we have never heard of still gets a useful target instead of null.
_TARGET_KEYS = ("file_path", "path", "command", "url", "pattern", "notebook_path", "query")


def target_of(tool_name: str, tool_input) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for k in _TARGET_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            # A command IS the target for Bash, but it is also the field most likely to
            # carry a secret inline (`export X=... && deploy`). Redact and clip it.
            clipped, _ = redact(v.strip()[:HEAD_CHARS])
            return clipped
    return None


def from_hook_payload(payload: dict) -> dict:
    """Map a Claude Code PostToolUse payload to ledger.append() kwargs.

    Field names are taken defensively in both snake_case and camelCase. The machine
    this was written on has a hook that silently did nothing for weeks because it read
    an input shape the harness had stopped sending; assuming one spelling is how that
    happens.
    """
    def pick(*names):
        for n in names:
            if n in payload and payload[n] is not None:
                return payload[n]
        return None

    tool_name = pick("tool_name", "toolName") or "unknown"
    tool_input = pick("tool_input", "toolInput")
    tool_response = pick("tool_response", "toolResponse")

    # Which side of the action are we on? The harness says so when it can, and the
    # shape says so when it cannot: a PreToolUse payload has no tool_response because
    # the tool has not run. Both are checked rather than trusting one, for the same
    # reason the field names are read defensively above.
    event = (pick("hook_event_name", "hookEventName") or "").lower()
    if "pre" in event:
        phase = PHASE_PRE
    elif "post" in event:
        phase = PHASE_POST
    else:
        phase = PHASE_POST if tool_response is not None else PHASE_PRE

    meta = {
        "tool_use_id": pick("tool_use_id", "toolUseId"),
        "cwd": pick("cwd"),
        "permission_mode": pick("permission_mode", "permissionMode"),
        "input": summarize(tool_input),
        "response": summarize(tool_response),
    }
    return {
        "action": "tool_call",
        "target": tool_name,
        "summary": target_of(tool_name, tool_input),
        "meta": {k: v for k, v in meta.items() if v is not None},
        "session": pick("session_id", "sessionId"),
        "phase": phase,
    }
