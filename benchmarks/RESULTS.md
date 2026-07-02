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
