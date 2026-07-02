#!/usr/bin/env python3
"""
core.index — retrieval. A thin wrapper over `qmd` (Tobi Lütke's MIT hybrid
BM25 + vector search), with a dependency-free direct-scan fallback so memory
survives the index being down.

qmd is an OPTIONAL external dependency (a CLI), never vendored. fbt-memory keeps
its data in an isolated qmd index (`--index fbt-memory`) so it never touches the
user's default qmd setup. If qmd isn't installed, `search` degrades to a keyword
scan over the markdown — retrieval still works, just without hybrid ranking.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import vault as vaultlib

QMD_INDEX = "fbt-memory"        # isolated index — never the user's default
_QMD = shutil.which("qmd")
_WORD = re.compile(r"[a-z0-9]{3,}")


def qmd_available() -> bool:
    return _QMD is not None


def collection_name(vault: Path) -> str:
    """Stable per-vault collection name derived from the absolute path."""
    h = hashlib.sha1(str(vault.resolve()).encode()).hexdigest()[:10]
    return f"fbt_{h}"


def _qmd(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([_QMD, *args], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def _collection_exists(name: str) -> bool:
    try:
        r = _qmd("collection", "list", "--index", QMD_INDEX, timeout=30)
        return name in (r.stdout or "")
    except Exception:
        return False


def ingest(vault: Path | str | None = None) -> dict:
    """Index the vault with qmd (add-or-update the collection, then embed)."""
    v = vaultlib._resolve(vault)
    if not qmd_available():
        return {"ok": False, "engine": "none",
                "note": "qmd not installed — `ask` will use the direct-scan fallback. "
                        "Install qmd for hybrid retrieval: https://github.com/tobi/qmd"}
    name = collection_name(v)
    if _collection_exists(name):
        _qmd("update", "--index", QMD_INDEX)
    else:
        r = _qmd("collection", "add", str(v), "--name", name,
                 "--mask", "**/*.md", "--index", QMD_INDEX)
        if r.returncode != 0:
            _qmd("update", "--index", QMD_INDEX)   # fall back to update if add balked
    emb = _qmd("embed", "--index", QMD_INDEX)
    return {"ok": emb.returncode == 0, "engine": "qmd", "collection": name,
            "embed_tail": (emb.stdout or emb.stderr or "").strip().splitlines()[-1:]}


def _strip_qmd_uri(file_uri: str, name: str) -> str:
    # "qmd://<collection>/<relpath>" -> "<relpath>"
    prefix = f"qmd://{name}/"
    return file_uri[len(prefix):] if file_uri.startswith(prefix) else file_uri.replace("qmd://", "")


def search(query: str, vault: Path | str | None = None, k: int = 5) -> list[dict]:
    """Return ranked passages. qmd hybrid if available, else a direct keyword scan.
    Each result: {rel, path, score, title, snippet, engine}."""
    v = vaultlib._resolve(vault)
    if qmd_available():
        name = collection_name(v)
        try:
            r = _qmd("query", query, "-c", name, "--index", QMD_INDEX,
                     "--json", "-n", str(k), timeout=120)
            data = json.loads(r.stdout) if r.stdout.strip().startswith("[") else []
            out = []
            for d in data:
                rel = _strip_qmd_uri(d.get("file", ""), name)
                out.append({"rel": rel, "path": str(v / rel), "score": d.get("score"),
                            "title": d.get("title", ""), "snippet": d.get("snippet", ""),
                            "engine": "qmd"})
            if out:
                return out
        except Exception:
            pass  # fall through to direct scan
    return _direct_scan(query, v, k)


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


def ask(query: str, vault: Path | str | None = None, k: int = 5) -> dict:
    """Retrieve, then synthesize an answer with a reader if one is configured
    (env FBT_READER, a command template with {prompt}); otherwise return the
    retrieved passages as the answer context."""
    import os
    hits = search(query, vault, k)
    context = "\n\n".join(f"[{h['title']}] {h['snippet']}" for h in hits)
    reader = os.environ.get("FBT_READER")
    answer = None
    if reader and hits:
        prompt = (f"Answer the question using ONLY the context. Cite the note titles.\n\n"
                  f"CONTEXT:\n{context}\n\nQUESTION: {query}\nANSWER:")
        try:
            parts = reader.split()
            r = subprocess.run(parts, input=prompt, capture_output=True,
                               text=True, timeout=120)
            answer = (r.stdout or "").strip() or None
        except Exception:
            answer = None
    return {"query": query, "hits": hits, "context": context, "answer": answer,
            "engine": hits[0]["engine"] if hits else "none"}
