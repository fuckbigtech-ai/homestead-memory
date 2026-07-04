# RotBench — a memory-integrity score

Every memory benchmark measures **recall** (can you find it?) or **QA** (can you
answer from it?). None measure whether the memory is still *intact*. Memory rots
quietly: a note contradicts itself, a claim's source disappears, a body drifts past
its own changelog, an extracted "fact" was never actually supported by its source.
An independent 2026 evaluation of 8 major memory frameworks found **none** ship
"freshness scoring to discard stale context before it corrupts retrieval."

RotBench is that missing number: **a 0–100 integrity score over a memory store,
computed by mechanical checks — no LLM judge, no vibes.**

## The score

`RotBench = max(0, round(100 × clean_notes / total_notes) − warn_penalty)`, where a
note is *unclean* if it has any FAIL-level finding, and `warn_penalty =
min(15, round(100 × warns/total × 0.3))`. A store with ANY fail is stamped
**ROT DETECTED** regardless of score — one contradicting note is still rot.

## Check families (v1)

| family | level | what it catches |
|---|---|---|
| `frontmatter` | FAIL | unparseable note — unrecoverable memory |
| `self_contradiction` | FAIL | a note that disagrees with itself (flat vs nested status) |
| `uncited_claim` | FAIL | a distilled/extracted claim with no source citation |
| `dangling_citation` | FAIL | a citation that doesn't resolve INSIDE the store (traversal/absolute = rot) |
| `broken_link` | WARN | a reference to a note that no longer exists |
| `stale_body` | WARN | body drifted >14d past its own changelog |
| `bad_status` / `required_field` | WARN | schema drift |
| deep: `fallback_resilience` | FAIL | retrieval dies when the index is down |
| deep: `fixture_miss` | FAIL | a user-pinned "this must stay findable" query stopped resolving |
| deep: `not_indexed` | WARN | index freshness |

Reference implementation: `hsm verify [--deep]` (this repo, MIT). Exit code is the
contract: **nonzero = rot**, so it gates CI/cron like a test suite.

## Why mechanical-only

An LLM judging integrity can hallucinate integrity. Every RotBench check is a
deterministic predicate over the store's own bytes — reproducible on any machine,
no API key, no trust. (The same philosophy as the store itself: claims carry
citations that are *checked*, not believed.)

## Run it on anything

The checks assume only: a folder of markdown notes with YAML frontmatter, optional
`## Changelog` lines (`- YYYY-MM-DD: …`), optional `(source: rel/path.md)` citations
on extracted claims. That's deliberately minimal — most markdown memory/PKM layouts
qualify with zero or trivial adaptation.

```bash
pip install homestead-memory
hsm verify /path/to/your/memory --deep
```

## Break it

The score is only credible if it survives adversaries. If you can construct a store
that is *obviously rotten* to a human but scores INTACT — or an intact store that
false-positives — open an issue with the fixture. **We merge the fixture and fix the
check.** Scoreboard of merged breaks will live here.

## Reporting convention

Alongside any recall/QA number, report: `RotBench <score>/100 (<fails> fail / <warns>
warn, n=<notes>, v1, deep=<bool>)`. We report it in every benchmark run we publish
(see `RESULTS.md`) — we'd like to see other memory systems do the same.
