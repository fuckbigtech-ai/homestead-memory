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

## Data model

- Distilled notes live at `<vault>/distilled/<entity-slug>.md`. They are normal notes
  (indexed, searchable) — NOT excluded.
- Note shape:

```markdown
---
name: <entity-slug>
type: distilled
entity: <Entity Name>
updated: YYYY-MM-DD
---

# <Entity Name>

- <current durable fact> (source: <rel/path.md>)
- <another fact> (source: <rel/path.md>)

## Changelog
- YYYY-MM-DD: <field/claim> <old> → <new> (source: <rel/path.md>)
- YYYY-MM-DD: recorded: <fact> (source: <rel/path.md>)
```

- The `## Changelog` format is intentionally the one `core/temporal.py` already parses —
  distilled notes feed the temporal layer with zero new code.
- Incremental state: `.hsm/distill_state.json` = `{rel_path: sha1(body)}` of processed
  raw notes. `hsm distill` only touches new/changed notes.

## Pipeline (`hsm distill [path] [--model X] [--dry]`)

1. **Diff**: hash raw note bodies vs `distill_state.json` → the new/changed set.
   Distilled notes themselves and ignored paths are never inputs (no self-ingestion —
   the generated-artifact-quarantine rule).
2. **Extract** (per changed note, model = `--model` / `$HSM_DISTILL_MODEL`, default a
   local ollama model): prompt returns strict JSON
   `[{"entity", "fact", "field"?, "quote"}]` where `quote` is a verbatim supporting
   span from the note. Temperature 0. One slow/failed call skips that note (batch
   never crashes; note stays "unprocessed" for the next run).
3. **Cite-or-drop (in code, not trust)**: normalize whitespace/case; `quote` must occur
   in the source body, else the fact is DROPPED and counted. Dropped facts are
   reported (negative output — silence ≠ clean).
4. **Merge (read-before-write, contradiction protocol)**: load the entity's distilled
   note if it exists.
   - New fact → append bullet + changelog `recorded: … (source: …)`.
   - Contradicting fact (same entity+field, different value) → update the bullet,
     append changelog `<field> <old> → <new> (source: …)`. Never silently overwrite.
   - Duplicate (same value) → no-op.
5. **Refresh**: update state; caller re-ingests (`hsm ingest`) so distilled notes are
   searchable; `temporal.build` picks up the new changelog lines natively.

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
