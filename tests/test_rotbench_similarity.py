"""RotBench v1.2 — store-agnostic unretired-duplicate detection.

These tests lock an EMPIRICALLY TUNED configuration. The constants in
core/similarity.py were not chosen by taste; they were measured. If someone
changes SHINGLE_N, the scoring function, or MIN_TOKENS, these fail loudly, which
is the point: a benchmark whose thresholds drift silently is not a benchmark.

The regression at the bottom (`test_catches_the_case_v1_1_scored_100`) is the
whole reason this module exists.
"""

from __future__ import annotations

from homestead_memory.core import similarity as S


def _score(a: str, b: str) -> float:
    return S.containment(S.shingles(S.tokenize(a)), S.shingles(S.tokenize(b)))


# --- what MUST be flagged -------------------------------------------------

def test_negation_amendment_is_a_duplicate():
    """The canonical accumulating-memory bug: a later note negates an earlier one
    and neither is retired. Near-identical vocabulary, opposite meaning."""
    s = _score("User is allergic to shellfish.",
               "User is not allergic to shellfish, that was someone else.")
    assert s >= S.DUPLICATE_THRESHOLD, s


def test_restatement_with_addition_is_a_duplicate():
    s = _score("The deploy pipeline runs on GitHub Actions with a staging gate.",
               "The deploy pipeline runs on GitHub Actions with a staging gate now.")
    assert s >= S.DUPLICATE_THRESHOLD, s


# --- what MUST NOT be flagged ---------------------------------------------

def test_job_change_is_not_a_duplicate():
    """An honest scope control. An embedding model rates these as similar because
    both are 'employment', but a person changing jobs is legitimate history, not
    rot. Flagging it would be a false positive, and a check that cries wolf gets
    switched off."""
    s = _score("User works as a staff engineer at a fintech company in Toronto.",
               "User works as a product manager at a healthcare startup in Vancouver.")
    assert s < S.DUPLICATE_THRESHOLD, s


def test_unrelated_notes_are_not_duplicates():
    s = _score("User prefers dark roast coffee, no sugar.",
               "The quarterly review is scheduled for August 15.")
    assert s < S.DUPLICATE_THRESHOLD, s


# --- tuning invariants ----------------------------------------------------

def test_separation_margin_is_preserved():
    """The chosen config separated should-flag from should-not-flag by 0.455.
    Guard the margin itself, not just the individual verdicts, so a change that
    keeps every assertion green while collapsing the gap still fails."""
    should = [
        ("User is allergic to shellfish.",
         "User is not allergic to shellfish, that was someone else."),
        ("The deploy pipeline runs on GitHub Actions with a staging gate.",
         "The deploy pipeline runs on GitHub Actions with a staging gate now."),
    ]
    should_not = [
        ("User works as a staff engineer at a fintech company in Toronto.",
         "User works as a product manager at a healthcare startup in Vancouver."),
        ("User prefers dark roast coffee, no sugar.",
         "The quarterly review is scheduled for August 15."),
    ]
    lo = min(_score(a, b) for a, b in should)
    hi = max(_score(a, b) for a, b in should_not)
    assert lo - hi >= 0.40, f"separation collapsed: should>={lo}, should_not<={hi}"
    assert hi < S.DUPLICATE_THRESHOLD <= lo


def test_short_memory_lines_are_still_eligible():
    """MIN_TOKENS was 6 and silently skipped 'User is allergic to shellfish' (5
    tokens), so the check found nothing on exactly the store it exists to catch.
    Memory stores are full of short one-liners; the cutoff must not blind it."""
    assert len(S.tokenize("User is allergic to shellfish.")) == 5
    assert S.MIN_TOKENS <= 5


def test_output_is_deterministic_and_ordered():
    notes = {
        "b.md": "User is not allergic to shellfish, that was someone else.",
        "a.md": "User is allergic to shellfish.",
        "c.md": "User prefers dark roast coffee, no sugar.",
    }
    first = S.find_unretired_duplicates(notes)
    assert first == S.find_unretired_duplicates(dict(reversed(list(notes.items()))))
    assert [(p.left, p.right) for p in first] == [("a.md", "b.md")]


# --- the regression this module exists for --------------------------------

def test_catches_the_case_v1_1_scored_100():
    """Measured 2026-07-30: a Mem0 export with a blatant contradiction imported
    and scored 100/100 MEMORY INTACT under v1.1, identical to a clean export,
    because raw imports never enter the distilled layer the v1.1 checks read.
    This is that store, and the check must now see it."""
    rotten = {
        "r1.md": "User works as a staff engineer at a fintech company in Toronto.",
        "r2.md": "User works as a product manager at a healthcare startup in Vancouver.",
        "r3.md": "User is allergic to shellfish.",
        "r4.md": "User is not allergic to shellfish, that was someone else.",
    }
    clean = {
        "m1.md": "User prefers dark roast coffee, no sugar.",
        "m2.md": "User works as a staff engineer at a fintech company in Toronto.",
        "m3.md": "User is allergic to shellfish.",
        "m4.md": "User's deploy pipeline runs on GitHub Actions with a staging gate.",
    }
    assert S.find_unretired_duplicates(clean) == [], "clean store must stay clean"
    pairs = S.find_unretired_duplicates(rotten)
    assert [(p.left, p.right) for p in pairs] == [("r3.md", "r4.md")]
    # and the legitimate job change is NOT swept up with it
    assert all({p.left, p.right} != {"r1.md", "r2.md"} for p in pairs)
