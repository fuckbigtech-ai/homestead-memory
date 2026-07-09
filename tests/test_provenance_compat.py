"""Backward-compat regression tests for write-provenance (A2).

Two edge cases surfaced by the cross-model adversarial audit:
  1. A legacy 6-column temporal.sqlite (built before provenance) must not crash
     history()/as_of() when queried before the next `hsm ingest` rebuild.
  2. `_body_text()` must hash a note whole when it opens with a Markdown
     horizontal rule (`---`) rather than real YAML frontmatter.
"""
from __future__ import annotations

import sqlite3

from homestead_memory.core import distill, temporal


def test_legacy_temporal_db_does_not_crash(tmp_path):
    (tmp_path / ".hsm").mkdir()
    dbp = temporal._existing_db_path(tmp_path)
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE changes (note TEXT, valid_date TEXT, field TEXT, "
                "old_val TEXT, new_val TEXT, text TEXT)")  # old 6-column schema
    con.execute("INSERT INTO changes VALUES (?,?,?,?,?,?)",
                ("user.md", "2026-01-01", "allergy", None, "penicillin", "recorded allergy"))
    con.commit()
    con.close()

    rows = temporal.history("user", vault=tmp_path)
    assert len(rows) == 1
    # new provenance keys are present but None on legacy rows (padded NULL)
    assert rows[0]["agent"] is None and rows[0]["session"] is None and rows[0]["ts"] is None
    assert temporal.as_of("user", "2026-12-31", vault=tmp_path)  # must not raise


def test_body_text_horizontal_rule_note_is_hashed_whole():
    note = "---\nmeeting notes\n---\nthe body here\n"  # HR, not YAML frontmatter
    assert distill._body_text(note) == note


def test_body_text_real_frontmatter_is_stripped():
    note = "---\nname: x\nstatus: hot\n---\nreal body\n"
    assert distill._body_text(note) == "real body\n"
