# Benchmarks

## LongMemEval

`fbt-memory` is measured on [LongMemEval](https://github.com/xiaowu0162/LongMemEval)
(ICLR 2025), the de-facto long-term-memory benchmark. For each question, the
haystack of chat sessions is written into a fresh markdown vault (the haystack is
the product), then fbt-memory retrieves and a reader answers.

Two runs:
- **A (baseline):** qmd hybrid retrieval, top-k as returned.
- **B (temporal):** same retrieval, recency-aware rerank (newest relevant sessions
  first) — the lever for `knowledge-update` / `temporal-reasoning` questions.

We publish **A, B, and the A→B delta**, plus a **RotBench** integrity score of the
constructed vault (nobody else reports memory integrity alongside recall).

### Validate the pipeline (no download)

```bash
python -m fbt_memory.benchmarks.longmemeval --synthetic
```

Runs a built-in synthetic LongMemEval-format set. Confirms the full pipeline
(vault build → ingest+embed → retrieve → rerank → reader → score). Note: the
synthetic set is intentionally small, so the A→B delta is ~0 — it proves the
pipeline, not the temporal wedge. The delta shows up on the real data below.

### Run the real benchmark (the published number)

```bash
# 1. get the dataset (264 MB)
pip install huggingface_hub
python -c "from huggingface_hub import hf_hub_download; \
  print(hf_hub_download('xiaowu0162/longmemeval-cleaned', 'longmemeval_s.json', repo_type='dataset'))"

# 2. reader: set FBT_READER (prompt on stdin) or have ~/.local/bin/cc (GLM) present
export FBT_READER="ollama run llama3.1:8b"      # example; any local/flat-rate reader

# 3. run (start small, then scale to the full 500)
python -m fbt_memory.benchmarks.longmemeval --data <path-to>/longmemeval_s.json -n 20 --mode both
python -m fbt_memory.benchmarks.longmemeval --data <path-to>/longmemeval_s.json --mode both
```

Cost note: LongMemEval-S is 500 questions × ~48 sessions (~115K tokens) each, so a
full run embeds a lot and makes 500×2 reader calls — it's a multi-hour job. Start
with `-n 20` to sanity-check, then run the full set (ideally overnight, on a
flat-rate/local reader).

### Scoring

First pass = normalized answer-inclusion (fast). An LLM-judge upgrade (closer to
the official eval) is the rigorous next step — see the `--judge` TODO in
`longmemeval.py`. Report the scoring method alongside the number.
