"""core.verify (the integrity gate) + core.temporal (bi-temporal from changelogs)."""
from pathlib import Path

from homestead_memory.core import temporal, verify

CLEAN = """---
name: {stem}
status: hot
updated: 2026-07-01
---
# {stem}
fine. See [[other]].

## Changelog
- 2026-07-01: recorded.
"""

OTHER = """---
name: other
status: reference
updated: 2026-07-01
---
ok
"""


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text)


def test_verify_clean_vault_intact(tmp_path):
    _write(tmp_path, "note.md", CLEAN.format(stem="note"))
    _write(tmp_path, "other.md", OTHER)
    rep = verify.verify_vault(tmp_path)
    assert rep["ok"] is True and rep["score"] == 100 and not rep["fails"]


def test_verify_catches_self_contradiction(tmp_path):
    _write(tmp_path, "bad.md",
           "---\nname: bad\nstatus: hot\nmetadata:\n  status: done\nupdated: 2026-07-01\n---\nx\n")
    rep = verify.verify_vault(tmp_path)
    assert rep["ok"] is False
    assert any(f.check == "self_contradiction" for f in rep["fails"])


def test_verify_warns_dangling_link_and_stale_body(tmp_path):
    _write(tmp_path, "l.md",
           "---\nname: l\nstatus: hot\nupdated: 2026-01-01\n---\nsee [[ghost]]\n\n## Changelog\n- 2026-06-01: moved on.\n")
    rep = verify.verify_vault(tmp_path)
    checks = {f.check for f in rep["warns"]}
    assert "broken_link" in checks and "stale_body" in checks


def test_verify_demo_catches_planted_rot():
    assert verify.run_demo() == 1              # nonzero = rot caught


def test_verify_no_frontmatter_is_fail(tmp_path):
    _write(tmp_path, "nofm.md", "# raw\nno frontmatter\n")
    rep = verify.verify_vault(tmp_path)
    assert any(f.check == "frontmatter" for f in rep["fails"])


# ------------------------------------------------------------------ temporal
NOTE_CL = """---
name: proj
status: active
updated: 2026-07-01
---
# proj

## Changelog
- 2026-07-01: status hot -> active. Reason: shipped.
- 2026-06-01: recorded. no signal-hook → deferred (prose arrow, not a transition)
"""


def test_changelog_parse_anchored_transitions():
    entries = temporal.parse_changelog(NOTE_CL)
    assert len(entries) == 2
    assert entries[0]["field"] == "status" and entries[0]["old"] == "hot" and entries[0]["new"] == "active"
    assert entries[1]["field"] is None         # prose arrow must NOT parse as a transition


def test_temporal_build_history_asof(tmp_path):
    (tmp_path / "proj.md").write_text(NOTE_CL)
    rep = temporal.build(tmp_path)
    assert rep["entries"] == 2
    assert (tmp_path / ".hsm" / "temporal.sqlite").exists()
    hist = temporal.history("proj", vault=tmp_path)
    assert hist[0]["valid_date"] == "2026-07-01"          # newest first
    asof = temporal.as_of("proj", "2026-06-15", vault=tmp_path)
    assert [r["valid_date"] for r in asof] == ["2026-06-01"]   # only <= date


def test_temporal_legacy_fbt_dir_read(tmp_path):
    (tmp_path / "proj.md").write_text(NOTE_CL)
    temporal.build(tmp_path)
    # simulate a legacy layout: move .hsm -> .fbt
    legacy = tmp_path / ".fbt"
    legacy.mkdir()
    (tmp_path / ".hsm" / "temporal.sqlite").rename(legacy / "temporal.sqlite")
    (tmp_path / ".hsm").rmdir()
    assert temporal.history("proj", vault=tmp_path)        # still readable via fallback
