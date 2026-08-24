# RotBench — the memory-integrity / tamper / poisoning benchmark

RotBench v1.3 (v1.1 scoring for non-deep runs; see Scope)

<!-- vale off -->

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

> **Scope caveat.** Most checks read a cite-or-drop distilled layer, so pointing
> them at an arbitrary imported store returns ~100 regardless of content. **v1.2
> (2026-07-30) lands the first store-agnostic check**, `unretired_duplicate`, which
> works on any store including raw imports and is what finally separates a clean
> foreign store from a poisoned one. Read
> [Scope, and what v1.1 does NOT measure](#scope-and-what-v11-does-not-measure)
> before quoting a number or comparing tools: the remaining gaps are still real.

It is also the consistency/trust layer for many agents sharing one memory. The
more writers a vault has, the more verification matters: duplicate facts, stale
citations, and unresolved merge conflicts need to be caught mechanically before
they become context for the next agent.

The honest homestead-memory line today is **85% recall / 52.8% QA / RotBench 99.4**.
Do not inflate it. Recall and QA are honest but mid; **RotBench is the number that's
actually ours, because no one else scores the integrity of the store itself.** It is
here to make memory claims falsifiable, not prettier.

<!-- vale on -->

## Threat model

RotBench scores memory against three attack classes — the things that make a
vault untrustworthy, not merely incomplete:

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

## Scope, and what v1.1 does NOT measure

**Read this before quoting a RotBench number.** Added 2026-07-30 after testing the
scorer against foreign stores and finding it could not tell a poisoned one from a
clean one.

RotBench v1.1 scores **the integrity of a store's distilled layer and its
signature.** The load-bearing checks (`uncited_claim`, `dangling_citation`,
`duplicate_value`, `temporal_mismatch`) all read the cite-or-drop distilled
structure. A store without that structure has nothing for them to read.

The measured consequence, reproducible from this repo:

```text
mem0 export, healthy                     -> hsm import -> 100/100 MEMORY INTACT
mem0 export, blatant contradictions      -> hsm import -> 100/100 MEMORY INTACT
  (employer = "fintech, Toronto" AND "healthcare startup, Vancouver";
   "is allergic to shellfish" AND "is not allergic to shellfish";
   a citation to a file that does not exist)
```

Both score 100. The clean result is correct and is a fairness property worth
keeping: a foreign store is **not** penalized merely for being foreign. The second
result is the limitation: raw imported notes never enter the distilled layer, so
every meaningful check passes vacuously.

Plainly:

- ✅ **Valid**: scoring a vault that uses the cite-or-drop distilled layer, and
  scoring tamper via the Ed25519 signature. Both are real and independently tested.
- ❌ **Not valid**: scoring an arbitrary third-party store by importing it and
  running `hsm verify`. It will score ~100 regardless of content. **Do not publish
  comparative numbers obtained that way.**
- ❌ **Not measured at all in v1.1**: semantic contradiction between notes,
  same-subject temporal conflict, and unretired duplicates. `self_contradiction`
  sounds like it covers the first one and does not: it compares a note's flat
  `status:` against its nested `metadata.status`, which is schema hygiene, not
  meaning.

### v1.3 (2026-08-24) — the ledger, and what a dangling pointer means

Two additions, both from measurement rather than preference.

**The agent ledger** (`ledger_*`). A hash-chained, append-only record of what an agent
actually did. It is DERIVED memory: every record is a claim about something that already
happened, so a dangling or broken record is categorically worse than in a hand-written
note. A note may point at something that does not exist yet; a ledger may not, because
you cannot record an event that has not occurred. Hence FAIL.

Note the boundary the spec states rather than hides: **a wholly rebuilt chain verifies
clean.** An attacker who rewrites every record recomputes every hash and the result is
self-consistent by construction. That is not a defect in chain verification, it is its
definition, and it is why `ledger_signature` exists. The test suite asserts the forged
chain passes, so nobody mistakes chaining for proof of authenticity.

**`dead_link`.** Previously all missing `[[wikilinks]]` were one WARN. Measured on a
real 4,808-note vault: of 120 unique missing targets, **93 had never existed** and
**27 had been deleted**. Those are not the same defect. A forward-link is an encouraged
habit; a link that outlived its target is rot. Only git can separate them, so this runs
only in git-backed vaults and DISCLOSES when it cannot run instead of scoring clean.

**Comparability.** A v1.3 `--deep` score is not directly comparable to a v1.2 `--deep`
score on a vault that has a ledger or deleted notes. Non-deep scores are unaffected. The
published fixture scores are unchanged and re-verified under v1.3: **clean 92 /
poisoned 85**, and a test now asserts both the values and that clean still exceeds
poisoned, because a benchmark that stops discriminating has stopped being one.

### v1.2 (2026-07-30) — partially closed

`unretired_duplicate` is the first check that scores memory CONTENT on a store with
no homestead structure, and it closes the specific hole above. (Two pre-existing
checks, `frontmatter` and `broken_link`, already read raw markdown, but they score
file hygiene, not what the store claims.)

Reproduce it from this repo. Both fixtures are FOUR notes and differ in exactly one
line, so the warn-rate denominator is identical and the scores are comparable:

```bash
hsm import benchmarks/fixtures/mem0-clean.json    /tmp/rb-clean    --format mem0
hsm import benchmarks/fixtures/mem0-poisoned.json /tmp/rb-poisoned --format mem0
hsm verify --deep --json /tmp/rb-clean
hsm verify --deep --json /tmp/rb-poisoned
```

```text
clean     n=4  score 92  MEMORY INTACT  unretired_duplicate = 0
poisoned  n=4  score 85  MEMORY INTACT  unretired_duplicate = 1
                                        (r3.md ~ r4.md, containment 1.00)
```

**Corrected 2026-07-30.** An earlier revision published `poisoned -> 88`. That number
came from a FIVE-note store while the clean one had four, so the two were not
comparable and the pair did not reproduce. The fixtures are now committed and both
are n=4.

Note the stamp does NOT move: both stores read MEMORY INTACT, because
`unretired_duplicate` is a WARN. The score and the findings differentiate them; the
verdict and the exit code do not.

Still open, and still not measured: semantic contradiction where the two notes
share few tokens, and same-subject temporal conflict. No mechanical check can do
the first without a model, so it may never land without breaking the no-LLM-judge
property. **A v1.2 --deep score is not directly comparable to a v1.1 --deep score**
on the same vault, because the new WARN family shifts `warn_penalty`. Non-deep
scores are unchanged.

## Mapping to OWASP ASI06

**ASI06 - Memory & Context Poisoning** in the [OWASP Top 10 for Agentic
Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)
names this risk officially. The [OWASP Agent Memory
Guard](https://owasp.org/www-project-agent-memory-guard/) project describes itself
as "the reference implementation that the risk definition currently lacks."

That is the gap RotBench addresses. ASI06 names the risk and enumerates controls;
it does not define a **score**. Without a number, "our memory is protected" is not
a falsifiable claim.

RotBench check families against ASI06's three attack vectors:

| ASI06 attack vector | what it means | RotBench checks |
|---|---|---|
| **Direct injection** | a malicious or buggy agent writes false information straight into shared memory | `uncited_claim` (cite-or-drop: an unsourced distilled claim is a FAIL), `duplicate_value` (conflicting values for one field) |
| **Indirect injection** | an agent processes untrusted external data and stores the result as trusted memory. OWASP notes detection here "requires provenance tracking to identify externally-sourced content" | `uncited_claim` + `dangling_citation` are exactly provenance checks: every distilled claim must carry a `(source: ...)` that resolves inside the vault |
| **Gradual erosion ("sleeper agent")** | an agent behaves normally to build trust, then injects poisoned memories later, so the payload is temporally decoupled from the write | the time-axis family: `temporal_mismatch`, `stale_body`, `updated_ahead`, `citation_source_stale`, plus the WARN on a stale-but-valid signature |

### Where RotBench does NOT overlap ASI06 (stated plainly)

ASI06's mitigation guidance is mostly **preventive and runtime**: encryption at
rest, least-privilege access, session isolation via partition keys, not persisting
raw tool results, immutable system prompts, cache TTLs. RotBench does none of
that. It is **detection over a store at rest**, and it is scored rather than
enforced. The two are complementary layers, not substitutes: Agent Memory Guard
blocks a poisoned write as it happens; RotBench tells you whether what is already
stored can be trusted.

One primitive differs too. Agent Memory Guard validates integrity with SHA-256
baselines; RotBench uses a detached Ed25519 signature over the vault's canonical
markdown state. Same goal (tamper-evidence), different mechanism. A store using
either should be able to report a RotBench score.

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
| `unretired_duplicate` | WARN | (deep) **v1.2, store-agnostic.** two notes are near-textual duplicates, both live, neither marked as superseding the other. Unigram containment >= 0.77, pure stdlib, no model. Catches the accumulating-memory bug (a later note amends or negates an earlier one and the old one is never retired) on ANY store, including raw imports |
| `ledger_chain` | FAIL | (deep) **v1.3.** a record in the agent ledger fails to hash-chain to its predecessor: edited in place, deleted, reordered, or torn by a crash. Reported at the exact index. A ledger is DERIVED memory (every record claims something already happened), so a break is inadmissible rather than merely suspicious |
| `ledger_drop` | FAIL | (deep) **v1.3.** the ledger records that it failed to record. A log that hides its own gaps is worse than no log, because it invites trust it has not earned |
| `ledger_signature` | FAIL | (deep) **v1.3.** the signed checkpoint does not verify against the current head. Catches a wholly rebuilt chain, which hash-chaining alone cannot: an attacker who rewrites every record recomputes every hash and produces a self-consistent file |
| `ledger_unsigned` | WARN | (deep) **v1.3.** a ledger exists with no checkpoint. The chain still proves nobody edited it in place; requiring a key to use the tool would stop people using the tool |
| `dead_link` | FAIL | (deep, git-backed vaults) **v1.3.** a `[[wikilink]]` pointing at a note that git shows was DELETED. Distinct from `broken_link`: measured on a real 4,808-note vault, of 120 unique missing targets **93 never existed** (forward-links, an encouraged habit) and **27 had been deleted** (dead pointers). Grading them identically either punishes good practice or excuses real decay |
| `dead_link_unchecked` | WARN | (deep) **v1.3.** missing link targets exist but the vault is not a git repository, so forward-links cannot be separated from deleted notes. Emitted ONLY when there are broken links to adjudicate: an unconditional coverage warning penalises a vault for the tool's limitation rather than its own state |
| `duplicate_scan_skipped` | WARN | (deep) the vault exceeded the 2000-note pairwise cap, so unretired-duplicate detection did NOT run. Reported rather than silently sampled |
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
