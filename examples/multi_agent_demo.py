#!/usr/bin/env python3
"""Narrated multi-agent conflict demo for homestead-memory."""
from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path

from homestead_memory.core import provenance, remember, resolve, verify

_RECORDED_RE = re.compile(r'recorded\s+city:\s*"([^"]*)"')
_UPDATE_RE = re.compile(r'update\s+city:\s*"[^"]*"\s*->\s*"([^"]*)"')


def _force_merged_conflict(note: Path) -> None:
    """Simulate two agents' copies merging by keeping both current city bullets.

    The changelog lines were produced by the public remember API; this small
    explicit merge mimics a sync layer preserving both agents' current bullets.
    """
    text = note.read_text(encoding="utf-8")
    conflict_bullet = "- city: Berlin (source: remember)"
    if conflict_bullet in text:
        return
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and line.startswith("- city: Tokyo "):
            out.append(conflict_bullet)
            inserted = True
        out.append(line)
    note.write_text("\n".join(out) + "\n", encoding="utf-8")


def _city_attribution(note: Path) -> list[str]:
    out: list[str] = []
    for line in note.read_text(encoding="utf-8").splitlines():
        value = None
        m = _RECORDED_RE.search(line)
        if m:
            value = m.group(1)
        m = _UPDATE_RE.search(line)
        if m:
            value = m.group(1)
        token = provenance.parse_token(line)
        if value and token:
            out.append(f"city={value} agent={token['agent']} ts={token['ts']}")
    return out


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="hsm-multi-agent-") as d:
        vault = Path(d)
        user = "User"

        print("1. INTACT: two agents write through the public API.")
        remember.remember(user, "city", "Berlin", vault=vault, agent="claude", session="demo-claude")
        time.sleep(1.05)
        remember.remember(user, "city", "Tokyo", vault=vault, agent="codex", session="demo-codex")
        verify.print_report(verify.verify_vault(vault), quiet=True)

        print("\n2. ROT: simulate a sync merge that preserved both agents' city bullets.")
        note = vault / "distilled" / "user.md"
        _force_merged_conflict(note)
        rotted = verify.verify_vault(vault)
        verify.print_report(rotted)
        print("   attribution:")
        for line in _city_attribution(note):
            print(f"   - {line}")

        print("\n3. RESOLVE: latest provenance timestamp wins, loser stays in history.")
        res = resolve.resolve(user, vault=vault, field="city", strategy="latest",
                              agent="resolver", session="demo-resolver")
        for item in res["resolved"]:
            losers = ", ".join(item["losers"]) if item["losers"] else "(none)"
            print(f"   {item['field']}: kept {item['winner']} over {losers}")

        print("\n4. INTACT: verify after resolution.")
        final = verify.verify_vault(vault)
        verify.print_report(final, quiet=True)

        print("\nN agents, one verified memory: conflicts are caught with attribution "
              "and resolved by timestamp, never silently.")
        return 0 if (not rotted["ok"] and final["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(run())
