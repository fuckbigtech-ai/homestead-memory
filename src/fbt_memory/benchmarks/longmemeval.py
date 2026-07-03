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
import urllib.request
from pathlib import Path

from ..core import index, verify

_CC = str(Path.home() / ".local/bin/cc")
_norm_re = re.compile(r"[^a-z0-9 ]+")
_WORD = re.compile(r"[a-z0-9]{3,}")
_OLLAMA_API = "http://localhost:11434/api/generate"
_READER = None   # ("ollama", model) | ("cmd", shellcmd) | ("cc",)  — set in main()
_JUDGE = None    # ("ollama", model) | None (None -> normalized-inclusion scoring)
_CONTEXT = "full"  # "full" (whole session) | "span" (precise relevant turns, low-token)
_DETERMINISTIC = False  # Tier-2: enumerate-then-count in code for "how many" questions
_CHUNK = "session"  # "session" (one note per session) | "turns" (finer turn-windows)


def _ollama_generate(model: str, prompt: str, temperature: float = 0.0, timeout: int = 180) -> str:
    """Deterministic (temp-0) generation via the local ollama HTTP API — reliable and
    reproducible, unlike the flat-rate cloud path that timed out mid-batch."""
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": temperature}}).encode()
    req = urllib.request.Request(_OLLAMA_API, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("response", "").strip()


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
_CHUNK_TURNS = 2  # window size when chunking sessions into turn-windows


def build_question_vault(item: dict, root: Path) -> None:
    # Hyphen, not underscore, in filenames: qmd normalizes '_'→'-' in its qmd:// URI,
    # so an underscore name won't round-trip (rel from search != file on disk).
    sessions, dates = _sessions_and_dates(item)
    for i, sess in enumerate(sessions):
        date = dates[i] if i < len(dates) else "2026-01-01"
        date = re.sub(r"[^0-9-]", "", str(date).split()[0])[:10] or "2026-01-01"
        turns = [t for t in (sess if isinstance(sess, list) else [sess]) if isinstance(t, dict)]

        def _fmt(ts):
            return "\n".join(f"**{t.get('role','user')}:** {t.get('content','')}" for t in ts)

        if _CHUNK == "turns":
            # Finer granularity → the exact relevant turns rank top (MemPalace's recall
            # trick) AND the reader gets precise context (fewer tokens). The chunk name
            # keeps the session index so recall (session-(\d+)) still resolves.
            for j in range(0, len(turns) or 1, _CHUNK_TURNS):
                cid = f"session-{i:03d}-chunk-{j // _CHUNK_TURNS:02d}"
                note = (f"---\nname: {cid}\ndate: {date}\nstatus: reference\n"
                        f"updated: {date}\n---\n\n# Session {i} ({date})\n\n{_fmt(turns[j:j + _CHUNK_TURNS])}\n")
                (root / f"{cid}.md").write_text(note, encoding="utf-8")
        else:
            note = (f"---\nname: session-{i:03d}\ndate: {date}\nstatus: reference\n"
                    f"updated: {date}\n---\n\n# Session {i} ({date})\n\n{_fmt(turns)}\n")
            (root / f"session-{i:03d}.md").write_text(note, encoding="utf-8")


def _resolve_note(root: Path, rel: str) -> Path | None:
    """Map a retrieval rel-path to the real file on disk, tolerating qmd's '_'→'-'
    URI normalization (else reads silently fail and fall back to a truncated snippet)."""
    p = root / rel
    if p.exists():
        return p
    stem = Path(rel).stem
    for cand in (stem, stem.replace("-", "_"), stem.replace("_", "-")):
        f = root / f"{cand}.md"
        if f.exists():
            return f
    matches = [q for q in root.glob("*.md") if q.stem.replace("_", "-") == stem.replace("_", "-")]
    return matches[0] if matches else None


def _note_date(path: str, root: Path) -> str:
    f = _resolve_note(root, path)
    if not f:
        return ""
    try:
        from ..core import vault as vaultlib
        fm = vaultlib.parse_frontmatter(f.read_text(errors="replace"))
        return (fm["fields"].get("date") or fm["fields"].get("updated") or "") if fm else ""
    except Exception:
        return ""


# ------------------------------------------------------------------------- reader
_CODEX = "/opt/homebrew/bin/codex"
_CODEX_NOISE = re.compile(r"^(hook:|codex$|tokens used$|[\d,]+$)")


def _parse_codex(out: str) -> str:
    """Strip the codex-exec harness noise (hook lines, 'tokens used', token counts),
    leaving the model's answer. The adaptive prompt's 'ANSWER:' line is extracted
    downstream in run_question."""
    lines = [s for ln in out.splitlines() if (s := ln.strip())
             and not _CODEX_NOISE.match(s)]
    return "\n".join(lines).strip()


def read(prompt: str, timeout: int = 150) -> str:
    """Call the configured reader. One slow/failed call returns "" (a wrong-but-
    scoreable prediction) instead of crashing the batch — feedback_batch_llm_resilience."""
    kind = _READER or (("ollama", "llama3.1:latest") if shutil.which("ollama") else ("cc",))
    try:
        if kind[0] == "ollama":
            return _ollama_generate(kind[1], prompt, 0.0, timeout)
        if kind[0] == "codex":
            # raw binary (not the ~/bin wrapper — that fires qmd/vault-writeback overhead)
            r = subprocess.run([_CODEX, "exec", "--sandbox", "read-only",
                                "--skip-git-repo-check", prompt],
                               capture_output=True, text=True,
                               timeout=max(timeout, 180), stdin=subprocess.DEVNULL)
            return _parse_codex(r.stdout)
        if kind[0] == "cmd":
            r = subprocess.run(kind[1].split(), input=prompt, capture_output=True,
                               text=True, timeout=timeout)
            return (r.stdout or "").strip()
        r = subprocess.run([_CC, "-p", prompt, "--output-format", "json"],
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        try:
            return json.loads(r.stdout).get("result", "").strip()
        except Exception:
            return (r.stdout or "").strip()
    except Exception:
        return ""


def judge_correct(question: str, gold: str, pred: str) -> bool:
    """LLM-judge if configured (more accurate than string inclusion), else fall back
    to normalized-inclusion. Judge runs at temp 0 for reproducibility."""
    if not _JUDGE:
        return scores_correct(pred, gold)
    if not pred.strip():
        return False
    prompt = ("You are grading a memory system's answer.\n"
              "- If GOLD states a fact, the PREDICTION is correct when it conveys the same "
              "fact (ignore wording/format).\n"
              "- If GOLD is a PREFERENCE/RUBRIC (e.g. 'the user would prefer suggestions that "
              "take X into account'), the PREDICTION is correct when it plausibly satisfies "
              "that preference/rubric.\n"
              "Reply with exactly YES or NO.\n\n"
              f"QUESTION: {question}\nGOLD: {gold}\nPREDICTED: {pred}\nCorrect?")
    try:
        v = _ollama_generate(_JUDGE[1], prompt, 0.0, 60).strip().upper()
        return v.startswith("Y")
    except Exception:
        return scores_correct(pred, gold)


# -------------------------------------------------------------------------- score
def _norm(s) -> str:
    # gold answers are sometimes ints/floats (counts, durations) — coerce to str.
    return _norm_re.sub(" ", str(s if s is not None else "").lower()).strip()


_COUNT_Q_RE = re.compile(r"\bhow many\b|\btotal number\b", re.I)


def _is_count_q(q: str) -> bool:
    return bool(_COUNT_Q_RE.search(q))


def deterministic_count(q: str, context: str) -> str | None:
    """Tier-2 scaffold: the LLM is reliable at IDENTIFYING items, unreliable at
    COUNTING them ('4 vs 5'). So separate the two — have it enumerate the qualifying
    items one per line, then COUNT the lines in code. Returns the count as a string,
    'not enough information' if none, or None to fall back to the normal reader."""
    prompt = (
        "From the CONTEXT, list EVERY distinct item the question asks about, ONE per line "
        "starting with '- '. Use ONLY the context; do not invent or infer beyond it. "
        "If the context contains none, write exactly 'NONE'.\n\n"
        f"CONTEXT:\n{context}\n\nQUESTION: {q}\nLIST:")
    raw = read(prompt)
    if not raw:
        return None
    items = [ln for ln in raw.splitlines() if ln.strip().startswith("-") and len(ln.strip()) > 1]
    if not items:
        return "not enough information" if "NONE" in raw.upper() else None
    return str(len(items))


def _est_tokens(text: str) -> int:
    """Cheap, consistent token estimate (~4 chars/token). We report tokens-per-query
    so 'best AND cheapest' is a measured claim, not a slogan."""
    return (len(text) + 3) // 4


def _span(text: str, query: str, max_chars: int = 1000) -> str:
    """Precise-span extraction: return only the turns relevant to the query (plus a
    neighbor for context), not the whole session. Cuts reader tokens ~10x AND reduces
    noise — the recall→QA gap is the reader drowning in full transcripts."""
    terms = {w for w in _WORD.findall(query.lower()) if len(w) > 2}
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not terms or not lines:
        return text[:max_chars]
    scores = [sum(ln.lower().count(t) for t in terms) for ln in lines]
    if not any(scores):
        return text[:max_chars]
    keep = set()
    for i, s in enumerate(scores):
        if s:
            keep.update({i - 1, i, i + 1})           # matched turn + neighbors
    picked = sorted(x for x in keep if 0 <= x < len(lines))
    out, total = [], 0
    for i in picked:
        if total + len(lines[i]) + 1 > max_chars:
            break
        out.append(lines[i]); total += len(lines[i]) + 1
    return "\n".join(out) or text[:max_chars]


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
def _evidence_indices(item: dict) -> set[int]:
    """Which session indices hold the answer (from answer_session_ids) — the ground
    truth for retrieval recall, computed with NO reader (reader-independent)."""
    ans = set(item.get("answer_session_ids") or [])
    hay = item.get("haystack_session_ids") or []
    return {i for i, sid in enumerate(hay) if sid in ans}


def run_question(item: dict, mode: str, k: int = 6) -> dict:
    # finer chunks = more, smaller notes per session, so retrieve more of them
    if _CHUNK == "turns":
        k = 12
    sessions, _ = _sessions_and_dates(item)
    q = item["question"]
    gold = item.get("answer", "")
    evidence = _evidence_indices(item)
    with tempfile.TemporaryDirectory(prefix="lme-") as d:
        root = Path(d)
        build_question_vault(item, root)
        rot = verify.verify_vault(root)  # RotBench on the constructed vault
        ing = index.ingest(root)
        hits = index.search(q, root, k=k * 2 if mode == "b" else k)
        if mode == "b" and hits:
            # temporal rerank: keep qmd RELEVANCE dominant, add a light recency BOOST.
            # (A naive newest-first sort destroys relevance and wrecks "which came
            # first"/ordering questions — it only helps knowledge-update.)
            ranked = sorted({_note_date(h["rel"], root) for h in hits if _note_date(h["rel"], root)})
            rank_of = {d: i for i, d in enumerate(ranked)}
            span = max(1, len(ranked) - 1)
            for h in hits:
                rec = rank_of.get(_note_date(h["rel"], root), 0) / span if ranked else 0.0
                h["_b"] = (h["score"] or 0.0) + 0.15 * rec
            hits.sort(key=lambda h: h["_b"], reverse=True)
        hits = hits[:k]
        # Reader-independent retrieval recall: did top-k include an evidence session?
        retrieved_idx = set()
        for h in hits:
            m = re.search(r"session-(\d+)", h["rel"])
            if m:
                retrieved_idx.add(int(m.group(1)))
        recall_hit = (not evidence) or bool(evidence & retrieved_idx)
        # Feed the reader the FULL retrieved note body (the memory the system stored),
        # not qmd's ~350-char snippet — on LongMemEval the answer is often a single
        # sentence buried in a long session, which a truncated snippet drops. Cap per
        # note so k notes fit a reasonable reader window.
        def _body(h):
            f = _resolve_note(root, h["rel"])
            if f is None:
                return h["snippet"]  # last resort; _resolve_note should always hit
            txt = f.read_text(errors="replace").split("---", 2)[-1].strip()  # drop frontmatter
            # 'span' hands the reader only the relevant turns (cheap + low-noise);
            # 'full' hands the whole session (accurate but token-heavy).
            return _span(txt, q, 1000) if _CONTEXT == "span" else txt[:8000]
        context = "\n\n".join(
            f"[{h['title']} · {_note_date(h['rel'], root)}]\n{_body(h)}" for h in hits)
        ctx_tokens = _est_tokens(context)
        # Question-type-adaptive prompting — the failures are arithmetic/date-math, not
        # retrieval. Give the reader the CURRENT DATE (LongMemEval question_date; the
        # reader can't do "how many days ago" without it) + a reasoning scaffold per type.
        # NOTE: qtype here stands in for a query router's classification (see the
        # query-type router work); using the label measures the technique's ceiling.
        qtype = item.get("question_type", "")
        qdate = item.get("question_date", "") or "unknown"
        # A good memory knows what it DOESN'T know — LongMemEval includes abstention
        # questions where the right answer is "not enough information". Forcing a guess
        # tanks those (verify-don't-hope applied to reading), so every prompt permits it.
        ABSTAIN = ("If the context does not actually contain enough information to answer, "
                   "respond exactly 'not enough information' — do NOT guess.")
        if qtype == "temporal-reasoning":
            instr = ("Reason step by step: list the relevant events WITH their dates from the "
                     "context, then compute the answer relative to CURRENT DATE. " + ABSTAIN +
                     " End with the final answer on a line starting 'ANSWER:'.")
        elif qtype == "multi-session":
            instr = ("The question may ask 'how many' or a total. Find every relevant item across "
                     "the context, list them, then count or sum ONLY items actually present. " +
                     ABSTAIN + " End with the final answer on a line starting 'ANSWER:'.")
        elif qtype == "knowledge-update":
            instr = ("Several values may appear over time; use the MOST RECENT (latest date). " +
                     ABSTAIN + " End with the final answer on a line starting 'ANSWER:'.")
        elif qtype == "single-session-preference":
            # Preference/advice is open-ended — there's always enough to give a
            # preference-aware suggestion, so NO abstention (it over-triggers here).
            instr = ("Give a helpful suggestion that reflects the user's stated preferences and "
                     "situation from the context. End with the final answer on a line starting 'ANSWER:'.")
        else:
            instr = ("Answer in a few words using ONLY the context. " + ABSTAIN +
                     " End with the final answer on a line starting 'ANSWER:'.")
        prompt = (f"{instr}\nCURRENT DATE: {qdate}\n\n"
                  f"CONTEXT:\n{context}\n\nQUESTION: {q}")
        pred = None
        if _DETERMINISTIC and _is_count_q(q):
            # Tier 2: enumerate-then-count in code for "how many" questions.
            pred = deterministic_count(q, context)
        if pred is None:
            raw = read(prompt)
            m = re.search(r"ANSWER:\s*(.+)", raw, re.I | re.S)  # final answer if it showed work
            pred = (m.group(1).strip() if m else raw).strip()[:300]
        # cleanup the transient qmd collection
        try:
            subprocess.run([shutil.which("qmd") or "qmd", "collection", "remove",
                            index.collection_name(root), "--index", index.QMD_INDEX],
                           capture_output=True, timeout=30)
        except Exception:
            pass
    return {"id": item.get("question_id"), "type": item.get("question_type"),
            "q": q, "gold": gold, "pred": pred,
            "correct": judge_correct(q, gold, pred), "rot": rot["score"],
            "recall": recall_hit, "ctx_tokens": ctx_tokens, "engine": ing.get("engine")}


def run(data: list[dict], modes: list[str], n: int | None,
        checkpoint: Path | None = None) -> dict:
    items = data[:n] if n else data
    results = {m: [] for m in modes}
    for m in modes:
        for it in items:
            # Batch-level resilience: NOTHING a single question does can kill the run
            # (feedback_batch_llm_resilience). A failed question scores as incorrect.
            try:
                res = run_question(it, m)
            except Exception as e:
                res = {"id": it.get("question_id"), "type": it.get("question_type"),
                       "q": it.get("question", ""), "gold": it.get("answer", ""),
                       "pred": "", "correct": False, "rot": None,
                       "engine": None, "error": f"{type(e).__name__}: {e}"}
            results[m].append(res)
            mark = "✓" if res["correct"] else ("!" if res.get("error") else "✗")
            print(f"  [{m.upper()}] {mark} {res['type']:<22} q={res['q'][:48]!r} "
                  f"pred={res['pred'][:40]!r}", flush=True)
            if checkpoint:  # persist after every question — a crash never wastes the run
                checkpoint.write_text(json.dumps(results))
    summary = {}
    for m in modes:
        rs = results[m]
        n = len(rs) or 1
        acc = sum(r["correct"] for r in rs) / n
        rec = sum(r.get("recall") for r in rs) / n
        toks = [r.get("ctx_tokens", 0) for r in rs]
        rot_vals = [r["rot"] for r in rs if r.get("rot") is not None]
        summary[m] = {"n": len(rs), "accuracy": round(100 * acc, 1),
                      "recall_at_k": round(100 * rec, 1),
                      "avg_ctx_tokens": round(sum(toks) / n),
                      "avg_rotbench": round(sum(rot_vals) / len(rot_vals), 1) if rot_vals else 0}
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
    ap.add_argument("--reader", default=None,
                    help="ollama:<model> (temp-0, reliable) | cc | else $FBT_READER")
    ap.add_argument("--judge", default=None,
                    help="ollama:<model> to LLM-judge correctness (else normalized-inclusion)")
    ap.add_argument("--checkpoint", default=None,
                    help="write partial results JSON after every question (crash-safe)")
    ap.add_argument("--context", choices=["full", "span"], default="full",
                    help="full session (accurate, token-heavy) vs precise span (cheap, low-noise)")
    ap.add_argument("--deterministic", action="store_true",
                    help="Tier 2: enumerate-then-count in code for 'how many' questions")
    ap.add_argument("--chunk", choices=["session", "turns"], default="session",
                    help="one note per session vs finer turn-windows (MemPalace recall trick)")
    args = ap.parse_args(argv)

    global _READER, _JUDGE, _CONTEXT, _DETERMINISTIC, _CHUNK
    _CONTEXT = args.context
    _DETERMINISTIC = args.deterministic
    _CHUNK = args.chunk
    if args.reader:
        _READER = (("ollama", args.reader[7:]) if args.reader.startswith("ollama:")
                   else ("codex",) if args.reader == "codex"
                   else ("cc",) if args.reader == "cc" else ("cmd", args.reader))
    elif os.environ.get("FBT_READER"):
        _READER = ("cmd", os.environ["FBT_READER"])
    if args.judge:
        _JUDGE = ("ollama", args.judge[7:] if args.judge.startswith("ollama:") else args.judge)

    data = SYNTHETIC if args.synthetic else load_dataset(args.data)
    if args.shuffle:
        import random
        random.Random(args.seed).shuffle(data)
    modes = ["a", "b"] if args.mode == "both" else [args.mode]
    print(f"LongMemEval — {'synthetic' if args.synthetic else args.data} · "
          f"{args.n or len(data)} questions · modes={modes} · "
          f"reader={_READER} judge={_JUDGE}\n")
    ckpt = Path(args.checkpoint) if args.checkpoint else None
    out = run(data, modes, args.n, checkpoint=ckpt)
    print("\n=== RESULTS ===")
    for m, s in out["summary"].items():
        label = "A (qmd baseline)" if m == "a" else "B (qmd + temporal rerank)"
        print(f"  {label}:  QA {s['accuracy']}%  ·  recall@k {s['recall_at_k']}%  ·  "
              f"~{s['avg_ctx_tokens']} ctx tokens/q  (n={s['n']}, RotBench {s['avg_rotbench']}/100)")
    if "a" in out["summary"] and "b" in out["summary"]:
        delta = out["summary"]["b"]["accuracy"] - out["summary"]["a"]["accuracy"]
        print(f"  A→B temporal delta:  {delta:+.1f} points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
