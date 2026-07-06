#!/usr/bin/env python3
"""
core.tuning — the compounding loop, v0. Measured local self-improvement.

`hsm tune` grid-searches k against YOUR golden-recall fixtures (`.hsm/fixtures.json`)
and writes the best to `.hsm/tuning.json`, which `ask` then uses. The metric is FIXTURE
recall (before vs after) — it optimizes for the fixtures you defined, so make them
representative; a larger k buys that recall by adding broader context. It can never
trade integrity for recall, because tuning only changes retrieval params, never your
notes (so `hsm verify` is unaffected and still gates the result).

No network, no model training — the 8090 harness→improve loop kept local-first, so the
improvement stays yours. v0 scope: it tunes k (retrieval breadth) against your fixtures.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import vault as vaultlib

_TUNING = "tuning.json"
_DEFAULT_KS = (3, 5, 8, 12, 16)


def _tuning_path(vault: Path) -> Path:
    return vault / ".hsm" / _TUNING


def load(vault: Path | str | None = None) -> dict:
    """The tuned params (empty dict if never tuned or unreadable)."""
    v = vaultlib._resolve(vault)
    p = _tuning_path(v)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def tuned_k(vault: Path | str | None = None, default: int = 5) -> int:
    """The tuned retrieval breadth, validated + clamped to [1, 100] so a hand-edited or
    corrupt tuning.json (k='x', null, -1, 1000000000) can never crash or poison ask()."""
    try:
        k = int(load(vault).get("k", default))
    except (TypeError, ValueError):
        return default
    return k if 1 <= k <= 100 else default


def _fixtures(vault: Path) -> list[dict]:
    for name in (".hsm", ".fbt"):   # legacy .fbt honored
        f = vault / name / "fixtures.json"
        if f.exists():
            try:
                cases = json.loads(f.read_text())
            except (OSError, ValueError):
                return []
            if not isinstance(cases, list):
                return []
            return [c for c in cases if c.get("query") and c.get("expect")]
    return []


def _recall_at(vault: Path, cases: list[dict], k: int) -> float:
    from . import index                       # lazy: avoid an index<->tuning import cycle
    if not cases:
        return 0.0
    hit = 0
    for c in cases:
        hits = index.search(c["query"], vault, k)
        exp = c["expect"]
        if any(exp == Path(h["rel"]).stem or exp in h["rel"] for h in hits):
            hit += 1
    return hit / len(cases)


def tune(vault: Path | str | None = None, ks=_DEFAULT_KS) -> dict:
    """Grid-search k over the fixtures; pick the SMALLEST k that reaches the best recall
    (best recall at the least context cost). Writes `.hsm/tuning.json`. Returns a report."""
    v = vaultlib._resolve(vault)
    cases = _fixtures(v)
    if not cases:
        return {"ok": False, "fixtures": 0,
                "reason": 'no fixtures — add .hsm/fixtures.json: [{"query","expect"}]'}
    ks = sorted(set(int(k) for k in ks if int(k) > 0))
    per_k = {k: _recall_at(v, cases, k) for k in ks}
    best = max(per_k.values())
    chosen = min(k for k, r in per_k.items() if r == best)
    before = per_k[5] if 5 in per_k else _recall_at(v, cases, 5)
    rec = {"k": chosen, "fixture_recall": round(best, 4), "at": date.today().isoformat()}
    try:
        p = _tuning_path(v)
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(rec), encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, "fixtures": len(cases),
            "per_k": {k: round(r, 4) for k, r in per_k.items()},
            "chosen_k": chosen, "recall_before": round(before, 4), "recall_after": round(best, 4)}
