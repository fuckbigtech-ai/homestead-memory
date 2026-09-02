"""Docs must not describe an older product than the one that ships.

Why this exists. On 2026-09-02 four surfaces were found describing an older product at
once: fuckbigtech.ai/lab's meta tags, that page's OG image, this README ("v0.2, building
in public" while the package was 0.4.0), and ROADMAP.md, which topped out at "v0.2 in
progress" and never mentioned the agent ledger despite the ledger being the lead feature.
Four instances of one defect class in one day is a process gap, not four accidents.

WHAT THIS CHECK HAD TO GET RIGHT, and what a first attempt got wrong.

The obvious rule, "the doc must mention the current version somewhere", is useless here.
It was written, and it did NOT catch the real README defect: that file already contained
"0.4.0" on line 88 and "v0.4" on line 362 while line 472 still said "v0.2, building in
public". Presence of the right number proves nothing when the wrong number sits in the
sentence a reader treats as the product's own summary of itself.

The inverse rule, "no version lower than current anywhere", is worse. Docs legitimately
cite old versions constantly ("corrected in v1.4", "shipped in 0.3.2"), so it fails on
every changelog line and gets deleted within a week.

So this targets the shape that actually broke: a STATUS CLAIM, meaning a version sitting
in a sentence that presents itself as the current state. Historical references are
untouched by design.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VER = r"v?(\d+\.\d+(?:\.\d+)?)"

# Each pattern captures a version that the surrounding words present as CURRENT.
# Add a pattern here when a new way of saying "this is the version" appears in the docs.
STATUS_PATTERNS = [
    rf"{VER},\s*building in public",
    rf"current(?:ly)?\s+(?:on|at|release)\b[:\s]*{VER}",
    rf"latest\s+release\b[:\s]*{VER}",
    rf"\bthis\s+is\s+{VER}\b",
    rf"\bnow\s+(?:on|at)\s+{VER}\b",
]


def _current() -> tuple[str, str, str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "could not read version from pyproject.toml"
    return tuple(m.group(1).split("."))


def _status_claims(body: str) -> list[str]:
    out = []
    for pat in STATUS_PATTERNS:
        out += re.findall(pat, body, re.I)
    return out


@pytest.mark.parametrize("doc", ["README.md", "ROADMAP.md"])
def test_status_claims_name_the_shipping_version(doc: str):
    """Any sentence that says "this is the current version" must say the true one."""
    major, minor, _ = _current()
    body = (ROOT / doc).read_text(encoding="utf-8")
    for claimed in _status_claims(body):
        cmaj, cmin = claimed.split(".")[:2]
        assert (cmaj, cmin) == (major, minor), (
            f"{doc} presents {claimed} as the current version, but {major}.{minor} ships. "
            f"This is the exact line that read 'v0.2, building in public' while the "
            f"package was 0.4.0."
        )


def test_roadmap_reaches_the_shipping_version():
    """ROADMAP enumerates releases as headings, so its highest heading is a status claim.

    The real failure was a roadmap whose last heading was v0.2 while 0.4.0 shipped, so it
    silently described a product two minor versions behind and omitted its lead feature.
    """
    major, minor, _ = _current()
    body = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    # scan every version token on each heading line, not just the leading one: a heading
    # like "## v0.3 and v0.4: the agent ledger" would otherwise register as 0.3
    heads = []
    for line in re.findall(r"^#{1,3}\s+.*$", body, re.M):
        for m in re.findall(r"\bv(\d+)\.(\d+)", line):
            heads.append((int(m[0]), int(m[1])))
    assert heads, "ROADMAP.md has no version headings, so releases are not enumerated"
    assert max(heads) >= (int(major), int(minor)), (
        f"ROADMAP.md stops at v{max(heads)[0]}.{max(heads)[1]} but {major}.{minor} ships. "
        f"Anyone reading it sees a product several releases behind."
    )
