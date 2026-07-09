#!/usr/bin/env python3
"""
core.distill — the distilled layer. Write-time, incremental, additive, AUDITABLE.

Port of a proven personal-vault architecture: raw verbatim notes stay the source of
truth; `hsm distill` maintains per-entity distilled notes (plain markdown) whose every
claim carries a citation to its raw source, validated in code (cite-or-drop). The
extraction camp (Mem0/Zep) builds structure you can't audit; this layer is verifiable —
`hsm verify` gains a `distill_integrity` family.

Spec: docs/DISTILL_SPEC.md (v1.1). Key rules implemented here:
  - source scope   = vault.iter_notes() minus distilled/ minus `type: distilled`
  - cite-or-drop   = the model's supporting quote must occur in the source (normalized)
  - merge          = read-before-write; (entity, field) keyed; contradictions append a
                     Changelog line, never silently overwrite; idempotent on re-runs
  - atomic state   = a note's hash is recorded only after ALL its facts merged cleanly
                     (citations sidecar is written before state, so state never claims
                     evidence that wasn't persisted)
  - long notes     = extracted over sequential windows (never silently truncated);
                     notes beyond the window cap are counted in the report
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from . import provenance
from . import vault as vaultlib

DISTILLED_DIR = "distilled"
STATE_FILE = "distill_state.json"
CITATIONS_FILE = "citations.json"
_MIN_QUOTE_LEN = 12
_WINDOW = 8000          # chars per extraction window
_MAX_WINDOWS = 4        # notes longer than _WINDOW*_MAX_WINDOWS are counted as truncated
_OLLAMA_API = "http://localhost:11434/api/generate"

# a distilled-note body bullet:  - <field>: <value> (source: <rel/path.md>)
_BULLET_RE = re.compile(r"^- (?P<field>[a-z0-9_]+): (?P<value>.*?) \(source: (?P<src>[^)]+)\)\s*$")
_CHANGELOG_HEADER_RE = re.compile(r"^##+\s*Changelog\s*$", re.M)
# dedupe key for an existing changelog line (date-scoped: a re-occurring transition on a
# NEW date is a legitimate new event; same-day re-runs are idempotent)
_LOGLINE_RE = re.compile(
    r'^- (?P<date>\d{4}-\d{2}-\d{2}): (?:update|recorded) (?P<field>[a-z0-9_]+):')


# --------------------------------------------------------------------- helpers
def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").casefold()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def _san_field(field: str) -> str:
    """Sanitize a field name into the bullet/changelog-safe grammar [a-z0-9_]+."""
    f = unicodedata.normalize("NFKC", str(field or "")).casefold()
    f = re.sub(r"[^a-z0-9]+", "_", f).strip("_")
    return f or "fact"


def _san_value(value: str) -> str:
    """One-line, quote-safe value (newlines folded; double-quotes normalized)."""
    v = re.sub(r"\s+", " ", str(value or "")).strip()
    return v.replace('"', "'")


def _normalize(text: str) -> str:
    """Quote-match normalization: NFKC + casefold + whitespace-fold + strip emphasis."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = re.sub(r"[*_`]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _body_text(text: str) -> str:
    # Hash the body below the frontmatter, but ONLY strip a real YAML frontmatter
    # block. A note that opens with a Markdown horizontal rule (`---`) and no YAML
    # must be hashed whole, not mis-sliced at the first two rules.
    fm = vaultlib.parse_frontmatter(text or "")
    if not (fm and fm.get("fields")):
        return text or ""
    m = re.match(r"\A---\s*\n.*?\n---\s*\n", text or "", re.DOTALL)
    return text[m.end():] if m else (text or "")


def _sidecar(vault: Path, name: str) -> Path:
    return vault / ".hsm" / name


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_json(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, sort_keys=True))


# ------------------------------------------------------------------ extraction
_EXTRACT_PROMPT = """Extract durable, entity-keyed facts from the NOTE below.

Return ONLY a JSON array. Each element:
{{"entity": "<who/what the fact is about>",
  "field": "<short snake_case attribute, e.g. current_crm, home_city, allergy>",
  "value": "<the current value, concise>",
  "fact": "<one-sentence human-readable statement>",
  "quote": "<VERBATIM span copied from the note that supports this fact>"}}

Rules: only facts explicitly supported by the note; quote must be copied verbatim
(it is checked mechanically — a paraphrased quote gets the fact discarded); skip
chit-chat, opinions, and anything transient. Empty array if nothing durable.

NOTE ({rel}):
{body}

JSON:"""


def _ollama_generate(model: str, prompt: str, timeout: int) -> str:
    """POST to local ollama. Retries 429/5xx with exponential backoff — a big batch
    (e.g. per-session extraction) must throttle, not silently starve (the 2026-07-04
    rate-limit post-mortem: 41/50 empty predictions from unhandled 429s)."""
    import time
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0}}).encode()
    delay = 2.0
    for attempt in range(5):
        req = urllib.request.Request(_OLLAMA_API, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read()).get("response", "")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise
    return ""


def _ollama_extract(model: str, rel: str, body: str, timeout: int = 240) -> list:
    """Windowed extraction — long notes are covered in sequential windows, never
    silently truncated. Returns the concatenated fact list."""
    windows = [body[i:i + _WINDOW] for i in range(0, max(len(body), 1), _WINDOW)][:_MAX_WINDOWS]
    facts: list = []
    for w in windows:
        out = _ollama_generate(model, _EXTRACT_PROMPT.format(rel=rel, body=w), timeout)
        m = re.search(r"\[.*\]", out, re.DOTALL)
        if m:
            try:
                got = json.loads(m.group(0))
                if isinstance(got, list):
                    facts.extend(got)
            except Exception:
                pass                      # unparseable window → contributes nothing
    return facts


# ----------------------------------------------------------- distilled-note IO
def _parse_distilled(text: str) -> dict:
    """{field: (value, source)} from a distilled note's body bullets."""
    out = {}
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            out[m.group("field")] = (m.group("value").strip(), m.group("src").strip())
    return out


def _render_distilled(slug: str, entity: str, fields: dict, changelog: list[str]) -> str:
    today = date.today().isoformat()
    bullets = "\n".join(f"- {f}: {v} (source: {s})" for f, (v, s) in sorted(fields.items()))
    log = "\n".join(changelog)
    return (f"---\nname: {slug}\ntype: distilled\nentity: {entity}\nupdated: {today}\n---\n\n"
            f"# {entity}\n\n{bullets}\n\n## Changelog\n{log}\n")


def _existing_changelog(text: str) -> list[str]:
    m = _CHANGELOG_HEADER_RE.search(text)
    if not m:
        return []
    return [ln for ln in text[m.end():].splitlines() if ln.strip().startswith("-")]


# ------------------------------------------------------------------- the pass
def distill(vault: Path | str | None = None, model: str | None = None,
            dry: bool = False, extract_fn=None, agent: str | None = None,
            session: str | None = None) -> dict:
    """Run the incremental distill pass. extract_fn(rel, body) -> list[fact-dicts]
    is injectable for tests; default = local ollama at temperature 0 (windowed)."""
    import os
    v = vaultlib._resolve(vault)
    model = model or os.environ.get("HSM_DISTILL_MODEL", "llama3.1:latest")
    extract = extract_fn or (lambda rel, body: _ollama_extract(model, rel, body))
    writer_agent = provenance.resolve_agent(agent)
    writer_session = provenance.resolve_session(session)
    writer_ts = provenance.now_ts()
    prov_token = provenance.format_token(writer_agent, writer_session, writer_ts)

    state_p, cite_p = _sidecar(v, STATE_FILE), _sidecar(v, CITATIONS_FILE)
    state, citations = _load_json(state_p), _load_json(cite_p)
    today = date.today().isoformat()

    rep = {"scanned": 0, "changed": 0, "facts": 0, "dropped": 0, "failed_notes": 0,
           "skipped_unsafe_path": 0, "truncated_notes": 0,
           "entities_created": 0, "entities_updated": 0, "changelog_lines": 0, "dry": dry}

    for p, rel in vaultlib.iter_notes(v):
        rp = rel.as_posix()
        # source scope: never distill the distilled layer (self-ingestion guard)
        if rp.startswith(f"{DISTILLED_DIR}/"):
            continue
        if ")" in rp:
            # a ')' in the path would break the `(source: …)` grammar — skip, visibly
            rep["skipped_unsafe_path"] += 1
            continue
        text = p.read_text(errors="replace")
        source_sha256 = _sha256(_body_text(text))
        fm = vaultlib.parse_frontmatter(text)
        if fm and fm["fields"].get("type") == "distilled":
            continue
        rep["scanned"] += 1
        h = _hash(text)
        if state.get(rp) == h:
            continue
        rep["changed"] += 1
        if len(text) > _WINDOW * _MAX_WINDOWS:
            rep["truncated_notes"] += 1     # visible, never silent

        try:
            raw_facts = extract(rp, text)
        except Exception:
            rep["failed_notes"] += 1
            continue                        # hash NOT recorded → retried next run
        if not isinstance(raw_facts, list):
            raw_facts = []                  # malformed extractor output ≠ crash

        # cite-or-drop, in code
        body_norm = _normalize(text)
        keep = []
        for f in raw_facts:
            if not isinstance(f, dict):
                rep["dropped"] += 1
                continue
            quote = str(f.get("quote", ""))
            ok = (f.get("entity") and f.get("field") and f.get("value")
                  and len(quote) >= _MIN_QUOTE_LEN and _normalize(quote) in body_norm)
            if ok:
                keep.append(f)
            else:
                rep["dropped"] += 1
        rep["facts"] += len(keep)

        # merge per entity (read-before-write; idempotent)
        try:
            by_entity: dict[str, list[dict]] = {}
            for f in keep:
                by_entity.setdefault(slugify(str(f["entity"])), []).append(f)
            for slug, facts in by_entity.items():
                note_p = v / DISTILLED_DIR / f"{slug}.md"
                created = not note_p.exists()
                existing = "" if created else note_p.read_text(errors="replace")
                fields = _parse_distilled(existing)
                changelog = _existing_changelog(existing)
                # date-scoped dedupe keys already present in the changelog
                seen_lines = set(changelog)
                entity_name = str(facts[0]["entity"])
                added: list[str] = []
                touched = False
                for f in facts:
                    fld = _san_field(f["field"])
                    val = _san_value(f["value"])
                    cur = fields.get(fld)
                    if cur and _normalize(cur[0]) == _normalize(val):
                        # no-op value — but backfill the citation if the sidecar lost it
                        # (keeps .hsm/citations.json rebuildable from a full re-distill)
                        citations.setdefault(f"{slug}::{fld}",
                                             {"source": rp, "quote": str(f.get("quote", "")),
                                              "value": val, "date": today,
                                              "agent": writer_agent, "session": writer_session,
                                              "ts": writer_ts, "sha256": source_sha256})
                        continue
                    line = ((f'- {today}: update {fld}: "{cur[0]}" -> "{val}" (source: {rp})')
                            if cur else
                            (f'- {today}: recorded {fld}: "{val}" (source: {rp})'))
                    line = f"{line} {prov_token}"
                    # ALWAYS update the bullet + citation; dedupe governs the LOG only
                    fields[fld] = (val, rp)
                    citations[f"{slug}::{fld}"] = {"source": rp, "quote": str(f.get("quote", "")),
                                                   "value": val, "date": today,
                                                   "agent": writer_agent, "session": writer_session,
                                                   "ts": writer_ts, "sha256": source_sha256}
                    touched = True
                    if line not in seen_lines:
                        added.append(line)
                        seen_lines.add(line)
                        rep["changelog_lines"] += 1
                if added:
                    changelog = added + changelog   # newest-first, in extraction order
                if touched:
                    rep["entities_created" if created else "entities_updated"] += 1
                    if not dry:
                        note_p.parent.mkdir(parents=True, exist_ok=True)
                        note_p.write_text(
                            _render_distilled(slug, entity_name, fields, changelog),
                            encoding="utf-8")
        except Exception:
            rep["failed_notes"] += 1
            continue                        # merge/write failed → hash NOT recorded

        state[rp] = h                       # only after a clean merge

    if not dry:
        _save_json(cite_p, citations)       # evidence first…
        _save_json(state_p, state)          # …then the claim that it was processed
    return rep
