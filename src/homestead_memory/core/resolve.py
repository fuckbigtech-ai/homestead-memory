#!/usr/bin/env python3
"""Conflict resolution for distilled notes."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import distill
from . import provenance
from . import store
from . import vault as vaultlib

_CHANGELOG_LINE_RE = re.compile(r"^-\s*(?P<date>\d{4}-\d{2}-\d{2}):\s*(?P<rest>.*)$")
_RECORDED_RE = re.compile(r'recorded\s+(?P<field>[A-Za-z0-9_]+):\s*"(?P<value>[^"]*)"')
_UPDATE_RE = re.compile(
    r'update\s+(?P<field>[A-Za-z0-9_]+):\s*"(?P<old>[^"]*)"\s*->\s*"(?P<new>[^"]*)"'
)
_BODY_BULLET_RE = re.compile(r"-\s*([A-Za-z0-9_]+):\s*(.*?)\s*\(source:\s*([^)]+)\)")


@dataclass
class _Bullet:
    field: str
    value: str
    source: str
    index: int


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sort_key(ts: str | None) -> tuple[int, datetime]:
    dt = _parse_dt(ts)
    return (1, dt) if dt is not None else (0, datetime.min.replace(tzinfo=timezone.utc))


def _body_bullets(text: str) -> list[_Bullet]:
    out: list[_Bullet] = []
    in_changelog = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            in_changelog = "changelog" in s.casefold()
            continue
        if in_changelog:
            continue
        m = _BODY_BULLET_RE.match(s)
        if m:
            out.append(_Bullet(m.group(1).casefold(), m.group(2).strip(), m.group(3).strip(),
                               len(out)))
    return out


def _entity_name(text: str, fallback: str) -> str:
    fm = vaultlib.parse_frontmatter(text)
    if fm:
        entity = fm["fields"].get("entity")
        if entity:
            return entity
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _line_ts(line: str) -> str | None:
    token = provenance.parse_token(line)
    if token and token.get("ts"):
        return token["ts"]
    m = _CHANGELOG_LINE_RE.match(line.strip())
    return m.group("date") if m else None


def _asserted_values(line: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _RECORDED_RE.finditer(line):
        out.append((m.group("field").casefold(), m.group("value")))
    for m in _UPDATE_RE.finditer(line):
        out.append((m.group("field").casefold(), m.group("new")))
    return out


def _candidate_ts(changelog: list[str], field: str, value: str) -> str | None:
    want_field = field.casefold()
    want_value = _norm(value)
    newest: str | None = None
    for line in changelog:
        for fld, val in _asserted_values(line):
            if fld == want_field and _norm(val) == want_value:
                ts = _line_ts(line)
                if ts is not None and (newest is None or _sort_key(ts) > _sort_key(newest)):
                    newest = ts
    return newest


def _conflicted_fields(bullets: list[_Bullet]) -> dict[str, list[tuple[str, int]]]:
    by_field: dict[str, dict[str, tuple[str, int]]] = {}
    for b in bullets:
        by_field.setdefault(b.field, {}).setdefault(_norm(b.value), (b.value, b.index))
    return {field: list(values.values()) for field, values in by_field.items() if len(values) >= 2}


def _comma_values(values: list[str]) -> str:
    return ", ".join(f'"{v}"' for v in values) if values else '""'


def resolve(entity, vault=None, field=None, strategy="latest", agent=None, session=None) -> dict:
    """Resolve duplicate-value conflicts in one distilled note.

    The current bullet is collapsed to one `(source: resolve)` value, while the
    historical recorded/update lines remain as the audit trail.
    """
    if strategy not in {"latest", "keep-both"}:
        raise ValueError("strategy must be 'latest' or 'keep-both'")

    v = vaultlib._resolve(vault)
    resolver_agent = provenance.resolve_agent(agent)
    resolver_session = provenance.resolve_session(session)
    resolver_ts = provenance.now_ts()
    prov_token = provenance.format_token(resolver_agent, resolver_session, resolver_ts)
    today = date.today().isoformat()
    entity_name = str(entity or "")
    slug = distill.slugify(entity_name)
    rel = Path(distill.DISTILLED_DIR) / f"{slug}.md"
    target = v / rel
    wanted_field = distill._san_field(field) if field else None

    result = {
        "note": rel.as_posix() if target.exists() else None,
        "resolved": [],
        "agent": resolver_agent,
        "session": resolver_session,
        "ts": resolver_ts,
    }

    with store.vault_lock(v):
        if not target.exists():
            result["note"] = None
            return result

        text = target.read_text(errors="replace")
        bullets = _body_bullets(text)
        conflicts = _conflicted_fields(bullets)
        if wanted_field is not None:
            conflicts = {wanted_field: conflicts[wanted_field]} if wanted_field in conflicts else {}
        if not conflicts:
            result["note"] = rel.as_posix()
            return result

        fields = distill._parse_distilled(text)
        changelog = distill._existing_changelog(text)
        resolved_fields: list[dict] = []

        for fld in sorted(conflicts):
            values = conflicts[fld]
            candidates = [
                {"value": val, "index": index, "ts": _candidate_ts(changelog, fld, val)}
                for val, index in values
            ]
            if strategy == "keep-both":
                merged_values = sorted({distill._san_value(c["value"]) for c in candidates},
                                       key=lambda x: x.casefold())
                winner = " | ".join(merged_values)
                losers: list[str] = []
                winner_ts = max((c["ts"] for c in candidates if c["ts"] is not None),
                                default=None, key=_sort_key)
                line = (f'- {today}: resolved {fld}: merged {_comma_values(merged_values)} '
                        f'(source: resolve) {prov_token}')
            else:
                ordered = sorted(
                    candidates,
                    key=lambda c: (_sort_key(c["ts"]), c["index"]),
                )
                kept = ordered[-1]
                winner = distill._san_value(kept["value"])
                winner_ts = kept["ts"]
                losers = sorted({distill._san_value(c["value"]) for c in candidates
                                if distill._san_value(c["value"]) != winner},
                                key=lambda x: x.casefold())
                line = (f'- {today}: resolved {fld}: kept "{winner}" over '
                        f'{_comma_values(losers)} (source: resolve) {prov_token}')

            fields[fld] = (winner, "resolve")
            changelog.append(line)
            resolved_fields.append({
                "field": fld,
                "winner": winner,
                "winner_ts": winner_ts,
                "losers": losers,
                "strategy": strategy,
            })

        cite_p = distill._sidecar(v, distill.CITATIONS_FILE)
        citations = distill._load_json(cite_p)
        for item in resolved_fields:
            citations[f"{slug}::{item['field']}"] = {
                "source": "resolve",
                "quote": "",
                "value": item["winner"],
                "date": today,
                "agent": resolver_agent,
                "session": resolver_session,
                "ts": resolver_ts,
                "sha256": distill._sha256(item["winner"]),
            }

        note_entity = _entity_name(text, entity_name)
        store.atomic_write(cite_p, json.dumps(citations, indent=1, sort_keys=True))
        store.atomic_write(target, distill._render_distilled(slug, note_entity, fields, changelog))
        from . import temporal
        temporal.update_note(rel, v)
        result["note"] = rel.as_posix()
        result["resolved"] = resolved_fields
        return result
