# RotBench — the memory-integrity / tamper / poisoning benchmark

RotBench v1.1

**LOCOMO / LongMemEval measure whether the model REMEMBERS. RotBench measures
whether the memory can be TRUSTED — that it wasn't corrupted, poisoned, or
silently rewritten.** Recall and QA are a crowded, contested lane; integrity is
the axis nobody ships as a gate. Recall has LOCOMO and LongMemEval, hallucination
has HaluMem, poisoning-*attacks* have MPBench — but nothing scores whether the
stored memory itself was tampered with or silently corrupted. And every recall
benchmark reads against a fixed answer key that people keep disputing; RotBench
doesn't read against a key at all — it checks the store against itself.

Every memory benchmark measures **recall** (can you find it?) or **QA** (can you
answer from it?). None measure whether the memory is still *intact*. Memory rots
quietly: a note contradicts itself, a claim's source disappears, a body drifts past
its own changelog, an extracted "fact" was never actually supported by its source.
Most memory tools store locally now; almost none verify what they stored.

RotBench is that missing number: **a 0-100 integrity score over a memory store,
computed by mechanical checks — no LLM judge, no vibes.**

It is also the consistency/trust layer for many agents sharing one memory. The
more writers a vault has, the more verification matters: duplicate facts, stale
citations, and unresolved merge conflicts need to be caught mechanically before
they become context for the next agent.

The honest homestead-memory line today is **85% recall / 52.8% QA / RotBench 99.4**.
Do not inflate it. Recall and QA are honest but mid; **RotBench is the number that's
actually ours, because no one else scores the integrity of the store itself.** It is
here to make memory claims falsifiable, not prettier.

## Threat model

RotBench scores memory against three attack classes — the things that make a
memory store untrustworthy, not merely incomplete:

| class | what it is | the check that catches it | level |
|---|---|---|---|
| **rot** | a note contradicts itself, a citation points at a source that's gone, or a body drifts past its own changelog | `self_contradiction`, `dangling_citation`, `duplicate_value`, `temporal_mismatch`, `stale_body` | FAIL / WARN |
| **tamper** | a note's bytes are edited *after* the store was attested — a post-write rewrite, not a legitimate update | the detached **Ed25519 signature** over the vault's canonical markdown state → `provenance_integrity` (FAIL on an invalid/wrong-signer signature; WARN on a stale-but-valid one) | FAIL / WARN |
| **poisoning** | untrusted input injects a "memory" with no real source — an agent writes a distilled fact carrying no resolving citation | `uncited_claim` (cite-or-drop: every distilled bullet must carry a `(source: …)` that *resolves*). Whether the cited source actually *supports* the claim is the separate distilled-layer verbatim-quote check. | FAIL |

This is not a new idea grafted on — the detection already existed in
`src/homestead_memory/core/verify.py`: signing catches file tamper, `uncited_claim`
catches injected-unsourced (poisoned) claims, `dangling_citation` catches dead
evidence. RotBench makes it **explicit, fixtured, and named** (`tests/test_rotbench_integrity.py`
proves each class is caught with the right Finding).

### Prior art

- **"Context rot"** (Chroma, Jul 2025) — the concept that retrieved context degrades
  as a store accumulates stale/contradictory fragments; the `rot` family targets
  exactly this.
- **"From Untrusted Input to Trusted Memory: A Systematic Study of Memory
  Poisoning Attacks in LLM Agents"** (arXiv, Jun 2026) — systematizes memory
  poisoning into six attack classes and nine vulnerabilities and introduces
  **MPBench** to measure how well those *attacks* succeed. RotBench is the
  complementary half: MPBench scores the attack; RotBench scores the store's
  *defenses* — the cite-or-drop gate (`uncited_claim`) against unsourced
  injection, and the Ed25519 signature (`provenance_integrity`) against
  post-write tampering.

## Conformance

The reference scorer (use `--deep` to include the signature/tamper checks):

```bash
hsm verify --deep --json /path/to/vault
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
| `provenance_integrity` | FAIL/WARN | (deep) the Ed25519 signature over the vault's canonical state: FAIL if invalid / wrong signer, WARN if stale (vault changed since signing) — the tamper axis |
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
