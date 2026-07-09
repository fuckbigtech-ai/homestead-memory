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

from . import provenance
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
_RECORDED_RE = re.compile(r'^recorded\s+([a-z0-9_-]+):\s*"(.*?)"', re.I)
_RESOLVED_KEEP_RE = re.compile(r'^resolved\s+([a-z0-9_-]+):\s*kept\s+"(.*?)"', re.I)
_RESOLVED_MERGE_RE = re.compile(r'^resolved\s+([a-z0-9_-]+):\s*merged\s+(.*?)(?:\s+\(source:|$)', re.I)
_CHANGE_COLUMNS = ("note", "valid_date", "field", "old_val", "new_val", "text",
                   "agent", "session", "ts")
_CHANGE_INSERT = (
    "INSERT INTO changes (note, valid_date, field, old_val, new_val, text, "
    "agent, session, ts) VALUES (?,?,?,?,?,?,?,?,?)"
)


def parse_changelog(text: str) -> list[dict]:
    """Extract dated changelog entries (with any field transition) from a note body."""
    m = _CHANGELOG_HEADER_RE.search(text)
    if not m:
        return []
    section = text[m.end():]
    out = []
    for em in _ENTRY_RE.finditer(section):
        date, body = em.group(1), em.group(2).strip()
        prov = provenance.parse_token(body) or {}
        field = old = new = None
        qm = _QUOTED_TRANSITION_RE.search(body)     # distill-canonical form first
        if qm:
            field, old, new = qm.group(1).lower(), qm.group(2), qm.group(3)
        else:
            tm = _TRANSITION_RE.search(body)
            if tm:
                field, old, new = tm.group(1).lower(), tm.group(2), tm.group(3)
            else:
                rm = _RECORDED_RE.search(body)
                if rm:
                    field, new = rm.group(1).lower(), rm.group(2)
                else:
                    rkm = _RESOLVED_KEEP_RE.search(body)
                    if rkm:
                        field, new = rkm.group(1).lower(), rkm.group(2)
                    else:
                        rmm = _RESOLVED_MERGE_RE.search(body)
                        if rmm:
                            values = re.findall(r'"([^"]*)"', rmm.group(2))
                            if values:
                                field = rmm.group(1).lower()
                                new = " | ".join(sorted(values, key=lambda x: x.casefold()))
        out.append({"date": date, "field": field, "old": old, "new": new, "text": body,
                    "agent": prov.get("agent"), "session": prov.get("session"),
                    "ts": prov.get("ts")})
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


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS changes (note TEXT, valid_date TEXT, field TEXT, "
        "old_val TEXT, new_val TEXT, text TEXT, agent TEXT, session TEXT, ts TEXT)"
    )
    have = {r[1] for r in con.execute("PRAGMA table_info(changes)")}
    for col in _CHANGE_COLUMNS:
        if col not in have:
            con.execute(f"ALTER TABLE changes ADD COLUMN {col} TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_note ON changes(note)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_date ON changes(valid_date)")


def _insert_entry(con: sqlite3.Connection, rel: str, e: dict) -> None:
    con.execute(
        _CHANGE_INSERT,
        (rel, e["date"], e["field"], e["old"], e["new"], e["text"],
         e["agent"], e["session"], e["ts"]),
    )


def build(vault: Path | str | None = None) -> dict:
    """(Re)build the temporal sidecar from every note's changelog. Returns counts."""
    v = vaultlib._resolve(vault)
    dbp = db_path(v)
    dbp.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(dbp)
    con.execute("DROP TABLE IF EXISTS changes")
    _ensure_schema(con)
    n_notes = n_entries = 0
    for p, rel in vaultlib.iter_notes(v):
        entries = parse_changelog(p.read_text(errors="replace"))
        if entries:
            n_notes += 1
        for e in entries:
            _insert_entry(con, rel.as_posix(), e)
            n_entries += 1
    con.commit()
    con.close()
    return {"notes_with_history": n_notes, "entries": n_entries, "db": str(dbp)}


def _resolve_note(vault: Path, note: str | Path) -> tuple[Path | None, str]:
    raw = Path(str(note))
    if raw.suffix.lower() == ".md":
        rel = raw
        if raw.is_absolute():
            try:
                rel = raw.relative_to(vault)
            except ValueError:
                rel = Path(raw.name)
        return (vault / rel if (vault / rel).exists() else None, rel.as_posix())

    stem = raw.stem or str(note)
    preferred = Path("distilled") / f"{stem}.md"
    if (vault / preferred).exists():
        return vault / preferred, preferred.as_posix()
    matches = sorted(
        (p, rel.as_posix())
        for p, rel in vaultlib.iter_notes(vault)
        if p.stem == stem or rel.with_suffix("").as_posix() == str(note)
    )
    if matches:
        return matches[0]
    return None, preferred.as_posix()


def update_note(note, vault: Path | str | None = None) -> int:
    """Refresh one note's rows in the temporal sidecar.

    Creates the current `.hsm/temporal.sqlite` sidecar on fresh vaults. SQLite
    lock errors are treated as a best-effort miss so callers that already wrote
    the markdown note are not broken by a derived index refresh.
    """
    v = vaultlib._resolve(vault)
    dbp = db_path(v)
    dbp.parent.mkdir(parents=True, exist_ok=True)
    note_p, rel = _resolve_note(v, note)
    con = None
    try:
        con = sqlite3.connect(dbp)
        with con:
            _ensure_schema(con)
            con.execute("DELETE FROM changes WHERE note = ?", (rel,))
            if note_p is None:
                return 0
            entries = parse_changelog(note_p.read_text(errors="replace"))
            for e in entries:
                _insert_entry(con, rel, e)
            return len(entries)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "readonly" in str(exc).lower():
            return 0
        raise
    finally:
        if con is not None:
            con.close()


def _connect(vault: Path) -> sqlite3.Connection | None:
    dbp = _existing_db_path(vault)
    if not dbp.exists():
        return None
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    return con


_PROV_COLS = ("agent", "session", "ts")


def _cols(con: sqlite3.Connection) -> str:
    """Column list for history/as_of. Legacy 6-column sidecars (built before
    write-provenance) lack agent/session/ts; pad them with NULL so a stale DB
    queried before the next `hsm ingest` rebuild doesn't crash."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(changes)")}
    if set(_PROV_COLS) <= have:
        return "valid_date, field, old_val, new_val, text, agent, session, ts"
    return ("valid_date, field, old_val, new_val, text, "
            "NULL AS agent, NULL AS session, NULL AS ts")


def history(note: str, vault: Path | str | None = None) -> list[dict]:
    """All recorded changes for a note (stem or relpath), newest first."""
    v = vaultlib._resolve(vault)
    con = _connect(v)
    if con is None:
        return []
    like = note if note.endswith(".md") else f"%{note}%"
    rows = con.execute(
        f"SELECT {_cols(con)} FROM changes "
        "WHERE note = ? OR note LIKE ? ORDER BY valid_date DESC, ts DESC, rowid DESC",
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
    q = (f"SELECT {_cols(con)} FROM changes "
         "WHERE (note = ? OR note LIKE ?) AND valid_date <= ?")
    args = [note, f"%{note}%", date]
    if field:
        q += " AND field = ?"
        args.append(field.lower())
    q += " ORDER BY valid_date DESC, ts DESC, rowid DESC"
    rows = con.execute(q, args).fetchall()
    con.close()
    return [dict(r) for r in rows]
