#!/usr/bin/env python3
"""
core.telemetry — a local, opt-in usage log.

The frontier-lab move is "your usage telemetry post-trains OUR model." This is the
local-first counterpart: an append-only `.hsm/telemetry.jsonl` recording how retrieval
performed. homestead-memory NEVER sends it anywhere — it's a plain-JSON file you can
read and delete. It is OFF by default (set `HSM_TELEMETRY=1`) and lives inside `.hsm/`
(excluded from indexing + the content hash). To keep query text off disk entirely, the
`ask` logger stores a query HASH, not the raw query.

Honest scope: `hsm tune` (v0) optimizes against your fixtures, not this log — the log
is for transparency and future analysis. No network, no model training.

If you sync your vault with git, gitignore `.hsm/` (it's a machine-local sidecar).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_FILE = "telemetry.jsonl"


def enabled() -> bool:
    """Telemetry is strictly opt-in (privacy by default). Case-insensitive off-values."""
    return os.environ.get("HSM_TELEMETRY", "").strip().lower() not in ("", "0", "false", "no", "off")


def _path(vault: Path) -> Path:
    return vault / ".hsm" / _FILE


def log(vault: Path, event: dict) -> None:
    """Append one event to the local log. Silent no-op when disabled or on any IO error
    — telemetry must never break a query."""
    if not enabled():
        return
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        p = _path(vault)
        p.parent.mkdir(exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def events(vault: Path) -> list[dict]:
    """Read the local log (best-effort; skips any corrupt line)."""
    p = _path(vault)
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out
