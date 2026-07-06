#!/usr/bin/env python3
"""
core.chunking — split a retrieved note into query-relevant windows.

Retrieval finds the right *note* (qmd hybrid gives ~85% recall on LongMemEval),
but the reader was drowning in qmd's ~350-char snippet. The fix is parent-document
retrieval done at read time: resolve the retrieved note, split its FULL body into
paragraph/heading chunks here, and hand the reader only the chunks that actually
match the query, within a budget. Higher fidelity than a snippet, cheaper than the
whole note.

Pure stdlib — no qmd, no models. Deterministic (query-term frequency ranking).
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]{3,}")
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FM_KEY_RE = re.compile(r"(?m)^[A-Za-z0-9_-]+:\s")


def strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block so it doesn't dominate chunk scoring.
    Only strips a block that actually contains `key:` lines — a body that merely opens
    with a `---` thematic break (e.g. '---\\n# Title\\n---\\ncontent') is left intact."""
    m = _FM_RE.match(text)
    if m and _FM_KEY_RE.search(m.group(1)):
        return text[m.end():].lstrip("\n")
    return text


def chunk_markdown(text: str, *, max_chars: int = 1200) -> list[str]:
    """Split markdown into chunks on blank-line (paragraph) boundaries, each
    <= max_chars. A single oversized paragraph is hard-split so no chunk exceeds
    the cap. Frontmatter is stripped first. Returns [] for empty input."""
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    text = strip_frontmatter(text).strip()
    if not text:
        return []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def _flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0

    for b in blocks:
        if len(b) > max_chars:
            _flush()
            for i in range(0, len(b), max_chars):
                chunks.append(b[i:i + max_chars])
            continue
        if cur and cur_len + len(b) > max_chars:
            _flush()
        cur.append(b)
        cur_len += len(b) + 2
    _flush()
    return chunks


def _score(chunk: str, terms: set[str]) -> int:
    low = chunk.lower()
    return sum(low.count(t) for t in terms)


def select_relevant(chunks: list[str], query: str, *, max_chars: int) -> list[str]:
    """Rank chunks by query-term frequency, greedily keep the top ones whose combined
    length fits max_chars, then restore document order for a readable context. Always
    returns at least the single best-ranked chunk when any chunks exist (even if that
    one chunk alone exceeds max_chars — a truncated best beats nothing)."""
    if not chunks:
        return []
    terms = set(_WORD.findall(query.lower()))
    order = list(range(len(chunks)))
    if terms:
        order.sort(key=lambda i: (-_score(chunks[i], terms), i))
    chosen: list[int] = []
    total = 0
    for i in order:
        c = chunks[i]
        extra = len(c) + (2 if chosen else 0)   # the "\n\n" separator relevant_window joins with
        if chosen and total + extra > max_chars:
            continue
        chosen.append(i)
        total += extra
        if total >= max_chars:
            break
    chosen.sort()
    return [chunks[i] for i in chosen]


def relevant_window(text: str, query: str, *, max_chars: int = 2000,
                    chunk_chars: int = 1200) -> str:
    """Full note body -> the query-relevant chunks joined, within a char budget.
    The read-time parent-document primitive `ask()` uses. Empty string if no body.

    Chunk size is capped at max_chars so even the always-kept best chunk cannot
    blow a small budget (the `select_relevant` 'at least the best chunk' guarantee
    would otherwise return a full 1200-char chunk for a 200-char budget)."""
    if max_chars < 1:
        return ""
    chunks = chunk_markdown(text, max_chars=min(chunk_chars, max_chars))
    if not chunks:
        return ""
    return "\n\n".join(select_relevant(chunks, query, max_chars=max_chars))
