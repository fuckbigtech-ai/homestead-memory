#!/usr/bin/env python3
"""Write-provenance helpers for the distilled layer."""
from __future__ import annotations

import os
import re
import secrets
import socket
from datetime import datetime, timezone

_SESSION_ID: str | None = None

PROV_RE = re.compile(
    r"\[agent=(?P<agent>[^\s\]]+)\s+session=(?P<session>[^\s\]]+)\s+ts=(?P<ts>[^\s\]]+)\]\s*$"
)


def _sanitize(value: str | None, fallback: str = "unknown") -> str:
    v = re.sub(r"\s+", "_", str(value or "").strip()).replace("]", "")
    return v or fallback


def resolve_agent(explicit: str | None = None) -> str:
    """Resolve and sanitize the writer identity."""
    if explicit is not None:
        return _sanitize(explicit)
    env = os.environ.get("HSM_AGENT")
    if env is not None:
        return _sanitize(env)
    try:
        return _sanitize(socket.gethostname())
    except Exception:
        return "unknown"


def resolve_session(explicit: str | None = None) -> str:
    """Resolve and sanitize the write session id, stable for this process."""
    global _SESSION_ID
    if explicit is not None:
        return _sanitize(explicit)
    env = os.environ.get("HSM_SESSION")
    if env is not None:
        return _sanitize(env)
    if _SESSION_ID is None:
        _SESSION_ID = secrets.token_hex(4)
    return _SESSION_ID


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_token(agent: str, session: str, ts: str) -> str:
    return f"[agent={agent} session={session} ts={ts}]"


def parse_token(text: str) -> dict | None:
    m = PROV_RE.search(text or "")
    return m.groupdict() if m else None
