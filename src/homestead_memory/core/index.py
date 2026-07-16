#!/usr/bin/env python3
"""
core.index — retrieval. A thin wrapper over `qmd` (Tobi Lütke's MIT hybrid
BM25 + vector search), with a dependency-free direct-scan fallback so memory
survives the index being down.

qmd is an OPTIONAL external dependency (a CLI), never vendored. Homestead owns an
explicit INDEX_PATH and QMD_CONFIG_DIR, so it cannot mutate the user's default qmd
setup. Retrieval prefers a long-lived loopback MCP process, then the dedicated qmd
CLI, then a dependency-free scan over the markdown.

`ask()` does parent-document retrieval at read time: qmd finds the right note, we
resolve its FULL body and hand the reader the query-relevant chunks (via core.chunking)
instead of qmd's ~350-char snippet, with per-question-type prompting + abstention and
a light recency rerank for time-sensitive questions. These techniques were proven in
the LongMemEval harness first; this is their production home.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from . import chunking
from . import qmd_runtime
from . import store
from . import telemetry
from . import tuning
from . import vault as vaultlib


def _find_qmd() -> str | None:
    """Select a compatible qmd even when an older install appears first on PATH."""
    override = os.environ.get("HSM_QMD_BIN")
    candidates: list[str] = [override] if override else []
    discovered = shutil.which("qmd")
    if discovered:
        candidates.append(discovered)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(str(Path(directory).expanduser() / "qmd"))
    candidates.extend(str(path) for path in sorted(
        (Path.home() / ".nvm/versions/node").glob("*/bin/qmd"), reverse=True))
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not os.access(candidate, os.X_OK):
            continue
        seen.add(candidate)
        if qmd_runtime.compatible(candidate):
            return candidate
    return None


_QMD = _find_qmd()
_WORD = re.compile(r"[a-z0-9]{3,}")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_KNOWN_TYPES = {"temporal-reasoning", "knowledge-update", "multi-session", "default"}
_RETRIEVAL_MODES = {"fast", "balanced", "quality"}


def qmd_available() -> bool:
    return qmd_runtime.compatible(_QMD)


def collection_name(vault: Path) -> str:
    """Stable per-vault collection name derived from the absolute path."""
    h = hashlib.sha1(str(vault.resolve()).encode()).hexdigest()[:10]
    return f"fbt_{h}"


def _vault_content_hash(vault: Path) -> str:
    """A stable digest of every note's (relpath, bytes). Lets `verify --deep` detect
    that the vault changed since the last ingest — otherwise qmd can ghost-match stale
    embeddings against content that has since been edited."""
    h = hashlib.sha1()
    for p, rel in sorted(vaultlib.iter_notes(vault), key=lambda t: t[1].as_posix()):
        h.update(rel.as_posix().encode() + b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    return h.hexdigest()


def _qmd(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([_QMD, *args], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL,
                          env=qmd_runtime.environment(qmd_bin=_QMD))


def _collection_exists(name: str) -> bool:
    try:
        r = _qmd("collection", "list", timeout=30)
        return name in (r.stdout or "")
    except Exception:
        return False


def ingest(vault: Path | str | None = None) -> dict:
    """Index the vault with qmd (add-or-update the collection, then embed)."""
    v = vaultlib._resolve(vault)
    if not qmd_available():
        return {"ok": False, "engine": "none",
                "note": "qmd 2.1+ is required; `ask` is using direct-scan fallback. "
                        "Install qmd: npm install -g @tobilu/qmd@2.1.0"}
    name = collection_name(v)
    runtime_status = qmd_runtime.status()
    was_running = runtime_status["ok"]
    marker = qmd_runtime.ensure_dirs()["maintenance"]
    marker.write_text(json.dumps({"operation": "ingest", "started_at": time.time()}))
    if was_running:
        qmd_runtime.stop()
    try:
        if _collection_exists(name):
            update = _qmd("update")
        else:
            update = _qmd("collection", "add", str(v), "--name", name,
                          "--mask", "**/*.md")
        if update.returncode != 0:
            return {"ok": False, "engine": "qmd-cli", "collection": name,
                    "reason": "qmd_update_failed",
                    "note": (update.stderr or update.stdout or "").strip()[-1000:]}
        emb = _qmd("embed", "--max-docs-per-batch", "100", "--max-batch-mb", "64")
    finally:
        marker.unlink(missing_ok=True)
        if was_running:
            qmd_runtime.start(_QMD)
    if emb.returncode == 0:                # record the content hash ONLY on a clean embed, so
        try:                              # a failed/stale index doesn't suppress index_drift
            state = v / ".hsm"
            store.atomic_write(
                state / "ingest.json",
                json.dumps({"content_hash": _vault_content_hash(v), "collection": name,
                            "at": date.today().isoformat()}))
        except OSError:
            pass
    return {"ok": emb.returncode == 0, "engine": "qmd", "collection": name,
            "embed_tail": (emb.stdout or emb.stderr or "").strip().splitlines()[-1:]}


def _strip_qmd_uri(file_uri: str, name: str) -> str:
    # "qmd://<collection>/<relpath>" -> "<relpath>"
    prefix = f"qmd://{name}/"
    if file_uri.startswith(prefix):
        return file_uri[len(prefix):]
    plain = file_uri.replace("qmd://", "", 1)
    return plain[len(name) + 1:] if plain.startswith(f"{name}/") else plain


def _one_line(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _index_age_seconds() -> float | None:
    index_path = qmd_runtime.paths()["index"]
    try:
        return round(max(0.0, time.time() - index_path.stat().st_mtime), 1)
    except OSError:
        return None


def _request_json(url: str, payload: dict, timeout: float,
                  session: str | None = None) -> tuple[dict, str | None]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers,
                                     method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return (json.loads(raw) if raw else {}), response.headers.get("Mcp-Session-Id") or session


def _mcp_search(query: str, name: str, k: int, mode: str) -> list[dict]:
    """Run one stateless client session against qmd's long-lived MCP store."""
    timeout = 120.0 if mode == "quality" else 3.5
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "homestead-memory", "version": "0.2.2"}}}
    session = None
    try:
        _, session = _request_json(qmd_runtime.endpoint(), init, timeout)
        if not session:
            raise RuntimeError("qmd MCP did not establish a session")
        _request_json(qmd_runtime.endpoint(),
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      timeout, session)
        clean = _one_line(query)
        arguments = {
            "searches": [{"type": "lex", "query": clean}, {"type": "vec", "query": clean}],
            "limit": k, "collections": [name], "rerank": mode == "quality",
        }
        result, _ = _request_json(qmd_runtime.endpoint(), {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "query", "arguments": arguments}}, timeout, session)
        if "error" in result:
            raise RuntimeError(result["error"].get("message", "qmd MCP query failed"))
        return result.get("result", {}).get("structuredContent", {}).get("results", [])
    finally:
        if session:
            request = urllib.request.Request(
                qmd_runtime.endpoint(), headers={"Mcp-Session-Id": session}, method="DELETE")
            try:
                urllib.request.urlopen(request, timeout=1).close()
            except (OSError, urllib.error.URLError):
                pass


def _cli_search(query: str, name: str, k: int, mode: str) -> list[dict]:
    clean = _one_line(query)
    if mode == "fast":
        args = ["search", clean]
        timeout = 5
    else:
        args = ["query", f"lex: {clean}\nvec: {clean}"]
        if mode == "balanced":
            args.append("--no-rerank")
        timeout = 120 if mode == "quality" else 5
    run = _qmd(*args, "-c", name, "--json", "-n", str(k), timeout=timeout)
    if run.returncode != 0:
        raise RuntimeError((run.stderr or run.stdout or "qmd failed").strip()[-1000:])
    return json.loads(run.stdout) if run.stdout.strip().startswith("[") else []


def _normalize_qmd_results(data: list[dict], vault: Path, name: str, engine: str,
                           mode: str, degraded: bool, reason: str | None) -> list[dict]:
    out = []
    for item in data:
        rel = _strip_qmd_uri(str(item.get("file", "")), name)
        resolved = _resolve_note(vault, rel)
        if resolved is not None:
            rel = resolved.relative_to(vault.resolve()).as_posix()
        out.append({"rel": rel, "path": str(resolved or (vault / rel)), "score": item.get("score"),
                    "title": item.get("title", ""), "snippet": item.get("snippet", ""),
                    "engine": engine, "retrieval_mode": mode, "degraded": degraded,
                    "reason": reason})
    return out


def search_report(query: str, vault: Path | str | None = None, k: int = 5,
                  retrieval_mode: str = "balanced") -> dict:
    """Return hits plus explicit engine, latency, index age, and degradation state."""
    started = time.monotonic()
    v = vaultlib._resolve(vault)
    mode = retrieval_mode if retrieval_mode in _RETRIEVAL_MODES else "balanced"
    name = collection_name(v)
    errors: list[str] = []
    if qmd_runtime.maintenance_active():
        errors.append("maintenance_active")
    elif qmd_available():
        if mode != "fast":
            try:
                data = _mcp_search(query, name, k, mode)
                hits = _normalize_qmd_results(data, v, name, "qmd-mcp", mode, False, None)
                if hits:
                    return _search_result(hits, "qmd-mcp", mode, False, None, started)
                errors.append("mcp_no_hits")
            except Exception as exc:
                errors.append(f"mcp_failed:{type(exc).__name__}")
        name = collection_name(v)
        try:
            data = _cli_search(query, name, k, mode)
            degraded = bool(errors)
            reason = ";".join(errors) or None
            hits = _normalize_qmd_results(data, v, name, "qmd-cli", mode, degraded, reason)
            if hits:
                return _search_result(hits, "qmd-cli", mode, degraded, reason, started)
            errors.append("cli_no_hits")
        except Exception as exc:
            errors.append(f"cli_failed:{type(exc).__name__}")
    else:
        errors.append("qmd_2_1_unavailable")
    reason = ";".join(errors)
    hits = _direct_scan(query, v, k)
    for hit in hits:
        hit.update({"retrieval_mode": mode, "degraded": True, "reason": reason})
    return _search_result(hits, "direct-scan", mode, True, reason, started)


def _search_result(hits: list[dict], engine: str, mode: str, degraded: bool,
                   reason: str | None, started: float) -> dict:
    return {"hits": hits, "engine": engine, "retrieval_mode": mode,
            "degraded": degraded, "reason": reason,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "index_age_seconds": _index_age_seconds()}


def search(query: str, vault: Path | str | None = None, k: int = 5,
           retrieval_mode: str = "balanced") -> list[dict]:
    """Compatibility surface returning ranked passages only."""
    return search_report(query, vault, k, retrieval_mode)["hits"]


def _direct_scan(query: str, vault: Path, k: int) -> list[dict]:
    """Dependency-free fallback: keyword-overlap ranking over the markdown."""
    terms = {w for w in _WORD.findall(query.lower())}
    scored = []
    for p, rel in vaultlib.iter_notes(vault):
        txt = p.read_text(errors="replace")
        low = txt.lower()
        score = sum(low.count(t) for t in terms)
        if score:
            # a tiny snippet around the first hit
            idx = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
            snippet = txt[max(0, idx - 60): idx + 160].replace("\n", " ")
            scored.append({"rel": rel.as_posix(), "path": str(p), "score": float(score),
                           "title": rel.stem, "snippet": snippet, "engine": "direct-scan"})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


# ------------------------------------------------------------------ read-time helpers
def _resolve_note(vault: Path, rel: str) -> Path | None:
    """Map a retrieval rel-path back to the real file, tolerating qmd's '_'<->'-' URI
    normalization (else a full-body read silently fails and we'd fall back to the
    truncated snippet — the 44-point recall cliff from RESULTS.md run 6).

    Every candidate is confined to the vault: a crafted/corrupted qmd rel like
    '../private.md' or an absolute path resolves OUTSIDE the root and is rejected, so
    /ask can never leak a sibling file. The '_'<->'-' fallback preserves the note's
    subdirectory (a top-level-only glob would silently drop nested notes to a snippet)."""
    if not rel:
        return None
    root = vault.resolve()
    relp = Path(rel)
    if relp.is_absolute() or ".." in relp.parts:
        return None

    def _within(f: Path) -> Path | None:
        try:
            r = f.resolve()
        except OSError:
            return None
        return r if (r.is_file() and root in r.parents) else None

    hit = _within(vault / relp)
    if hit:
        return hit

    def _key(value: str) -> str:
        return value.casefold().replace("_", "-")

    base = root
    for part in relp.parent.parts:
        try:
            directories = [entry for entry in base.iterdir() if entry.is_dir()]
        except OSError:
            return None
        match = next((entry for entry in directories if entry.name == part), None)
        if match is None:
            match = next((entry for entry in directories if _key(entry.name) == _key(part)), None)
        if match is None:
            return None
        base = match

    stem = relp.stem
    try:
        for candidate in base.iterdir():
            if candidate.is_file() and candidate.suffix.casefold() == ".md" and \
                    _key(candidate.stem) == _key(stem):
                hit = _within(candidate)
                if hit:
                    return hit
    except OSError:
        return None
    return None


def _date_from_text(text: str) -> str:
    """A note's own date: frontmatter `updated:`/`date:` if present, else the first
    date-looking token in the body, else '' (unknown)."""
    fm = vaultlib.parse_frontmatter(text)
    if fm:
        for key in ("updated", "date"):
            m = _DATE_RE.search(str(fm["fields"].get(key, "")))
            if m:
                return m.group(1)
    m = _DATE_RE.search(text)
    return m.group(1) if m else ""


def _note_date(vault: Path, rel: str) -> str:
    """Resolve a rel-path to its file and read its date (for recency ranking)."""
    f = _resolve_note(vault, rel)
    if f is None:
        return ""
    try:
        return _date_from_text(f.read_text(errors="replace"))
    except OSError:
        return ""


def classify_question(q: str) -> str:
    """Heuristic question-type router (regex/keyword, no model call). Types mirror the
    LongMemEval scaffolds ported below. Order matters: date-math before enumeration,
    'current/latest' before generic counting."""
    ql = q.lower()
    if re.search(r"how (many|much) (days?|weeks?|months?|years?|hours?|minutes?)", ql) \
            or re.search(r"\b(how long ago|days? ago|weeks? ago|months? ago|how long since)\b", ql):
        return "temporal-reasoning"
    if re.search(r"\b(currently|current|nowadays|these days|as of now|right now|latest|still|anymore)\b", ql):
        return "knowledge-update"
    if re.search(r"\b(how many|how much|number of|total|count|list all|all the)\b", ql):
        return "multi-session"
    if re.search(r"\b(when did|when was|how long|before|after|first|last|most recent|earliest)\b", ql):
        return "temporal-reasoning"
    return "default"


_ABSTAIN = ("If the context does not contain enough information to answer, respond "
            "exactly 'not enough information'. Do NOT guess.")


def _instr_for(qtype: str) -> str:
    """Per-question-type reader instruction (ported from benchmarks/longmemeval.py).
    Every type permits abstention (verify-don't-hope, applied to reading) except
    open-ended preference advice, which the heuristic router never emits."""
    if qtype == "temporal-reasoning":
        return ("Reason step by step: list the relevant events WITH their dates from the "
                "context, then compute the answer. " + _ABSTAIN +
                " End with the final answer on a line starting 'ANSWER:'.")
    if qtype == "multi-session":
        return ("Find every relevant item across the context, list them, then count or sum "
                "ONLY items actually present. " + _ABSTAIN +
                " End with the final answer on a line starting 'ANSWER:'.")
    if qtype == "knowledge-update":
        return ("Several values may appear over time; use the MOST RECENT (latest date). " +
                _ABSTAIN + " End with the final answer on a line starting 'ANSWER:'.")
    return ("Answer concisely using ONLY the context. " + _ABSTAIN +
            " End with the final answer on a line starting 'ANSWER:'.")


def _recency_rerank(hits: list[dict], vault: Path) -> list[dict]:
    """Keep qmd RELEVANCE dominant, add a light recency BOOST. A naive newest-first
    sort destroys relevance and wrecks ordering questions, so this only nudges."""
    dates = sorted({d for d in (_note_date(vault, h["rel"]) for h in hits) if d})
    if not dates:
        return hits
    rank = {d: i for i, d in enumerate(dates)}
    span = max(1, len(dates) - 1)
    scored = []
    for h in hits:
        rec = rank.get(_note_date(vault, h["rel"]), 0) / span
        scored.append(((h.get("score") or 0.0) + 0.15 * rec, h))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [h for _, h in scored]


def _assemble_context(query: str, hits: list[dict], vault: Path, token_budget: int) -> str:
    """Parent-document retrieval at read time: resolve each retrieved note to its FULL
    body, keep the query-relevant chunks (core.chunking), assemble within a token
    budget. Dedupe by parent note (qmd can return several passages from one note);
    fall back to the hit snippet if a note can't be resolved."""
    char_budget = max(1000, token_budget * 4)   # ~4 chars/token
    # Dedupe to distinct parent notes FIRST (qmd can return several passages from one
    # note), so the per-note budget reflects the real number of notes, not the hit
    # count — else 5 duplicate hits would starve the one real note to a fifth of budget.
    unique: list[tuple[dict, "Path | None"]] = []
    seen: set = set()
    for h in hits:
        f = _resolve_note(vault, h["rel"])
        key = str(f) if f else h["rel"]
        if key in seen:
            continue
        seen.add(key)
        unique.append((h, f))
    if not unique:
        return ""
    per_note = max(600, char_budget // len(unique))
    parts: list[str] = []
    used = 0
    for h, f in unique:
        remaining = char_budget - used
        if parts and remaining <= 200:
            break
        rel = h["rel"]
        title = h.get("title") or (f.stem if f else Path(rel).stem)
        if f is None:
            date = ""
            body = (h.get("snippet") or "").strip()
        else:
            try:
                text = f.read_text(errors="replace")
            except OSError:
                text = ""
            date = _date_from_text(text)
            cap = max(200, min(per_note, remaining))
            body = chunking.relevant_window(text, query, max_chars=cap) \
                or (h.get("snippet") or "").strip()
        if not body:
            continue
        head = f"[{title} · {date}]" if date else f"[{title}]"
        block = f"{head}\n{body}"
        if parts and used + len(block) > char_budget:
            continue   # too big to fit now; a later, smaller hit may still fit
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)


def _est_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — for honest reporting, not billing."""
    return (len(text) + 3) // 4


def ask(query: str, vault: Path | str | None = None, k: int | None = None,
        question_type: str | None = None, token_budget: int = 6000,
        retrieval_mode: str = "balanced") -> dict:
    """Retrieve, then synthesize an answer with a reader if one is configured
    (env HSM_READER, a shell command that reads the prompt on stdin); otherwise return
    the assembled context as the answer material.

    k: passages to retrieve; None uses the tuned breadth for this vault (`hsm tune`),
    else 5. question_type: 'temporal-reasoning' | 'knowledge-update' | 'multi-session' |
    'default'. If None, a heuristic router classifies the query. token_budget caps the
    assembled context (~4 chars/token)."""
    v = vaultlib._resolve(vault)
    if k is None:                          # unset → the tuned breadth for THIS vault, else 5
        k = tuning.tuned_k(v)              # validated + clamped (a hand-edited tuning.json is safe)
    retrieval = search_report(query, v, k, retrieval_mode)
    hits = retrieval["hits"]
    qtype = question_type or classify_question(query)
    if qtype not in _KNOWN_TYPES:      # any bad input (CLI/API/MCP) → safe default
        qtype = "default"
    # Recency-boost only knowledge-update, which unambiguously wants the LATEST value.
    # Temporal questions include 'first/earliest', where boosting newest is exactly
    # backwards (and under a tight budget would drop the oldest, answer-bearing note),
    # so they rely on the dated context + CURRENT DATE scaffold, not a recency rerank.
    if qtype == "knowledge-update" and hits:
        hits = _recency_rerank(hits, v)
    context = _assemble_context(query, hits, v, token_budget)
    reader = os.environ.get("HSM_READER") or os.environ.get("FBT_READER")
    answer = None
    if reader and hits and context:
        instr = _instr_for(qtype)
        # 'how many days ago' is uncomputable without today's date; the harness passed
        # LongMemEval's question_date, production uses the real current date.
        header = f"CURRENT DATE: {date.today().isoformat()}\n" if qtype == "temporal-reasoning" else ""
        prompt = f"{instr}\n{header}\nCONTEXT:\n{context}\n\nQUESTION: {query}"
        try:
            r = subprocess.run(reader.split(), input=prompt, capture_output=True,
                               text=True, timeout=180)
            raw = (r.stdout or "").strip()
            # Take the text after the LAST line-anchored 'ANSWER:' sentinel. The per-type
            # prompts tell the model to show its work first, so an unanchored/first-match
            # regex would capture the reasoning ('to answer: ...') instead of the answer.
            marks = list(re.finditer(r"(?im)^[ \t]*ANSWER:[ \t]*", raw))
            answer = ((raw[marks[-1].end():].strip() if marks else raw) or None)
        except Exception:
            answer = None
    telemetry.log(v, {"type": "ask",
                      "query_hash": hashlib.sha1(query.encode("utf-8")).hexdigest()[:16],
                      "question_type": qtype, "k": k, "n_hits": len(hits),
                      "answered": answer is not None,
                      "top": hits[0]["rel"] if hits else None,
                      "context_tokens": _est_tokens(context),
                      "engine": retrieval["engine"],
                      "retrieval_mode": retrieval["retrieval_mode"],
                      "degraded": retrieval["degraded"],
                      "elapsed_ms": retrieval["elapsed_ms"]})
    return {"query": query, "hits": hits, "context": context, "answer": answer,
            "question_type": qtype, "context_tokens": _est_tokens(context),
            "engine": retrieval["engine"],
            "retrieval_mode": retrieval["retrieval_mode"],
            "degraded": retrieval["degraded"], "reason": retrieval["reason"],
            "elapsed_ms": retrieval["elapsed_ms"],
            "index_age_seconds": retrieval["index_age_seconds"]}
