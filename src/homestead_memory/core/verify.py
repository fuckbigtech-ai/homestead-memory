#!/usr/bin/env python3
"""
core.verify — the memory-integrity gate. The whole point of homestead-memory.

Every other memory layer asks you to *hope* it remembers. `hsm verify` scores
whether your memory can still prove it surfaces the *current* truth over stale,
contradictory, or dangling copies — and exits non-zero when it can't.

This is a first, honest scoring model (v0.0.1). It runs real checks over a
markdown vault:

  - frontmatter integrity  (a note that won't parse is unrecoverable memory)
  - self-contradiction     (flat vs nested `status:` disagree — the note argues
                            with itself; the classic silent-rot signal)
  - link integrity         (a [[wikilink]] to a note that no longer exists — a
                            memory pointing at something that's gone)
  - stale body             (the body's own `## Changelog` has moved on well past
                            the note's `updated:` date — the record drifted)

It will grow toward the full weighted degradation-test (freshness, fallback
resilience, contradiction-recall). But even this catches real rot today — which
is the demo: `hsm verify --demo`.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import vault as vaultlib

_CHANGELOG_DATE_RE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\b", re.M)
_STALE_BODY_DAYS = 14  # body's changelog this far past `updated:` = drifted record


@dataclass
class Finding:
    level: str   # "fail" | "warn"
    check: str
    note: str
    detail: str


def _latest_changelog_date(text: str):
    dates = _CHANGELOG_DATE_RE.findall(text)
    best = None
    for d in dates:
        try:
            dt = date.fromisoformat(d)
        except ValueError:
            continue
        if best is None or dt > best:
            best = dt
    return best


def _stale_body(text: str, updated: str | None) -> str | None:
    if not updated:
        return None
    try:
        upd = date.fromisoformat(updated.strip())
    except ValueError:
        return None
    cl = _latest_changelog_date(text)
    if cl and (cl - upd).days > _STALE_BODY_DAYS:
        return f"body drifted: latest changelog {cl} is {(cl - upd).days}d past updated:{upd}"
    return None


def deep_checks(vault: Path | str | None = None) -> list[Finding]:
    """Extra 'full' checks (hsm verify --deep):
      1. fallback resilience — retrieval must survive the index being down (the
         direct-scan must still find a known term). "Your memory works even when
         the fancy index is gone" is the whole ownership pitch.
      2. fixtures.json — user-defined golden recall (<vault>/.hsm/fixtures.json:
         [{"query","expect"}]) — a regression suite for "this must stay findable".
      3. qmd freshness — WARN if qmd is available but the vault was never ingested.
    """
    from . import index
    v = vaultlib._resolve(vault)
    out: list[Finding] = []
    notes = list(vaultlib.iter_notes(v))

    if notes:
        txt0 = notes[0][0].read_text(errors="replace").lower()
        words = [w for w in index._WORD.findall(txt0) if len(w) >= 6]
        probe = words[0] if words else notes[0][1].stem
        if not index._direct_scan(probe, v, 3):
            out.append(Finding("fail", "fallback_resilience", "(retrieval)",
                               f"direct-scan found nothing for a known term {probe!r} — "
                               f"memory would not survive qmd being down"))

    fx = v / ".hsm" / "fixtures.json"
    if not fx.exists():
        fx = v / ".fbt" / "fixtures.json"   # legacy location
    if fx.exists():
        try:
            cases = json.loads(fx.read_text())
        except Exception:
            cases = []
            out.append(Finding("warn", "fixtures", "fixtures.json", "unparseable"))
        for c in cases or []:
            query, expect = c.get("query", ""), c.get("expect", "")
            if not query or not expect:
                continue
            hits = index.search(query, v, 8)
            if not any(expect == Path(h["rel"]).stem or expect in h["rel"] for h in hits):
                out.append(Finding("fail", "fixture_miss", expect,
                                   f"query {query!r} did not retrieve the expected note"))

    if index.qmd_available() and notes and not index._collection_exists(index.collection_name(v)):
        out.append(Finding("warn", "not_indexed", "(retrieval)",
                           "qmd available but vault not ingested — run `hsm ingest` for hybrid retrieval"))
    return out


_SOURCE_CITE_RE = re.compile(r"\(source:\s*([^)]+)\)")


def _check_distilled(txt: str, rp: str, vroot: Path) -> list[Finding]:
    """distill_integrity: (a) uncited claim — a body bullet with no `(source: …)`;
    (b) dangling citation — a cited path that doesn't resolve (path-based, per spec)."""
    out: list[Finding] = []
    body = txt.split("---", 2)[-1]
    in_changelog = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            in_changelog = "changelog" in s.casefold()
            continue
        if not s.startswith("- "):
            continue
        cites = _SOURCE_CITE_RE.findall(s)
        if not cites and not in_changelog:
            out.append(Finding("fail", "uncited_claim", rp,
                               f"distilled claim has no (source: …): {s[:70]!r}"))
        for c in cites:
            c = c.strip()
            # citation must be a RELATIVE .md path that resolves INSIDE the vault —
            # absolute paths and ../ traversal are rot even if the file exists.
            if Path(c).is_absolute() or not c.endswith(".md"):
                out.append(Finding("fail", "dangling_citation", rp,
                                   f"(source: {c}) is not a relative .md path"))
                continue
            cand = (vroot / c)
            try:
                inside = cand.resolve().is_relative_to(vroot.resolve())
            except (OSError, ValueError):
                inside = False
            if not (inside and cand.exists()):
                out.append(Finding("fail", "dangling_citation", rp,
                                   f"(source: {c}) does not resolve inside the vault"))
    return out


def verify_vault(vault: Path | str | None = None, deep: bool = False) -> dict:
    """Run the integrity checks over a vault. Returns a report dict. deep=True adds
    the retrieval-resilience + fixtures + freshness families."""
    targets = vaultlib.valid_link_targets(vault)
    findings: list[Finding] = []
    notes = list(vaultlib.iter_notes(vault))

    for p, rel in notes:
        rp = rel.as_posix()
        txt = p.read_text(errors="replace")
        fm = vaultlib.parse_frontmatter(txt)

        if fm is None:
            findings.append(Finding("fail", "frontmatter", rp,
                                    "no frontmatter block — unparseable memory"))
            continue

        f = fm["fields"]
        if not f.get("name"):
            findings.append(Finding("warn", "required_field", rp, "missing name:"))
        st = f.get("status")
        if st is not None and st not in vaultlib.VALID_STATUS:
            findings.append(Finding("warn", "bad_status", rp, f"status '{st}' not in enum"))
        if fm["status_conflict"]:
            findings.append(Finding(
                "fail", "self_contradiction", rp,
                f"status contradicts itself: flat='{fm['flat'].get('status')}' "
                f"vs nested='{fm['nested'].get('status')}'"))

        for target, _is_embed in vaultlib.iter_wikilinks(txt):
            if vaultlib.is_pathlike_target(target):
                continue
            if target not in targets:
                findings.append(Finding("warn", "broken_link", rp,
                                        f"[[{target}]] → no such note (dangling memory)"))

        stale = _stale_body(txt, f.get("updated"))
        if stale:
            findings.append(Finding("warn", "stale_body", rp, stale))

        # distill_integrity — the auditable-extraction family (RotBench extension).
        # Every claim in a distilled note must carry a citation that resolves.
        if f.get("type") == "distilled" or rp.startswith("distilled/"):
            findings += _check_distilled(txt, rp, vaultlib._resolve(vault))

    if deep:
        findings += deep_checks(vault)

    fails = [x for x in findings if x.level == "fail"]
    warns = [x for x in findings if x.level == "warn"]
    # Scale-invariant score = % of notes with intact integrity, lightly eroded by
    # the warn rate. A vault with any integrity FAIL is stamped "ROT DETECTED"
    # regardless of score (one contradicting note is still rot).
    n = len(notes) or 1
    unhealthy = {x.note for x in fails}
    clean_frac = 1 - len(unhealthy) / n
    warn_pen = min(15, round(100 * (len(warns) / n) * 0.3))
    score = max(0, round(100 * clean_frac) - warn_pen)
    ok = (not fails) and score >= 85
    return {
        "vault": str(vaultlib._resolve(vault)),
        "n_notes": len(notes),
        "score": score,
        "ok": ok,
        "findings": findings,
        "fails": fails,
        "warns": warns,
    }


def print_report(rep: dict, *, quiet: bool = False) -> None:
    stamp = "MEMORY INTACT" if rep["ok"] else "ROT DETECTED"
    mark = "✅" if rep["ok"] else "🔴"
    print(f"{mark}  {stamp}  —  {rep['score']}/100   "
          f"({rep['n_notes']} notes, {len(rep['fails'])} fail / {len(rep['warns'])} warn)")
    if quiet:
        return
    shown = rep["fails"] + rep["warns"]
    for fd in shown[:40]:
        icon = "🔴" if fd.level == "fail" else "⚠️ "
        print(f"   {icon} [{fd.check}] {fd.note}: {fd.detail}")
    if len(shown) > 40:
        print(f"   … and {len(shown) - 40} more")


# ---------------------------------------------------------------------------
# hsm verify --demo : plant a contradiction, then watch the gate catch it.
# ---------------------------------------------------------------------------
_CLEAN_MEDS = """\
---
name: meds
status: reference
updated: 2026-07-01
---
# Meds
Notes about medications.
"""

_CLEAN_FACT = """\
---
name: penicillin-allergy
status: hot
updated: 2026-07-01
---
# Penicillin allergy
I'm allergic to penicillin. See [[meds]].

## Changelog
- 2026-07-01: recorded.
"""

# The rot: same note, but now it (a) links to a note we deleted, (b) argues with
# itself about its own status, and (c) its body has drifted months past `updated:`.
_ROTTED_FACT = """\
---
name: penicillin-allergy
status: hot
metadata:
  status: done
updated: 2026-07-01
---
# Penicillin allergy
I'm allergic to penicillin. See [[meds]].

## Changelog
- 2026-09-20: revised dosage guidance (body moved on; updated: never bumped).
- 2026-07-01: recorded.
"""


def run_demo() -> int:
    """Build a throwaway vault, verify it clean, plant rot, verify it caught.
    Returns the exit code the rotted verify would return (nonzero)."""
    with tempfile.TemporaryDirectory(prefix="fbt-verify-demo-") as d:
        v = Path(d)
        (v / "meds.md").write_text(_CLEAN_MEDS, encoding="utf-8")
        (v / "penicillin-allergy.md").write_text(_CLEAN_FACT, encoding="utf-8")

        print("① a clean vault — one fact, one linked note:\n")
        clean = verify_vault(v)
        print_report(clean)

        print("\n② now something rots — a linked note is deleted, the fact starts")
        print("   arguing with itself about its status, its body drifts past its")
        print("   own `updated:` date, and a DISTILLED note carries an uncited")
        print("   claim + a citation to a source that no longer exists. A cloud")
        print("   tool would never tell you:\n")
        (v / "meds.md").unlink()  # dangling [[meds]]
        (v / "penicillin-allergy.md").write_text(_ROTTED_FACT, encoding="utf-8")
        (v / "distilled").mkdir()
        (v / "distilled" / "user.md").write_text(
            "---\nname: user\ntype: distilled\nentity: User\nupdated: 2026-07-01\n---\n\n"
            "# User\n\n"
            "- allergy: penicillin (source: penicillin-allergy.md)\n"   # good claim
            "- favorite_drink: espresso\n"                              # UNCITED claim
            "- home_city: Berlin (source: deleted-note.md)\n"           # DANGLING citation
            "\n## Changelog\n- 2026-07-01: recorded allergy: \"penicillin\" (source: penicillin-allergy.md)\n",
            encoding="utf-8")
        rotted = verify_vault(v)
        print_report(rotted)

        print("\n③ that's the whole point: it PROVES it, live. Own your mind —")
        print("   and prove it never rotted.")
        return 0 if rotted["ok"] else 1
