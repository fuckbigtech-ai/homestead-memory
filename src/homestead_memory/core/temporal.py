#!/usr/bin/env python3
"""
core.temporal — a lightweight bi-temporal layer, derived from the markdown.

Notes already carry their own history as dated `## Changelog` lines, e.g.

    ## Changelog
    - 2026-07-01: status hot -> active. Reason: shipped. Prev updated 2026-06-15.

`temporal` regex-parses those into a queryable SQLite sidecar so you can ask
"what was true on date X?" / "when did this change?" — the thing graph-memory
systems (Zep/Graphiti) need a whole graph DB for. Here it's ~a table, because the
markdown is already the bi-temporal record. Markdown-primary, graph-derived.

The sidecar is DERIVED and disposable — rebuild it any time from the notes.
Stored at <vault>/.hsm/temporal.sqlite — a dotdir, never scanned as notes
(the legacy .fbt/ location is still read if present).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import vault as vaultlib

_CHANGELOG_HEADER_RE = re.compile(r"^#{1,6}\s*changelog\s*$", re.I | re.M)
_ENTRY_RE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\s*:\s*(.*)$", re.M)
# A field transition at the START of an entry: "status hot -> active".
# Anchored + restricted to known frontmatter fields so prose that merely contains
# an arrow ("no signal-hook → deferred") doesn't get mis-parsed as a transition.
_KNOWN_FIELDS = "status|type|brand|priority|stage|owner|phase|horizon|next_action"
_TRANSITION_RE = re.compile(
    rf"^({_KNOWN_FIELDS})\s+(\S+)\s*(?:->|→)\s*([^\s.,;]+)", re.I)
# Distill-canonical quoted transitions (additive; handles multi-word values):
#   update <field>: "<old>" -> "<new>"
_QUOTED_TRANSITION_RE = re.compile(
    r'^update\s+([a-z0-9_-]+):\s*"(.*?)"\s*(?:->|→)\s*"(.*?)"', re.I)


def parse_changelog(text: str) -> list[dict]:
    """Extract dated changelog entries (with any field transition) from a note body."""
    m = _CHANGELOG_HEADER_RE.search(text)
    if not m:
        return []
    section = text[m.end():]
    out = []
    for em in _ENTRY_RE.finditer(section):
        date, body = em.group(1), em.group(2).strip()
        field = old = new = None
        qm = _QUOTED_TRANSITION_RE.search(body)     # distill-canonical form first
        if qm:
            field, old, new = qm.group(1).lower(), qm.group(2), qm.group(3)
        else:
            tm = _TRANSITION_RE.search(body)
            if tm:
                field, old, new = tm.group(1).lower(), tm.group(2), tm.group(3)
        out.append({"date": date, "field": field, "old": old, "new": new, "text": body})
    return out


def db_path(vault: Path) -> Path:
    return vault / ".hsm" / "temporal.sqlite"


def _existing_db_path(vault: Path) -> Path:
    """Prefer the current sidecar; fall back to the legacy `.fbt/` location."""
    new = db_path(vault)
    if new.exists():
        return new
    legacy = vault / ".fbt" / "temporal.sqlite"
    return legacy if legacy.exists() else new


def build(vault: Path | str | None = None) -> dict:
    """(Re)build the temporal sidecar from every note's changelog. Returns counts."""
    v = vaultlib._resolve(vault)
    dbp = db_path(v)
    dbp.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(dbp)
    con.execute("DROP TABLE IF EXISTS changes")
    con.execute(
        "CREATE TABLE changes (note TEXT, valid_date TEXT, field TEXT, "
        "old_val TEXT, new_val TEXT, text TEXT)"
    )
    con.execute("CREATE INDEX idx_note ON changes(note)")
    con.execute("CREATE INDEX idx_date ON changes(valid_date)")
    n_notes = n_entries = 0
    for p, rel in vaultlib.iter_notes(v):
        entries = parse_changelog(p.read_text(errors="replace"))
        if entries:
            n_notes += 1
        for e in entries:
            con.execute(
                "INSERT INTO changes VALUES (?,?,?,?,?,?)",
                (rel.as_posix(), e["date"], e["field"], e["old"], e["new"], e["text"]),
            )
            n_entries += 1
    con.commit()
    con.close()
    return {"notes_with_history": n_notes, "entries": n_entries, "db": str(dbp)}


def _connect(vault: Path) -> sqlite3.Connection | None:
    dbp = _existing_db_path(vault)
    if not dbp.exists():
        return None
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    return con


def history(note: str, vault: Path | str | None = None) -> list[dict]:
    """All recorded changes for a note (stem or relpath), newest first."""
    v = vaultlib._resolve(vault)
    con = _connect(v)
    if con is None:
        return []
    like = note if note.endswith(".md") else f"%{note}%"
    rows = con.execute(
        "SELECT valid_date, field, old_val, new_val, text FROM changes "
        "WHERE note = ? OR note LIKE ? ORDER BY valid_date DESC",
        (note, like),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def as_of(note: str, date: str, field: str | None = None,
          vault: Path | str | None = None) -> list[dict]:
    """What was recorded for a note on/before `date` (optionally one field),
    newest-first — the 'what was true when' query."""
    v = vaultlib._resolve(vault)
    con = _connect(v)
    if con is None:
        return []
    q = ("SELECT valid_date, field, old_val, new_val, text FROM changes "
         "WHERE (note = ? OR note LIKE ?) AND valid_date <= ?")
    args = [note, f"%{note}%", date]
    if field:
        q += " AND field = ?"
        args.append(field.lower())
    q += " ORDER BY valid_date DESC"
    rows = con.execute(q, args).fetchall()
    con.close()
    return [dict(r) for r in rows]
