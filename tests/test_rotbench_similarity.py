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


# --- integration: the check wired into verify --------------------------------

def test_verify_deep_emits_json_serializable_findings(tmp_path):
    """Regression: iter_notes yields `rel` as a Path, and putting it straight into
    Finding.note made the whole --json report raise
    'Object of type PosixPath is not JSON serializable'. It only reproduced on a
    vault that actually HAS a duplicate, so the clean case passed and hid it.
    """
    import json
    from homestead_memory.core import verify

    v = tmp_path / "v"
    v.mkdir()
    (v / "a.md").write_text(
        "---\nname: a\n---\n\nUser is allergic to shellfish.\n")
    (v / "b.md").write_text(
        "---\nname: b\n---\n\nUser is not allergic to shellfish, that was someone else.\n")

    rep = verify.verify_vault(v, deep=True)
    dups = [f for f in rep["findings"] if f["check"] == "unretired_duplicate"]
    assert dups, "the duplicate pair must be reported"
    assert all(isinstance(f["note"], str) for f in rep["findings"])
    json.dumps(rep["findings"])          # must not raise


def test_verify_deep_is_quiet_on_a_clean_store(tmp_path):
    from homestead_memory.core import verify
    v = tmp_path / "v"
    v.mkdir()
    (v / "a.md").write_text("---\nname: a\n---\n\nUser prefers dark roast coffee, no sugar.\n")
    (v / "b.md").write_text("---\nname: b\n---\n\nThe quarterly review is scheduled for August 15.\n")
    rep = verify.verify_vault(v, deep=True)
    assert [f for f in rep["findings"] if f["check"] == "unretired_duplicate"] == []


# --- first-run guidance --------------------------------------------------

def test_quarantine_hint_fires_on_a_directory_of_timestamped_run_records(tmp_path):
    """Measured on a real vault: 1,466 frontmatter FAILs, 1,385 from ONE directory
    of one-per-timestamp run records, scoring 73 ROT DETECTED. Dumping that with no
    explanation makes a correct tool look broken.

    Rewritten 2026-07-30. This previously used undated names (`Meta/report-3.md`)
    and asserted only that "Meta/" appeared somewhere in the output — so it passed
    against the version that suggested excluding the whole parent. It was encoding
    the loose behaviour, not the intended one. The timestamped naming IS the signal.
    """
    from homestead_memory.core import verify
    d = tmp_path / "Meta" / "reports"
    d.mkdir(parents=True)
    for i in range(50):
        (d / f"2026-07-{i % 28 + 1:02d}T00-00-{i:02d}-report.md").write_text("no frontmatter\n")
    rep = {"fails": [_fm_fail(f"Meta/reports/{f.name}") for f in sorted(d.glob("*.md"))]}

    hint = verify.quarantine_hint(rep, tmp_path)
    assert hint and "echo 'Meta/reports/' >> .hsmignore" in hint


def test_quarantine_hint_silent_on_a_small_or_scattered_vault():
    """Must not nag. A handful of failures, or failures spread across the vault,
    are real findings and not a configuration problem."""
    from homestead_memory.core import verify
    few = [verify.Finding("fail", "frontmatter", "a.md", "x")]
    assert verify.quarantine_hint({"fails": few}) is None
    scattered = [verify.Finding("fail", "frontmatter", f"dir{i}/n.md", "x") for i in range(50)]
    assert verify.quarantine_hint({"fails": scattered}) is None
    assert verify.quarantine_hint({"fails": []}) is None


def _fm_fail(rel):
    from homestead_memory.core import verify
    return verify.Finding("fail", "frontmatter", rel, "no frontmatter")


def test_quarantine_hint_never_suggests_a_dir_holding_real_notes(tmp_path):
    """The regression that matters: suggesting the shared PARENT.

    The first version grouped failures by their top-level directory and emitted
    `echo 'Meta/' >> .hsmignore`. On the author's vault that one line would have
    excluded 104 real notes along with the run records — hiding memory that can
    genuinely rot and inflating the score. A hint that inflates your score on an
    integrity benchmark is worse than no hint.
    """
    from homestead_memory.core import verify
    runs = tmp_path / "Meta" / "runs"
    runs.mkdir(parents=True)
    for i in range(30):
        (runs / f"2026-07-{i % 28 + 1:02d}T00-00-{i:02d}-run.md").write_text("no frontmatter\n")
    # real, hand-maintained memory living in the SAME parent
    for i in range(10):
        (tmp_path / "Meta" / f"real_note_{i}.md").write_text(
            "---\nname: n\nupdated: 2026-07-30\n---\n\n## Changelog\n- 2026-07-30: x\n")

    rep = {"fails": [_fm_fail(f"Meta/runs/{f.name}") for f in sorted(runs.glob("*.md"))]}
    hint = verify.quarantine_hint(rep, tmp_path)

    assert hint is not None
    assert "echo 'Meta/runs/' >> .hsmignore" in hint
    assert "echo 'Meta/' >> .hsmignore" not in hint, "would exclude the 10 real notes too"


def test_quarantine_hint_declines_a_mixed_directory(tmp_path):
    """Failures inside a dir that ALSO holds real memory are not quarantinable."""
    from homestead_memory.core import verify
    d = tmp_path / "Brands"
    d.mkdir()
    for i in range(25):
        (d / f"2026-07-{i % 28 + 1:02d}-report.md").write_text("no frontmatter\n")
    for i in range(25):   # half the directory is real memory
        (d / f"brand_{i}.md").write_text("---\nname: b\n---\n")

    rep = {"fails": [_fm_fail(f"Brands/{f.name}") for f in sorted(d.glob("2026-*.md"))]}
    assert verify.quarantine_hint(rep, tmp_path) is None


def test_quarantine_hint_ignores_undated_clusters(tmp_path):
    """Timestamped naming is the run-record signal. Without it, stay quiet."""
    from homestead_memory.core import verify
    d = tmp_path / "Notes"
    d.mkdir()
    for i in range(30):
        (d / f"topic_{i}.md").write_text("no frontmatter\n")
    rep = {"fails": [_fm_fail(f"Notes/{f.name}") for f in sorted(d.glob("*.md"))]}
    assert verify.quarantine_hint(rep, tmp_path) is None
