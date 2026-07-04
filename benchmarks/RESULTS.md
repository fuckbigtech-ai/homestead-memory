# LongMemEval — first results (honest log)

Status: **harness validated; no publishable headline number yet.** These are early,
noise-dominated runs on a small sample with a small local reader. Recorded here in
full because building in public means showing the messy middle, not just a win.

## Setup

- Data: `longmemeval_oracle.json` (evidence-only split, easiest variant), 500 Q total.
- Sample: `-n 15 --shuffle --seed 42` (stratified across all 6 question types).
- Retrieval: qmd hybrid (BM25 + vector). **A** = top-k as returned. **B** = relevance-
  dominant + light (0.15) recency boost.
- Reader: local `llama3.1:8b` via ollama (default temperature).
- Scorer: normalized answer-inclusion (crude — under-counts correct-but-reworded answers).
- RotBench (memory integrity of the constructed vault): **100/100** every run.

## Runs

| run | A (baseline) | B (temporal) | delta | note |
|----|----|----|----|----|
| 1 | — | — | — | INVALID: `-n 20` hit only temporal-reasoning (file is type-sorted) + cloud reader timed out → empty answers |
| 2 | 26.7% | 13.3% | −13.4 | valid; exposed a bug — naive newest-first rerank wrecks ordering questions |
| 3 | 13.3% | 20.0% | +6.7 | rerank fixed (relevance-preserving blend); same seed as run 2 |

## The honest caveat (why there's no number yet)

**Baseline A swung 26.7% → 13.3% between runs 2 and 3 — identical seed, identical
15 questions.** That 13-point swing is pure reader stochasticity (llama3.1 at default
temperature). A +6.7 A→B delta is meaningless when the baseline varies ±13 from noise
alone. At n=15 with a stochastic 8B reader, these numbers cannot support any A-vs-B
conclusion. Claiming "temporal helps" here would be reading signal out of noise.

## What IS established

- The full pipeline runs end-to-end on real LongMemEval data: per-question haystack →
  markdown vault → ingest + embed → qmd retrieval → (B) temporal rerank → reader →
  score → aggregate, with RotBench alongside.
- It's resilient: a slow/failed reader call degrades to an empty (scoreable) answer,
  not a crash.
- The naive recency rerank is a known-bad heuristic; the relevance-preserving blend
  no longer catastrophically hurts.

## Path to a publishable number

1. **Scale:** n ≥ 100 (ideally the full 500) to beat per-run variance.
2. **Deterministic / stronger reader:** temperature 0, and a larger model than 8B.
3. **LLM-judge scorer:** replace normalized-inclusion with a judge (closer to the
   official eval); report the method alongside the number.
4. **Run the real `_s` set** (with distractors) — where the temporal lever should
   actually show up (current-value sessions buried below top-k), unlike the oracle
   split. Then compare against Mem0 (~49%) and Zep (~63.8%).

Reproduce any run: see `benchmarks/README.md`.

## Run 4 — first STABLE number (n=100, frontier reader + independent judge)

- reader `glm-5.2:cloud`, judge `deepseek-v4-pro:cloud`, n=100 stratified, checkpointed, 0 crashes.
- **A 25.0% · B 25.0% · delta +0.0** · RotBench 100.
- Per-type (A): knowledge-update 36%, temporal-reasoning 28%, multi-session 19%, single-session ~17-20%.
- **Diagnosis:** low + zero delta = a RETRIEVAL-GRANULARITY bug, not the memory model. The
  reader was fed qmd's ~350-char snippet, not the full retrieved session — on LongMemEval the
  answer is often one sentence in a long session, so the snippet dropped it. Caps A and B at the
  same ceiling → delta 0 (rerank can't help when the context can't hold the answer).
- **Fix (run 5):** feed the reader the FULL retrieved note body (≤1800 chars/note), not the snippet.

## Run 6 — THE NUMBER (path bug fixed) ✅

- reader `glm-5.2:cloud`, judge `deepseek-v4-pro:cloud`, n=100 stratified, checkpointed, 0 errors.
- **A (baseline) 69.0% · B (temporal) 70.0% · delta +1.0** · RotBench 100/100.
- Per-type (A): single-session-assistant 100%, knowledge-update 86%, single-session-user 83%,
  temporal-reasoning 66%, single-session-preference 50%, multi-session 48%.
- **Root cause of the earlier 25-30%:** qmd normalizes `_`→`-` in its `qmd://` URIs, so notes
  written `session_000.md` never matched the retrieved `session-000.md` → `_body`/`_note_date`
  silently fell back to a 350-char snippet on EVERY multi-turn note. Fixed: hyphenated filenames
  + `_resolve_note()` (-/_ tolerant + glob-by-stem). Verified on the exact failing case (reader
  now sees the evidence in 8000 chars, not 350).
- Journey: 25 → 26 → 29 → 30 → **69**, every step from reading actual failures, not re-rolling.

### Honest caveats (before quoting this publicly)
- **Oracle split** (evidence-only, no distractors) — easier than the `_s` set Mem0 (49%) and Zep
  (63.8%) report on. NOT apples-to-apples yet. The headline comparison run is `_s` with distractors.
- Temporal delta is +1.0 here; oracle rarely buries the current-value session below top-k, so the
  rerank has little to fix. The `_s` set is where it should earn its delta.
- n=100 of 500. Full-500 `_s` run is the publishable figure.

## QA optimization journey (_s, n=50, glm-5.2 reader + deepseek judge)

| version | change | QA | note |
|---|---|---|---|
| baseline | simple prompt | 60% | recall 96% — the gap is all reading |
| v2 | +question_date +CoT | 58% | temporal +2, but multi-session −2 (forced-guess) |
| v3 | +abstention | 60% | multi recovered, but preference over-abstained → 0 |
| **v4** | **per-type routing** | **68%** | **✅ clean beat of Zep 63.8% / Mem0 49%.** preference 5/5, temporal 8/9 |
| v5 | +Tier-2 enumerate-then-count | 46% | ❌ NET-NEGATIVE — "how many" is mostly duration/date-math, not enumeration; hijacked temporal (8→2). Flag off by default. |

**Canonical config = v4** (per-question-type adaptive routing; `--deterministic` OFF):
**68% QA · 96% recall · ~5.7k ctx tokens/q · RotBench 99.5** on the `_s` distractor set.

Lesson: per-type routing is the real lever (60→68). Deterministic counting is only
valid for TRUE enumeration ("how many [nouns]"), not "how many days/hours/times"
(duration) — the naive detector backfired. A future surgical version would (a) detect
enumeration vs duration vs sum, (b) date-diff arithmetic for durations. Deferred —
diminishing returns vs the n=50 noise floor; the win to lock is v4's 68%.

## Official-methodology evaluation (credibility check)

Implemented the EXACT per-type judge prompts from the official LongMemEval
`src/evaluation/evaluate_qa.py` (`get_anscheck_prompt()`, verbatim) as
`benchmarks/official_eval.py`. It re-scores saved predictions — decoupling generation
(expensive) from evaluation (cheap, re-runnable), like the official benchmark. Also
exports the official hypothesis JSONL ({question_id, hypothesis}) so the literal repo
script can be run with gpt-4o.

**v4 (n=50) re-judged under official methodology: 66.0%** (vs my-harness judge 68%).
Within 2 points → the harness is sound, not gaming. Per-type nearly identical
(temporal 89%, preference 80%, assistant 100%, multi-session 50%). **Beats Zep 63.8%
/ Mem0 49% under the official eval too.**

Note: the leaderboard judge is gpt-4o; we use deepseek-v4-pro:cloud (strong stand-in,
labeled). For a leaderboard-exact number, run the real repo's evaluate_qa.py with
gpt-4o on the exported hypothesis file (drop-in format). Full-500 will be official-
judged when it lands.

## DEFINITIVE — full-500 official + why we stopped (2026-07-03)

**Canonical config = clean v4** (per-type adaptive prompting on raw full context; all
experimental flags OFF by default: --deterministic, --chunk turns, --structure, --v4pp).

**Full-500 `_s`, official methodology:** QA **52.8%** (official) / 53.4% (my-judge — both
agree, harness validated) · recall@k **85%** · ~**5,247** tokens/q · RotBench **99.4**.

**Five improvement experiments ALL failed to beat v4** (chunk+span 26, codex-reader 54,
structuring 46, v4++ complete-evidence 56, vs v4 68 n=50). The QA ceiling is ARCHITECTURAL
— beating Mem0/Zep on QA needs their write-time structured-extraction (abandons our
verbatim/cost/verification edge). n=50 is noise-dominated (qmd retrieval nondeterminism,
recall ±10-15%); full-500 is the stable number. **Decision: ship the real story (elite
recall + only self-verifying + lowest cost + local + honest), not the QA crown.** Full
writeup: vault note fbt_memory_benchmark_result_2026-07-03.

## Distilled-layer run (n=50, 2026-07-04) — INVALID (rate-limit starvation)

First measurement attempt of `--distill` came back QA 18% — but **41/50 predictions
were EMPTY**: the ~2,400 per-session extraction calls exhausted the cloud provider's
rate limit (HTTP 429), which then starved the READER and JUDGE calls too. The number
measures the rate limiter, not the distilled layer. **Distill's benchmark effect is
UNMEASURED** (the layer's own artifacts were fine: RotBench 99.6 incl. distill_integrity).

Fixes shipped: 429/5xx exponential backoff in both ollama call sites. Lesson logged:
call-amplifying designs (48 extractions/question) need throttling and a call budget;
this was the campaign's third cloud-dependency contamination (ccr timeout, qmd index
collision, provider 429). Re-measure later with backoff + local extraction model —
non-blocking for launch (distill is a product feature for coherent personal vaults;
its live E2E is verified — see the demo).
