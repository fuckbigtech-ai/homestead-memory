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
