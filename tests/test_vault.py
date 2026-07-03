"""core.vault — frontmatter, wikilinks, ignore rules, note iteration."""
from pathlib import Path

from homestead_memory.core import vault


# ---------------------------------------------------------------- frontmatter
FLAT = """---
name: alpha
status: hot
updated: 2026-07-01
---
body
"""

NESTED = """---
name: beta
metadata:
  status: active
  brand: X
---
body
"""

CONFLICT = """---
name: gamma
status: hot
metadata:
  status: done
---
body
"""


def test_parse_flat():
    fm = vault.parse_frontmatter(FLAT)
    assert fm["fields"]["status"] == "hot"
    assert fm["status_nested"] is False
    assert fm["status_conflict"] is False
    # status_line points at the actual file line holding `status:`
    assert FLAT.splitlines()[fm["status_line"]].startswith("status:")


def test_parse_nested():
    fm = vault.parse_frontmatter(NESTED)
    assert fm["fields"]["status"] == "active"
    assert fm["fields"]["brand"] == "X"
    assert fm["status_nested"] is True


def test_parse_conflict_flat_wins():
    fm = vault.parse_frontmatter(CONFLICT)
    assert fm["fields"]["status"] == "hot"   # flat wins
    assert fm["status_conflict"] is True


def test_parse_no_frontmatter():
    assert vault.parse_frontmatter("# just a body\n") is None


# ----------------------------------------------------------------- wikilinks
def test_wikilinks_basic_alias_section_embed():
    text = "See [[alpha]] and [[beta|B]] and [[gamma#sec]] and ![[img.png]]."
    got = list(vault.iter_wikilinks(text))
    targets = [t for t, _ in got]
    assert targets == ["alpha", "beta", "gamma", "img.png"]
    assert got[3][1] is True                   # embed flag


def test_wikilinks_escaped_pipe_and_typo():
    text = r"table cell [[note\|alias]] and typo [[[extra]]"
    targets = [t for t, _ in vault.iter_wikilinks(text)]
    assert targets == ["note", "extra"]


def test_wikilinks_skip_code_spans():
    text = "real [[yes]]\n```\n[[no-fenced]]\n```\nand `[[no-inline]]` end"
    targets = [t for t, _ in vault.iter_wikilinks(text)]
    assert targets == ["yes"]


# ------------------------------------------------------------- ignore + iter
def _mk(root: Path, rel: str, body: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {p.stem}\nstatus: hot\nupdated: 2026-07-01\n---\n{body}\n")


def test_iter_notes_excludes_dotdirs_raw_archive(tmp_path):
    _mk(tmp_path, "keep.md")
    _mk(tmp_path, "raw/skip.md")
    _mk(tmp_path, "archive/skip.md")
    _mk(tmp_path, ".git/skip.md")
    rels = [r.as_posix() for _, r in vault.iter_notes(tmp_path)]
    assert rels == ["keep.md"]


def test_hsmignore_quarantines_dirs_and_globs(tmp_path):
    _mk(tmp_path, "keep.md")
    _mk(tmp_path, "reports/gen.md")
    _mk(tmp_path, "note.generated.md")
    (tmp_path / ".hsmignore").write_text("reports/\n*.generated.md\n")
    rels = [r.as_posix() for _, r in vault.iter_notes(tmp_path)]
    assert rels == ["keep.md"]


def test_legacy_fbtignore_still_honored(tmp_path):
    _mk(tmp_path, "keep.md")
    _mk(tmp_path, "reports/gen.md")
    (tmp_path / ".fbtignore").write_text("reports/\n")
    rels = [r.as_posix() for _, r in vault.iter_notes(tmp_path)]
    assert rels == ["keep.md"]


def test_hsm_vault_env_and_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("HSM_VAULT", raising=False)
    monkeypatch.setenv("FBT_VAULT", str(tmp_path))
    assert vault.default_vault() == tmp_path
    other = tmp_path / "other"
    monkeypatch.setenv("HSM_VAULT", str(other))
    assert vault.default_vault() == other      # HSM_* wins over FBT_*
