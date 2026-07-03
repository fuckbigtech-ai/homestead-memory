# `hsm distill` — spec v1 (the distilled layer)

## Why

homestead-memory's verbatim layer wins recall (85-96%) and costs $0 at write time, but
raw transcripts are hard for a reader to reason over — that's why extraction systems
(Mem0/Zep) win QA despite worse recall and expensive writes. Five read-time fixes all
failed (see `benchmarks/RESULTS.md`): the structure has to be built at **write time,
incrementally** — but it does NOT have to be a graph database.

The design is a port of a proven personal-vault architecture: **raw verbatim notes +
distilled entity notes + a Changelog contradiction protocol**, all plain markdown.
Verbatim stays the source of truth; distilled is an additive, derived, *auditable* layer.

**The wedge:** every distilled claim must carry a citation to its raw source, validated
in code (cite-or-drop). Mem0/Zep extraction is unauditable; ours is verifiable — and
`hsm verify` gains a `distill_integrity` check family, extending RotBench.

## Data model  *(v1.1 — hardened per spec review)*

- Distilled notes live at `<vault>/distilled/<entity-slug>.md`. They are normal notes
  (indexed, searchable) — NOT excluded from retrieval.
- **Source scope (explicit):** distill inputs = exactly `vault.iter_notes()` output
  (the indexed notes — so `raw/`, `archive/`, dotdirs, `.hsmignore`d paths, Dashboard/
  MEMORY are already out), **minus** anything under `distilled/` **minus** any note
  with `type: distilled` (no self-ingestion — generated-artifact quarantine).
- Note shape:

```markdown
---
name: <entity-slug>
type: distilled
entity: <Entity Name>
updated: YYYY-MM-DD
---

# <Entity Name>

- <field>: <value> (source: <rel/path.md>)
- <field>: <value> (source: <rel/path.md>)

## Changelog
- YYYY-MM-DD: update <field>: "<old>" -> "<new>" (source: <rel/path.md>)
- YYYY-MM-DD: recorded <field>: "<value>" (source: <rel/path.md>)
```

- **Temporal compatibility (corrected claim):** `temporal.py`'s dated-entry regex parses
  every line above as a dated text entry (history / `--as-of` work as-is). Its existing
  *transition* regex only handles known-field single-token transitions — so `temporal.py`
  gains ONE additive regex for the distill-canonical quoted form
  (`update <field>: "<old>" -> "<new>"`), which handles multi-word values. Existing
  parsing is untouched.
- **Extraction schema (required fields):** `{"entity", "field", "value", "fact", "quote"}`
  — `field`+`value` required (contradiction tracking keys on `(entity, field)`),
  `fact` = human-readable sentence, `quote` = verbatim supporting span.
- **Citations sidecar:** `.hsm/citations.json` =
  `{ "<entity-slug>::<field>": {"source", "quote", "value", "date"} }`. The markdown
  stays human-readable (`(source: path)`); the sidecar retains the exact quote so
  integrity checks can revalidate evidence later. Derived + disposable.
- **Incremental state:** `.hsm/distill_state.json` = `{rel_path: sha1(body)}` of
  **successfully processed** notes only.

## Pipeline (`hsm distill [path] [--model X] [--dry]`)

1. **Diff**: hash in-scope note bodies vs `distill_state.json` → the new/changed set.
2. **Extract** (per changed note, model = `--model` / `$HSM_DISTILL_MODEL`, default a
   local ollama model, temperature 0): strict-JSON list per the schema above. A
   slow/failed/unparseable call skips that note — batch never crashes; the note's
   hash is NOT recorded, so it retries next run.
3. **Cite-or-drop (in code, not trust)**: quote normalization = NFKC + casefold +
   whitespace-fold + strip `*_`\`` emphasis chars; minimum quote length 12 chars;
   normalized quote must occur in the normalized source body, else the fact is
   DROPPED and counted. Dropped facts are reported per run (negative output).
4. **Merge (read-before-write, contradiction protocol, idempotent)**: bullet key =
   `(entity, field)`.
   - New key → add bullet + changelog `recorded <field>: "<value>" (source: …)`.
   - Same key, same normalized value → **no-op** (idempotence: a full re-distill
     after state deletion produces zero new lines).
   - Same key, different value → update the bullet in place + append changelog
     `update <field>: "<old>" -> "<new>" (source: …)`. Never silently overwrite.
   - Changelog dedupe: skip if an identical entry (field+old+new+source) already exists.
   - Frontmatter `updated:` is bumped on every write (prevents later stale-body warns).
5. **Atomic state:** a note's hash is written to `distill_state.json` only after ALL
   its surviving facts merged + files written successfully. Cite-dropped facts count
   as processed (deliberate drops, reported); write/merge failures leave the note
   unprocessed for retry.
6. **Refresh**: caller re-ingests (`hsm ingest`) so distilled notes are searchable;
   `temporal.build` picks up the new changelog lines.

## Resolution rules

- Entity slugs: lower-kebab of the extracted entity name. Two entities colliding on a
  slug merge into one note; the run report counts `entities_created` vs
  `entities_updated` so drift is visible (accepted v1 limitation).
- Citation resolution in verify is **path-based** (`(source: rel/path.md)` must exist
  relative to the vault root), distinct from the stem-based wikilink check.

## Verify integration — `distill_integrity` (extends RotBench)

Runs whenever distilled notes exist:
- **uncited claim**: a body bullet in a distilled note without `(source: …)` → FAIL.
- **dangling citation**: `(source: X)` where X doesn't resolve to an existing note → FAIL.
- changelog lines validated the same way.
- (later, `--deep` + model: semantic "does the source support the claim" check.)

`hsm verify --demo` extension: plant one uncited claim + one dangling citation in a
distilled note → both caught live, nonzero exit.

## Benchmark integration

`longmemeval.py --distill`: after building the per-question vault, run distill over it
(distill model = the run's reader model), ingest both layers, retrieval unchanged.
ONE honest full-500 re-run + official re-judge at the end; publish whatever it says.

## Non-goals (v1)

Entity resolution beyond slug-matching · graph edges · semantic support-checking (v1 is
structural: quote-in-source + citation-resolves) · cloud models for scheduled runs ·
any auto-enforcement.

## Failure modes to design against

- Extraction hallucination → the quote gate (cite-or-drop) is the primary defense.
- Entity-slug drift ("John Smith" vs "john") → normalize slugs (lower, kebab); accept
  imperfect merging in v1, count entities created vs updated so drift is visible.
- Distill loop reprocessing its own output → distilled/ excluded from extraction inputs.
- State corruption → `distill_state.json` is derived; deleting it = full re-distill
  (idempotent by the merge rules).
