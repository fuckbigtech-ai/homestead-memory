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
    (root / name).write_text(text, encoding="utf-8")


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
    (tmp_path / "proj.md").write_text(NOTE_CL, encoding="utf-8")
    rep = temporal.build(tmp_path)
    assert rep["entries"] == 2
    assert (tmp_path / ".hsm" / "temporal.sqlite").exists()
    hist = temporal.history("proj", vault=tmp_path)
    assert hist[0]["valid_date"] == "2026-07-01"          # newest first
    asof = temporal.as_of("proj", "2026-06-15", vault=tmp_path)
    assert [r["valid_date"] for r in asof] == ["2026-06-01"]   # only <= date


def test_temporal_legacy_fbt_dir_read(tmp_path):
    (tmp_path / "proj.md").write_text(NOTE_CL, encoding="utf-8")
    temporal.build(tmp_path)
    # simulate a legacy layout: move .hsm -> .fbt
    legacy = tmp_path / ".fbt"
    legacy.mkdir()
    (tmp_path / ".hsm" / "temporal.sqlite").rename(legacy / "temporal.sqlite")
    (tmp_path / ".hsm").rmdir()
    assert temporal.history("proj", vault=tmp_path)        # still readable via fallback


# ---------------------------------------------------------------- RotBench v1.1 families
from homestead_memory.core import index as _index   # noqa: E402


def _checks(rep) -> set:
    return {f["check"] for f in rep["findings"]}


def _distilled(stem_body: str) -> str:
    return f"---\nname: user\ntype: distilled\nupdated: 2026-07-01\n---\n\n# User\n\n{stem_body}\n"


def test_v11_duplicate_value(tmp_path):
    _write(tmp_path, "a.md", OTHER)   # a resolving, fresh source
    _write(tmp_path, "user.md", _distilled(
        "- crm: Salesforce (source: a.md)\n- crm: HubSpot (source: a.md)\n\n"
        "## Changelog\n- 2026-07-01: recorded crm: \"HubSpot\" (source: a.md)\n"))
    rep = verify.verify_vault(tmp_path)
    assert "duplicate_value" in _checks(rep) and not rep["ok"]


def test_v11_temporal_mismatch(tmp_path):
    _write(tmp_path, "a.md", OTHER)
    _write(tmp_path, "user.md", _distilled(
        "- crm: Salesforce (source: a.md)\n\n## Changelog\n"
        "- 2026-06-01: recorded crm: \"Salesforce\" (source: a.md)\n"
        "- 2026-07-01: update crm: \"Salesforce\" -> \"HubSpot\" (source: a.md)\n"))
    rep = verify.verify_vault(tmp_path)
    assert "temporal_mismatch" in _checks(rep) and not rep["ok"]


def test_v11_citation_source_stale(tmp_path):
    _write(tmp_path, "old.md",
           "---\nname: old\nstatus: reference\nupdated: 2026-01-01\n---\nancient evidence\n")
    _write(tmp_path, "user.md", _distilled(
        "- crm: HubSpot (source: old.md)\n\n## Changelog\n"
        "- 2026-07-01: recorded crm: \"HubSpot\" (source: old.md)\n"))
    rep = verify.verify_vault(tmp_path)
    assert "citation_source_stale" in _checks(rep)   # WARN (source >90d old)


def test_v11_updated_ahead(tmp_path):
    _write(tmp_path, "n.md",
           "---\nname: n\nstatus: hot\nupdated: 2026-09-01\n---\n# n\n\n"
           "## Changelog\n- 2026-07-01: recorded.\n")
    rep = verify.verify_vault(tmp_path)
    assert "updated_ahead" in _checks(rep)           # WARN (field bumped past changelog)


def test_v11_index_drift(tmp_path, monkeypatch):
    _write(tmp_path, "n.md", OTHER)
    hsm = tmp_path / ".hsm"; hsm.mkdir()
    (hsm / "ingest.json").write_text('{"content_hash": "STALE", "collection": "x"}', encoding="utf-8")
    monkeypatch.setattr(_index, "_QMD", "/usr/bin/qmd")          # qmd 'available'
    monkeypatch.setattr(_index, "qmd_available", lambda: True)
    monkeypatch.setattr(_index, "_collection_exists", lambda name: True)  # avoid not_indexed
    rep = verify.verify_vault(tmp_path, deep=True)
    assert "index_drift" in _checks(rep)             # stored hash != current content


def test_v11_clean_distilled_still_intact(tmp_path):
    _write(tmp_path, "a.md", OTHER)
    _write(tmp_path, "user.md", _distilled(
        "- crm: HubSpot (source: a.md)\n\n## Changelog\n"
        "- 2026-07-01: recorded crm: \"HubSpot\" (source: a.md)\n"))
    rep = verify.verify_vault(tmp_path)
    assert rep["ok"] and not rep["fails"]            # no new false positives on a clean vault


def test_v11_temporal_tie_no_false_positive(tmp_path):
    _write(tmp_path, "a.md", OTHER)
    _write(tmp_path, "user.md", _distilled(
        "- crm: Pipedrive (source: a.md)\n\n## Changelog\n"
        "- 2026-07-06: update crm: \"HubSpot\" -> \"Pipedrive\" (source: a.md)\n"
        "- 2026-07-06: update crm: \"Salesforce\" -> \"HubSpot\" (source: a.md)\n"))
    rep = verify.verify_vault(tmp_path)
    assert "temporal_mismatch" not in _checks(rep)   # current value is one of the same-day values


def test_v11_multi_source_bullet_value(tmp_path):
    _write(tmp_path, "a.md", OTHER)
    _write(tmp_path, "b.md", OTHER)
    _write(tmp_path, "user.md", _distilled(
        "- crm: HubSpot (source: a.md) (source: b.md)\n\n## Changelog\n"
        "- 2026-07-01: recorded crm: \"HubSpot\" (source: a.md)\n"))
    rep = verify.verify_vault(tmp_path)
    assert "temporal_mismatch" not in _checks(rep) and rep["ok"]   # value parsed as 'HubSpot'
