#!/usr/bin/env python3
"""
LongMemEval harness for fbt-memory.

For each question, the haystack of chat sessions IS written into a fresh markdown
vault (the haystack is the product) — then fbt-memory retrieves and a reader
answers. Two runs:

  A (baseline)  = qmd hybrid retrieval, top-k as returned.
  B (temporal)  = same retrieval, then a recency-aware rerank that puts the most
                  recent relevant sessions first — the lever for "knowledge-update"
                  and "temporal-reasoning" questions (where the newest fact wins).

Publish A, B, and the A→B delta. Also emit a RotBench line (verify score of the
constructed vault) — nobody else reports memory integrity alongside recall.

Usage:
    python -m fbt_memory.benchmarks.longmemeval --synthetic
    python -m fbt_memory.benchmarks.longmemeval --data longmemeval_s.json -n 20 --mode both

Reader: $FBT_READER (prompt on stdin) if set, else ~/.local/bin/cc (GLM, flat-rate)
if present. Scoring: normalized answer-inclusion (fast first pass; an LLM judge is
the rigorous upgrade — see --judge, TODO).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core import index, verify

_CC = str(Path.home() / ".local/bin/cc")
_norm_re = re.compile(r"[^a-z0-9 ]+")


# --------------------------------------------------------------------------- data
def load_dataset(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text())
    items = raw if isinstance(raw, list) else raw.get("data", raw.get("questions", []))
    return items


def _sessions_and_dates(item: dict):
    """Tolerant accessor across LongMemEval field-name variants."""
    sessions = (item.get("haystack_sessions") or item.get("sessions")
                or item.get("haystack") or [])
    dates = item.get("haystack_dates") or item.get("dates") or []
    return sessions, dates


SYNTHETIC: list[dict] = [
    {  # knowledge-update: newer session overrides older — Run B should win
        "question_id": "syn-update-1",
        "question_type": "knowledge-update",
        "question": "What CRM does the user currently use?",
        "answer": "HubSpot",
        "haystack_dates": ["2026-01-10", "2026-03-02", "2026-06-20"],
        "haystack_sessions": [
            [{"role": "user", "content": "We just set up Salesforce as our CRM."},
             {"role": "assistant", "content": "Got it, Salesforce is your CRM."}],
            [{"role": "user", "content": "Reminder: team offsite planning for Q2."},
             {"role": "assistant", "content": "Noted the Q2 offsite."}],
            [{"role": "user", "content": "We migrated off Salesforce; we now use HubSpot as our CRM."},
             {"role": "assistant", "content": "Understood, HubSpot is now your CRM."}],
        ],
    },
    {  # single-session factual
        "question_id": "syn-fact-1",
        "question_type": "single-session-user",
        "question": "What is the user allergic to?",
        "answer": "penicillin",
        "haystack_dates": ["2026-02-01", "2026-02-15"],
        "haystack_sessions": [
            [{"role": "user", "content": "Note that I'm allergic to penicillin."},
             {"role": "assistant", "content": "Recorded your penicillin allergy."}],
            [{"role": "user", "content": "What's the weather like today?"},
             {"role": "assistant", "content": "I can't check live weather."}],
        ],
    },
    {  # temporal-reasoning
        "question_id": "syn-temporal-1",
        "question_type": "temporal-reasoning",
        "question": "Which city did the user move to most recently?",
        "answer": "Berlin",
        "haystack_dates": ["2025-11-01", "2026-04-10"],
        "haystack_sessions": [
            [{"role": "user", "content": "I just moved to Toronto for a new job."},
             {"role": "assistant", "content": "Congrats on the Toronto move."}],
            [{"role": "user", "content": "Update: I relocated again, now living in Berlin."},
             {"role": "assistant", "content": "Noted, you're in Berlin now."}],
        ],
    },
]


# ----------------------------------------------------------------- vault building
def build_question_vault(item: dict, root: Path) -> None:
    sessions, dates = _sessions_and_dates(item)
    for i, sess in enumerate(sessions):
        date = dates[i] if i < len(dates) else "2026-01-01"
        date = re.sub(r"[^0-9-]", "", str(date).split()[0])[:10] or "2026-01-01"
        turns = sess if isinstance(sess, list) else [sess]
        body = "\n".join(
            f"**{t.get('role','user')}:** {t.get('content','')}" for t in turns
            if isinstance(t, dict)
        )
        note = (f"---\nname: session_{i:03d}\ndate: {date}\nstatus: reference\n"
                f"updated: {date}\n---\n\n# Session {i} ({date})\n\n{body}\n")
        (root / f"session_{i:03d}.md").write_text(note, encoding="utf-8")


def _note_date(path: str, root: Path) -> str:
    try:
        from ..core import vault as vaultlib
        fm = vaultlib.parse_frontmatter((root / path).read_text(errors="replace"))
        return (fm["fields"].get("date") or fm["fields"].get("updated") or "") if fm else ""
    except Exception:
        return ""


# ------------------------------------------------------------------------- reader
def read(prompt: str, timeout: int = 120) -> str:
    """Call the reader. One slow/failed call returns "" (a wrong-but-scoreable
    prediction) instead of crashing the whole batch — feedback_batch_llm_resilience."""
    env = os.environ.get("FBT_READER")
    if not env and not Path(_CC).exists():
        raise RuntimeError("no reader: set FBT_READER or install cc")
    try:
        if env:
            r = subprocess.run(env.split(), input=prompt, capture_output=True,
                               text=True, timeout=timeout)
            return (r.stdout or "").strip()
        r = subprocess.run([_CC, "-p", prompt, "--output-format", "json"],
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        try:
            return json.loads(r.stdout).get("result", "").strip()
        except Exception:
            return (r.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


# -------------------------------------------------------------------------- score
def _norm(s: str) -> str:
    return _norm_re.sub(" ", (s or "").lower()).strip()


def scores_correct(pred: str, gold: str) -> bool:
    """Normalized inclusion: the gold answer (or all its salient tokens) appears
    in the prediction. Fast first pass; an LLM judge is the rigorous upgrade."""
    p, g = _norm(pred), _norm(gold)
    if not g:
        return False
    if g in p:
        return True
    toks = [t for t in g.split() if len(t) > 2]
    return bool(toks) and all(t in p for t in toks)


# ---------------------------------------------------------------------- one question
def run_question(item: dict, mode: str, k: int = 6) -> dict:
    sessions, _ = _sessions_and_dates(item)
    q = item["question"]
    gold = item.get("answer", "")
    with tempfile.TemporaryDirectory(prefix="lme-") as d:
        root = Path(d)
        build_question_vault(item, root)
        rot = verify.verify_vault(root)  # RotBench on the constructed vault
        ing = index.ingest(root)
        hits = index.search(q, root, k=k * 2 if mode == "b" else k)
        if mode == "b":  # temporal rerank: most-recent relevant sessions first
            hits.sort(key=lambda h: _note_date(h["rel"], root), reverse=True)
        hits = hits[:k]
        context = "\n\n".join(
            f"[{h['title']} · {_note_date(h['rel'], root)}] {h['snippet'][:350]}"
            for h in hits)
        prompt = (f"Answer the question in a few words using ONLY the context. "
                  f"If facts changed over time, use the MOST RECENT.\n\n"
                  f"CONTEXT:\n{context}\n\nQUESTION: {q}\nANSWER:")
        pred = read(prompt)
        # cleanup the transient qmd collection
        try:
            subprocess.run([shutil.which("qmd") or "qmd", "collection", "remove",
                            index.collection_name(root), "--index", index.QMD_INDEX],
                           capture_output=True, timeout=30)
        except Exception:
            pass
    return {"id": item.get("question_id"), "type": item.get("question_type"),
            "q": q, "gold": gold, "pred": pred,
            "correct": scores_correct(pred, gold), "rot": rot["score"],
            "engine": ing.get("engine")}


def run(data: list[dict], modes: list[str], n: int | None) -> dict:
    items = data[:n] if n else data
    results = {m: [] for m in modes}
    for m in modes:
        for it in items:
            res = run_question(it, m)
            results[m].append(res)
            mark = "✓" if res["correct"] else "✗"
            print(f"  [{m.upper()}] {mark} {res['type']:<22} q={res['q'][:48]!r} "
                  f"pred={res['pred'][:40]!r}")
    summary = {}
    for m in modes:
        rs = results[m]
        acc = sum(r["correct"] for r in rs) / len(rs) if rs else 0.0
        summary[m] = {"n": len(rs), "accuracy": round(100 * acc, 1),
                      "avg_rotbench": round(sum(r["rot"] for r in rs) / len(rs), 1) if rs else 0}
    return {"summary": summary, "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LongMemEval harness for fbt-memory")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", action="store_true",
                     help="run the built-in synthetic LongMemEval-format set (pipeline validation)")
    src.add_argument("--data", help="path to a LongMemEval JSON file")
    ap.add_argument("-n", type=int, default=None, help="limit to N questions")
    ap.add_argument("--mode", choices=["a", "b", "both"], default="both")
    ap.add_argument("--shuffle", action="store_true",
                    help="shuffle before sampling (LongMemEval files are type-sorted, so "
                         "a raw -n grabs one question type — shuffle for a representative mix)")
    ap.add_argument("--seed", type=int, default=42, help="deterministic shuffle seed")
    args = ap.parse_args(argv)

    data = SYNTHETIC if args.synthetic else load_dataset(args.data)
    if args.shuffle:
        import random
        random.Random(args.seed).shuffle(data)
    modes = ["a", "b"] if args.mode == "both" else [args.mode]
    print(f"LongMemEval — {'synthetic' if args.synthetic else args.data} · "
          f"{args.n or len(data)} questions · modes={modes}\n")
    out = run(data, modes, args.n)
    print("\n=== RESULTS ===")
    for m, s in out["summary"].items():
        label = "A (qmd baseline)" if m == "a" else "B (qmd + temporal rerank)"
        print(f"  {label}:  {s['accuracy']}%  (n={s['n']}, avg RotBench {s['avg_rotbench']}/100)")
    if "a" in out["summary"] and "b" in out["summary"]:
        delta = out["summary"]["b"]["accuracy"] - out["summary"]["a"]["accuracy"]
        print(f"  A→B temporal delta:  {delta:+.1f} points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
