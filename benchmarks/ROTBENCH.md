# RotBench — a memory-integrity score

RotBench v1.1

Every memory benchmark measures **recall** (can you find it?) or **QA** (can you
answer from it?). None measure whether the memory is still *intact*. Memory rots
quietly: a note contradicts itself, a claim's source disappears, a body drifts past
its own changelog, an extracted "fact" was never actually supported by its source.
An independent 2026 evaluation of 8 major memory frameworks found **none** ship
"freshness scoring to discard stale context before it corrupts retrieval."

RotBench is that missing number: **a 0-100 integrity score over a memory store,
computed by mechanical checks — no LLM judge, no vibes.**

The honest homestead-memory line today is **85% recall / 52.8% QA / RotBench 99.4**.
Do not inflate it. RotBench is here to make memory claims falsifiable, not prettier.

## Conformance

The reference scorer is:

```bash
hsm verify --json /path/to/vault
```

It emits this JSON shape:

```json
{
  "ok": true,
  "score": 100,
  "stamp": "MEMORY INTACT",
  "notes": 12,
  "rotbench_version": "v1.1",
  "findings": [
    {
      "level": "warn",
      "check": "broken_link",
      "note": "person.md",
      "detail": "[[ghost]] -> no such note (dangling memory)"
    }
  ]
}
```

Schema:

| key | type |
|---|---|
| `ok` | `bool` |
| `score` | `int` from `0` to `100` |
| `stamp` | `"MEMORY INTACT"` or `"ROT DETECTED"` |
| `notes` | `int` |
| `rotbench_version` | `str` |
| `findings` | `list` of `{level:"fail"|"warn", check:str, note:str, detail:str}` |

The scoring formula is the contract:

```text
score = max(0, round(100 * clean_notes / total_notes) - warn_penalty); warn_penalty = min(15, round(100 * warns/total * 0.3)); verdict INTACT iff (no fails) AND score >= 85.
```

Score your own tool against RotBench in one of two ways:

1. Export your memory to the homestead markdown layout and run `hsm verify --json`
   over it.
2. Implement the check families below plus the exact formula above, and emit the
   same JSON shape.

Deep verification can also run golden recall fixtures from
`<vault>/.hsm/fixtures.json`; see [`examples/README.md`](../examples/README.md) and
[`examples/fixtures.example.json`](../examples/fixtures.example.json).

## The score

`RotBench = max(0, round(100 * clean_notes / total_notes) - warn_penalty)`, where a
note is *unclean* if it has any FAIL-level finding, and `warn_penalty =
min(15, round(100 * warns/total * 0.3))`. A store with any fail is stamped
**ROT DETECTED** regardless of score — one contradicting note is still rot.

## Check families

These are the finding families emitted by `src/homestead_memory/core/verify.py`.
Rows marked "(deep)" run only when `hsm verify --deep` is enabled.

| family | level | what it detects |
|---|---|---|
| `frontmatter` | FAIL | no parseable frontmatter block, so the note is unrecoverable memory |
| `self_contradiction` | FAIL | flat `status:` and nested `metadata.status` disagree inside one note |
| `uncited_claim` | FAIL | a distilled body bullet has no `(source: ...)` citation |
| `dangling_citation` | FAIL | a citation is absolute, not `.md`, escapes the vault, or does not resolve inside it |
| `duplicate_value` | FAIL | the same distilled field is recorded twice with conflicting current values |
| `temporal_mismatch` | FAIL | a distilled current value contradicts the latest-by-date changelog assertion |
| `fallback_resilience` | FAIL | (deep) direct-scan retrieval cannot find a known term when the index is unavailable |
| `fixture_miss` | FAIL | (deep) a golden recall query did not retrieve its expected note |
| `required_field` | WARN | required metadata, currently `name:`, is missing |
| `bad_status` | WARN | `status:` is present but outside the vault status enum |
| `broken_link` | WARN | a `[[wikilink]]` points to no existing note |
| `stale_body` | WARN | the latest changelog date is more than 14 days after `updated:` |
| `updated_ahead` | WARN | `updated:` is more than 30 days ahead of the latest changelog date |
| `citation_source_stale` | WARN | a citation resolves, but its source note is more than 90 days old |
| `fixtures` | WARN | (deep) `.hsm/fixtures.json` exists but is unparseable |
| `not_indexed` | WARN | (deep) qmd is available, but the vault has not been ingested |
| `index_drift` | WARN | (deep) the vault changed since the last ingest, so qmd may ghost-match stale embeddings |

Reference implementation: `hsm verify [--deep]` (this repo, MIT). Exit code is the
contract: **nonzero = rot**, so it gates CI/cron like a test suite.

## Why mechanical-only

An LLM judging integrity can hallucinate integrity. Every RotBench check is a
deterministic predicate over the store's own bytes — reproducible on any machine,
no API key, no trust. The same philosophy as the store itself: claims carry
citations that are *checked*, not believed.

## Run it on anything

The checks assume only: a folder of markdown notes with YAML frontmatter, optional
`## Changelog` lines (`- YYYY-MM-DD: ...`), optional `(source: rel/path.md)` citations
on extracted claims. That's deliberately minimal — most markdown memory/PKM layouts
qualify with zero or trivial adaptation.

```bash
pip install homestead-memory
hsm verify /path/to/your/memory --deep
```

For CI, use the composite action in this repository; see
[`action.yml`](../action.yml) and the consumer example workflow at
[`rotbench-example.yml`](../.github/workflows/rotbench-example.yml).

## Break it

The score is only credible if it survives adversaries. If you can construct a store
that is *obviously rotten* to a human but scores INTACT — or an intact store that
false-positives — open an issue with the fixture. **We merge the fixture and fix the
check.** The public break-it scoreboard lives in
[`benchmarks/SCOREBOARD.md`](SCOREBOARD.md).

## Reporting convention

Alongside any recall/QA number, report: `RotBench <score>/100 (<fails> fail / <warns>
warn, n=<notes>, v1.1, deep=<bool>)`. We report it in every benchmark run we publish
(see `RESULTS.md`) — we'd like to see other memory systems do the same.
