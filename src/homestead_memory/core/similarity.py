"""similarity.py — store-agnostic near-duplicate detection (RotBench v1.2).

WHY THIS EXISTS
---------------
RotBench v1.1's load-bearing checks (`uncited_claim`, `dangling_citation`,
`duplicate_value`, `temporal_mismatch`) all read the cite-or-drop distilled layer.
A store that does not carry that structure has nothing for them to read, so it
scores ~100 no matter what it contains. Measured 2026-07-30: a Mem0 export with
blatant contradictions ("is allergic to shellfish" AND "is not allergic to
shellfish") imported and scored 100/100 MEMORY INTACT, identical to a clean one.

This module is the first check that works on ANY store, including raw imports:
notes that are near-textual-duplicates of each other, both live, with nothing
marking one as superseding the other. That is the canonical accumulating-memory
failure: the store keeps writing new versions of a fact and never retires the old
one, so retrieval can surface either.

WHY UNIGRAM CONTAINMENT AND NOT EMBEDDINGS
------------------------------------------
Embeddings would break the property the whole benchmark rests on: RotBench is
mechanical, reproducible on any machine, with no model and no API key. A score
that moves when someone swaps an embedding model is not a standard.

Unigram containment is deterministic, pure stdlib, and on this problem it is also
simply *better behaved*. (The first attempt WAS 3-gram Jaccard; it measured a
NEGATIVE margin. See the tuning table on DUPLICATE_THRESHOLD. `jaccard()` below is
retained for reporting and for reproducing that tuning; the check does not use it.)

  "is allergic to shellfish" vs "is NOT allergic to shellfish, that was someone
  else"  ->  high token overlap  ->  FLAGGED. Correct: near-identical text
  asserting opposite things, with neither retired, is a real integrity defect.
  (We did NOT measure an embedding baseline; there is no embedding dependency in
  this repo. The expectation that embeddings under-separate negation is stated as
  an expectation, not a result.)

  "staff engineer at a fintech in Toronto" vs "product manager at a healthcare
  startup in Vancouver"  ->  low token overlap  ->  NOT flagged. Correct: a person
  changing jobs is legitimate history, not rot; flagging it would be a false
  positive, and a check that cries wolf gets switched off. (We would EXPECT a
  topical embedding to group these as "employment" and over-flag, but that is an
  expectation, not something measured here.)

So the mechanical check is the more honest one here, not merely the cheaper one.

WHAT THIS DELIBERATELY DOES NOT CLAIM
-------------------------------------
It does not detect semantic contradiction. Two notes that disagree in meaning but
share few tokens will not be caught, and no mechanical check can catch them
without a model. The finding is worded as what it actually is: an unretired
near-duplicate pair. It is a WARN, not a FAIL, because a legitimate store may hold
deliberate near-duplicates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# TUNED EMPIRICALLY, not guessed. First attempt was 3-gram Jaccard and it FAILED:
# inserting one word ("not") breaks every 3-gram spanning it, so the negation pair
# scored 0.100 while an unrelated pair scored 0.111 — negative separation, i.e. the
# check was worse than useless. Measured margins (min of should-flag minus max of
# should-not-flag) across the fixture set:
#
#   n=1 containment  +0.455   <- chosen
#   n=1 jaccard      +0.125
#   n=2 containment  +0.386
#   n=2 jaccard      +0.078
#   n=3 containment  +0.133
#   n=3 jaccard      -0.011   <- the original guess, inverted
#
# Containment (|A n B| / min(|A|,|B|)) rather than Jaccard because the signal we
# want is asymmetric: an amendment restates the original and ADDS to it, so the
# older note's vocabulary ends up fully inside the newer one. Jaccard punishes that
# with the added length; containment scores it 1.0, which is the correct reading.
SHINGLE_N = 1
DUPLICATE_THRESHOLD = 0.77      # midpoint of the clean separating band (0.545, 1.0]
# 4, not 6. Measured on a real Mem0 import: "User is allergic to shellfish" is FIVE
# tokens, so a cutoff of 6 skipped it and the contradiction pair was never compared
# at all — the check silently found nothing on exactly the store it exists to catch.
# Memory stores are full of short one-line facts; a cutoff tuned for prose notes is
# the wrong instrument. 4 still filters near-empty notes without blinding the check.
MIN_TOKENS = 4

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Deterministic, no stemming, no stopword list.

    No stopword removal on purpose: dropping "not" is exactly how a negation pair
    would stop looking like a duplicate, which is the case we most want to see.
    """
    return _WORD_RE.findall((text or "").lower())


def shingles(tokens: list[str], n: int = SHINGLE_N) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def jaccard(a: set, b: set) -> float:
    """Symmetric overlap. Kept for reporting and for anyone reproducing the tuning."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def containment(a: set, b: set) -> float:
    """Asymmetric overlap: how much of the SMALLER set is inside the larger one.

    This is the scoring function. An unretired duplicate is usually an amendment
    that restates the original and adds to it, so the older note's vocabulary sits
    entirely inside the newer one. Jaccard penalises that with the added length;
    containment reads it as 1.0, which is what it actually is.
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b)) if inter else 0.0


@dataclass(frozen=True)
class Pair:
    left: str
    right: str
    score: float


def find_unretired_duplicates(
    notes: dict[str, str],
    *,
    threshold: float = DUPLICATE_THRESHOLD,
    min_tokens: int = MIN_TOKENS,
) -> list[Pair]:
    """Return near-duplicate note pairs, highest similarity first.

    `notes` maps a note identifier (relative path) to its body text. Comparison is
    O(n^2) in the number of eligible notes; memory stores in the size range this
    runs on (hundreds to low thousands) make that irrelevant, and an approximate
    index would make the result depend on tuning nobody can reproduce.
    """
    prepared: list[tuple[str, set]] = []
    for rel in sorted(notes):                     # sorted => stable output order
        toks = tokenize(notes[rel])
        if len(toks) < min_tokens:
            continue
        prepared.append((rel, shingles(toks)))

    out: list[Pair] = []
    for i in range(len(prepared)):
        rel_a, sh_a = prepared[i]
        for j in range(i + 1, len(prepared)):
            rel_b, sh_b = prepared[j]
            s = containment(sh_a, sh_b)
            if s >= threshold:
                out.append(Pair(rel_a, rel_b, round(s, 3)))
    out.sort(key=lambda p: (-p.score, p.left, p.right))
    return out
