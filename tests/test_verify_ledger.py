"""RotBench v1.3 — ledger integrity and dead-link promotion.

Two new families, both grounded in a measurement rather than a preference.

`ledger_*` treats the agent ledger as DERIVED memory: every record is a claim about
something that already happened, so its failure modes are categorically different from
a hand-written note's. A note may point at something that does not exist yet. A ledger
may not, because you cannot record an event that has not occurred. Hence FAIL, not WARN.

`dead_link` splits a single existing warning in two. Measured on a real 4,808-note
vault: of 120 unique missing link targets, 93 had never existed (forward-links, an
encouraged habit) and 27 had been deleted (dead pointers, rot). Grading them the same
either punishes good practice or excuses real decay.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from homestead_memory.core import ledger, verify


def _vault(tmp_path, note="a.md", body="---\nname: a\n---\n"):
    (tmp_path / note).write_text(body, encoding="utf-8")
    return tmp_path


def _checks(rep, prefix):
    return [f for f in rep["findings"] if f["check"].startswith(prefix)]


# --- ledger integrity ------------------------------------------------------

def test_no_ledger_is_not_a_defect(tmp_path):
    """Most vaults will never run the hook. Absence must not be graded."""
    rep = verify.verify_vault(_vault(tmp_path), deep=True)
    assert _checks(rep, "ledger") == []


def test_intact_ledger_produces_no_ledger_failures(tmp_path):
    v = _vault(tmp_path)
    for i in range(3):
        ledger.append("tool_call", target=f"t{i}", vault=v)
    rep = verify.verify_vault(v, deep=True)
    assert [f for f in _checks(rep, "ledger") if f["level"] == "fail"] == []


def test_a_broken_chain_is_a_FAIL_not_a_warning(tmp_path):
    """A ledger with a broken chain is not 'worth a look', it is inadmissible."""
    v = _vault(tmp_path)
    for i in range(4):
        ledger.append("tool_call", target=f"t{i}", vault=v)

    p = v / ledger.LEDGER_REL
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["summary"] = "tampered"
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rep = verify.verify_vault(v, deep=True)
    chain = [f for f in rep["findings"] if f["check"] == "ledger_chain"]
    assert chain, "a tampered ledger must be reported"
    assert all(f["level"] == "fail" for f in chain)
    assert rep["stamp"] == "ROT DETECTED"


def test_known_gaps_are_a_FAIL(tmp_path):
    """A record that hides its own holes is worse than no record: it invites trust."""
    v = _vault(tmp_path)
    ledger.append("tool_call", target="t", vault=v)
    ledger.record_drop("vault lock timed out", vault=v)

    rep = verify.verify_vault(v, deep=True)
    drops = [f for f in rep["findings"] if f["check"] == "ledger_drop"]
    assert len(drops) == 1 and drops[0]["level"] == "fail"
    assert "known gaps" in drops[0]["detail"]


def test_an_unsigned_ledger_warns_but_does_not_fail(tmp_path):
    """The chain still proves nobody edited it in place. Demanding a key to use the
    tool would stop people using the tool."""
    v = _vault(tmp_path)
    ledger.append("tool_call", target="t", vault=v)
    rep = verify.verify_vault(v, deep=True)
    unsigned = [f for f in rep["findings"] if f["check"] == "ledger_unsigned"]
    assert len(unsigned) == 1 and unsigned[0]["level"] == "warn"


# --- dead links ------------------------------------------------------------

def _git(tmp_path, *args):
    subprocess.run(["git", "-C", str(tmp_path), *args],
                   capture_output=True, check=False, text=True)


@pytest.fixture()
def git_vault(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_a_link_to_a_DELETED_note_is_promoted_to_a_failure(git_vault):
    v = git_vault
    (v / "gone.md").write_text("---\nname: gone\n---\n", encoding="utf-8")
    (v / "keeper.md").write_text("---\nname: keeper\n---\nsee [[gone]]\n", encoding="utf-8")
    _git(v, "add", "-A")
    _git(v, "commit", "-qm", "add")
    (v / "gone.md").unlink()
    _git(v, "add", "-A")
    _git(v, "commit", "-qm", "delete gone")

    rep = verify.verify_vault(v, deep=True)
    dead = [f for f in rep["findings"] if f["check"] == "dead_link"]
    assert dead, "a link to a deleted note is a dead pointer, not a forward-link"
    assert dead[0]["level"] == "fail"
    assert "DELETED" in dead[0]["detail"]


def test_a_forward_link_to_a_note_never_created_stays_a_warning(git_vault):
    """Writing [[note-i-will-make]] is encouraged practice. Grading it as rot would
    punish exactly the habit the vault format is designed around."""
    v = git_vault
    (v / "keeper.md").write_text(
        "---\nname: keeper\n---\nsee [[never_written]]\n", encoding="utf-8")
    _git(v, "add", "-A")
    _git(v, "commit", "-qm", "add")

    rep = verify.verify_vault(v, deep=True)
    assert [f for f in rep["findings"] if f["check"] == "dead_link"] == []
    assert [f for f in rep["findings"] if f["check"] == "broken_link"], \
        "it should still be reported, just not as rot"


def test_a_git_less_vault_with_broken_links_discloses_that_it_could_not_check(tmp_path):
    v = _vault(tmp_path, "a.md", "---\nname: a\n---\nsee [[missing]]\n")
    rep = verify.verify_vault(v, deep=True)
    unchecked = [f for f in rep["findings"] if f["check"] == "dead_link_unchecked"]
    assert len(unchecked) == 1, "silently scoring it clean would be a false negative"


def test_no_broken_links_means_no_coverage_warning_at_all(tmp_path):
    """REGRESSION. Emitting 'I could not check' unconditionally penalised every vault
    for OUR limitation: it dropped the published clean fixture from 92 to 85, collapsing
    it onto the poisoned score and destroying the benchmark's discriminating power."""
    v = _vault(tmp_path)
    rep = verify.verify_vault(v, deep=True)
    assert _checks(rep, "dead_link") == []


def test_published_fixtures_still_discriminate_under_v1_3():
    """The number that is published has to keep meaning what it says."""
    import tempfile
    from pathlib import Path

    from homestead_memory.core import portability

    scores = {}
    for name in ("clean", "poisoned"):
        fixture = Path("benchmarks/fixtures") / f"mem0-{name}.json"
        if not fixture.exists():
            pytest.skip("fixtures not present in this checkout")
        d = Path(tempfile.mkdtemp()) / "v"
        portability.import_memories(str(fixture), d, fmt="mem0")
        scores[name] = verify.verify_vault(d, deep=True)["score"]

    # 100 / 92, not 92 / 85. The old pair was measured on a machine with qmd
    # installed, where `not_indexed` fired and cost 8 points on BOTH fixtures.
    # CI has no qmd and reported 100 / 92, which is how the environment
    # dependency was found at all. Advisory checks no longer score, so these
    # numbers are now the same everywhere.
    assert scores["clean"] == 100, f"published clean score moved: {scores}"
    assert scores["poisoned"] == 92, f"published poisoned score moved: {scores}"
    assert scores["clean"] > scores["poisoned"], "the benchmark must discriminate"
