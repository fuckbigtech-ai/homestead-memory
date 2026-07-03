#!/usr/bin/env python3
"""
Official-methodology LongMemEval QA evaluation.

Re-scores saved predictions using the EXACT per-type judge prompts from the official
LongMemEval eval (github.com/xiaowu0162/LongMemEval, src/evaluation/evaluate_qa.py) —
verbatim `get_anscheck_prompt()`. This decouples generation (the expensive retrieval+
reader stage, run once) from evaluation (cheap, re-runnable), exactly like the official
benchmark (generate hypotheses → evaluate).

The official leaderboard uses gpt-4o as the judge. We default to a strong local/flat-rate
judge (deepseek-v4-pro:cloud) and label it clearly — swap --judge gpt-4o (via the real
repo script) for leaderboard-exact numbers. We also emit the official hypothesis JSONL
({question_id, hypothesis}) so anyone can run the literal official script.

Usage:
    python -m homestead_memory.benchmarks.official_eval <checkpoint.json> [--mode a] \
        [--judge ollama:deepseek-v4-pro:cloud] [--export hyp.jsonl]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from . import longmemeval as L   # reuse the ollama judge dispatch + reader plumbing


# ---- EXACT official prompts (verbatim from LongMemEval src/evaluation/evaluate_qa.py) ----
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
        elif task == 'temporal-reasoning':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
        elif task == 'knowledge-update':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
        elif task == 'single-session-preference':
            template = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
        else:
            raise NotImplementedError
        return template.format(question, answer, response)
    template = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."
    return template.format(question, answer, response)


def official_judge(judge, task, question, answer, response, abstention=False) -> bool:
    prompt = get_anscheck_prompt(task, question, answer, response, abstention)
    kind, model = judge
    if not (response or "").strip():
        # empty response: only correct if the question is abstention (model stayed silent)
        return False
    if kind == "ollama":
        out = L._ollama_generate(model, prompt, 0.0, 60)
    else:
        out = L.read(prompt)  # fallback path
    return out.strip().lower().startswith("y")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Official-methodology LongMemEval QA eval")
    ap.add_argument("checkpoint", help="results checkpoint JSON from the harness")
    ap.add_argument("--mode", default="a")
    ap.add_argument("--judge", default="ollama:deepseek-v4-pro:cloud",
                    help="judge model (leaderboard uses gpt-4o; this is a strong stand-in)")
    ap.add_argument("--export", default=None, help="write official hypothesis JSONL here")
    args = ap.parse_args(argv)

    data = json.load(open(args.checkpoint))
    rows = data[args.mode] if isinstance(data, dict) else data
    judge = (("ollama", args.judge[7:]) if args.judge.startswith("ollama:") else ("cmd", args.judge))

    if args.export:
        with open(args.export, "w") as f:
            for r in rows:
                f.write(json.dumps({"question_id": r.get("id"), "hypothesis": r.get("pred", "")}) + "\n")
        print(f"exported {len(rows)} hypotheses → {args.export}")

    by = defaultdict(lambda: [0, 0])
    total = [0, 0]
    print(f"official eval — judge={args.judge} · {len(rows)} questions\n")
    for r in rows:
        qid = str(r.get("id") or "")
        abstention = qid.endswith("_abs")
        ok = official_judge(judge, r["type"], r["q"], r["gold"], r.get("pred", ""), abstention)
        by[r["type"]][1] += 1
        by[r["type"]][0] += 1 if ok else 0
        total[1] += 1
        total[0] += 1 if ok else 0

    print("=== OFFICIAL-METHODOLOGY RESULTS ===")
    for t in sorted(by):
        c, n = by[t]
        print(f"  {t:<26} {c}/{n}  ({round(100*c/n)}%)")
    print(f"  {'OVERALL':<26} {total[0]}/{total[1]}  ({round(100*total[0]/total[1], 1)}%)")
    print("\n(Leaderboard uses gpt-4o as judge; run the real repo's evaluate_qa.py on the "
          "exported hypothesis file for a leaderboard-exact number.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
