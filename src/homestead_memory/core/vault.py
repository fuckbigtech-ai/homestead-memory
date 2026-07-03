#!/usr/bin/env python3
"""
core.vault — the markdown-vault model: frontmatter parsing, wikilinks, recency.

Two correctness-critical primitives everything else depends on:

  1. parse_frontmatter()  — reads BOTH flat (`status:`) and nested (`  status:`
     under a `metadata:` key) frontmatter, flat wins on conflict, and tracks the
     *exact file line index* of the winning status value so writers edit the right
     line (not a regex count=1 that may hit the wrong one).
  2. build_mtime_map()    — ONE batched git pass for latest-commit time per path,
     overlaid with filesystem mtime via max(), so modified/untracked notes reflect
     their live edit. A trustworthy recency signal (the `updated:` frontmatter
     field is too dirty to sort on).

The vault root is resolved from (in order): an explicit `vault=` argument, the
`HSM_VAULT` environment variable (legacy `FBT_VAULT` honored), else the current working directory. There is no
hard-coded personal path — this is the de-personalized core.

Ported from the author's personal `vault_mem_lib.py`; MIT.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Directory parts that exclude a note entirely (source material / tombstones).
EXCLUDE_DIR_PARTS = {"raw", "archive"}
# Path prefixes (relative, posix) excluded — e.g. Obsidian template folders whose
# `status: <% ... %>` placeholders must NOT be parsed as notes. Configurable via
# HSM_EXCLUDE_PREFIXES (comma-separated; legacy FBT_EXCLUDE_PREFIXES honored).
EXCLUDE_PREFIXES: tuple[str, ...] = tuple(
    p for p in (os.environ.get("HSM_EXCLUDE_PREFIXES")
                or os.environ.get("FBT_EXCLUDE_PREFIXES", "Meta/Templates/")).split(",") if p
)
# Top-level files that are conventionally indexes, not notes.
SKIP_FILES = {"Dashboard.md", "MEMORY.md"}

STATUS_ORDER = {
    "hot": 0, "active": 1, "paused": 2, "structural": 3,
    "review": 4, "done": 5, "reference": 6,
}
VALID_STATUS = set(STATUS_ORDER)

_FM_BLOCK_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FLAT_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")        # column 0
_NESTED_KV_RE = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")   # indented
_COMMIT_LINE_RE = re.compile(r"^C(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})$")


def default_vault() -> Path:
    """Vault root: $HSM_VAULT if set (legacy $FBT_VAULT honored), else the cwd."""
    env = os.environ.get("HSM_VAULT") or os.environ.get("FBT_VAULT")
    return Path(env).expanduser() if env else Path.cwd()


def _resolve(vault: Path | str | None) -> Path:
    return Path(vault).expanduser() if vault else default_vault()


def is_excluded(rel: Path) -> bool:
    """rel is a Path relative to the vault root."""
    parts = rel.parts
    if any(p.startswith(".") or p in EXCLUDE_DIR_PARTS for p in parts):
        return True
    if rel.name in SKIP_FILES:
        return True
    rp = rel.as_posix()
    return any(rp.startswith(pref) for pref in EXCLUDE_PREFIXES)


def _clean_val(v: str) -> str:
    return v.strip().strip('"').strip("'")


def parse_frontmatter(text: str):
    """
    Parse a note's frontmatter, handling flat AND nested-under-`metadata:` keys.

    Returns a dict (or None if there's no frontmatter block):
      fields          : merged {key: value} (flat wins over nested)
      flat / nested   : per-source maps
      line_index      : winning FILE line index per key (flat preferred)
      status_line     : 0-based FILE line index of the winning `status:` line, or None
      status_nested   : True if the winning status came from a nested line
      status_conflict : True if flat and nested set status to different values
    """
    m = _FM_BLOCK_RE.match(text)
    if not m:
        return None
    block = m.group(1)
    block_lines = block.splitlines()
    # File line index where the frontmatter body starts. `\s*` in the block regex
    # can eat blank lines after the opening `---`, so derive base from the real
    # match offset (NOT a hard-coded 1) or writers edit wrong lines.
    base = text[:m.start(1)].count("\n")

    flat, nested = {}, {}
    flat_line, nested_line = {}, {}
    in_metadata = False
    for i, line in enumerate(block_lines):
        fm = _FLAT_KV_RE.match(line)
        if fm:
            k, v = fm.group(1), _clean_val(fm.group(2))
            flat[k] = v
            flat_line[k] = base + i
            # A column-0 key ends any metadata block; `metadata:` (empty value) opens one.
            in_metadata = (k == "metadata" and v == "")
            continue
        # Indented keys count as nested fields ONLY inside a `metadata:` mapping —
        # never inside a block scalar (`description: |`) or other multiline value.
        if in_metadata:
            nm = _NESTED_KV_RE.match(line)
            if nm:
                k, v = nm.group(1), _clean_val(nm.group(2))
                nested[k] = v
                nested_line[k] = base + i

    merged = dict(nested)
    merged.update(flat)  # flat wins

    line_index = {}
    for k in set(flat) | set(nested):
        line_index[k] = flat_line[k] if k in flat_line else nested_line[k]

    has_flat = "status" in flat
    has_nested = "status" in nested
    if has_flat:
        status_line, status_nested = flat_line["status"], False
    elif has_nested:
        status_line, status_nested = nested_line["status"], True
    else:
        status_line, status_nested = None, False
    status_conflict = has_flat and has_nested and flat["status"] != nested["status"]

    return {
        "fields": merged,
        "flat": flat,
        "nested": nested,
        "flat_line": flat_line,
        "nested_line": nested_line,
        "line_index": line_index,
        "status_line": status_line,
        "status_nested": status_nested,
        "status_conflict": status_conflict,
    }


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_mtime_map(vault: Path | str | None = None) -> dict:
    """
    {relpath_posix: aware UTC datetime} = max(latest git-commit time, filesystem mtime).

    ONE batched `git log` pass (newest-first → first-seen-per-path = latest commit),
    then a filesystem-mtime overlay via max(). Never raises on git failure — falls
    back to fs mtime only (so a non-git vault still works).
    """
    vault = _resolve(vault)
    git_map: dict[str, datetime] = {}
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "log", "--no-renames",
             "--pretty=format:C%cI", "--name-only"],
            capture_output=True, text=True, timeout=30,
        )
        cur = None
        for line in proc.stdout.splitlines():
            cm = _COMMIT_LINE_RE.match(line)
            if cm:
                cur = _to_utc(datetime.fromisoformat(cm.group(1)))
            elif line and cur is not None and line.endswith(".md"):
                if line not in git_map:          # newest-first → keep first = latest
                    git_map[line] = cur
    except Exception:
        pass  # fs-mtime-only fallback

    mtime: dict[str, datetime] = {}
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault).as_posix()
        try:
            fs = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        g = git_map.get(rel)
        mtime[rel] = max(g, fs) if g else fs
    return mtime


def load_ignore(vault: Path) -> list[str]:
    """Read `<vault>/.hsmignore` (gitignore-ish: prefix-dir patterns ending in '/',
    or fnmatch globs; legacy `.fbtignore` honored). Lets users quarantine generated/
    report notes so a verifier never flags its own ecosystem's output (the
    generated-artifact-quarantine rule)."""
    f = vault / ".hsmignore"
    if not f.exists():
        f = vault / ".fbtignore"   # legacy name
    if not f.exists():
        return []
    return [ln.strip() for ln in f.read_text(errors="replace").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _ignored(rel_posix: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if rel_posix == pat[:-1] or rel_posix.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, pat.rstrip("/") + "/*"):
            return True
    return False


def iter_notes(vault: Path | str | None = None):
    """Yield (path, rel) for every non-excluded .md note.

    Uses os.walk with in-place directory pruning so we NEVER descend into dotdirs
    (.git, .smart-env, .venv) or raw/archive — a plain rglob walks those huge trees
    for nothing and turns a seconds-long scan into minutes on a real repo. Honors
    a `.hsmignore` (or legacy `.fbtignore`) in the vault root for user-declared exclusions."""
    vault = _resolve(vault)
    patterns = load_ignore(vault)
    dir_pats = [p[:-1] for p in patterns if p.endswith("/")]
    for dirpath, dirnames, filenames in os.walk(vault):
        rel_dir = Path(dirpath).relative_to(vault).as_posix()
        kept = []
        for d in sorted(dirnames):
            if d.startswith(".") or d in EXCLUDE_DIR_PARTS:
                continue
            child = d if rel_dir == "." else f"{rel_dir}/{d}"
            if any(child == dp or child.startswith(dp + "/") for dp in dir_pats):
                continue
            kept.append(d)
        dirnames[:] = kept
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(vault)
            if is_excluded(rel):
                continue
            if patterns and _ignored(rel.as_posix(), patterns):
                continue
            yield p, rel


# ---- canonical wikilink parser (single source of truth) ----
# Matches [[target]], [[target|alias]], [[target#section]], the ESCAPED-pipe form
# [[target\|alias]] (markdown-table escaping), and tolerates the [[[target]] typo.
# Target = chars up to the first of  [ ] # | \ .
_WIKILINK_RE = re.compile(r"(!?)\[\[+\s*([^\[\]#|\\]+)")
_ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf",
              ".mp4", ".mov", ".webm", ".canvas", ".excalidraw", ".xlsx", ".csv")
# Code spans (fenced ``` and inline `) hold literal [[stem]] doc-examples that are
# NOT links — blank them before extracting so they don't count as broken.
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def iter_wikilinks(text: str):
    """Yield (target_stem, is_embed) for every wikilink/embed outside code spans."""
    text = _INLINE_CODE_RE.sub(" ", _FENCED_CODE_RE.sub(" ", text))
    for m in _WIKILINK_RE.finditer(text):
        t = m.group(2).strip()
        if t:
            yield t, (m.group(1) == "!")


def is_pathlike_target(t: str) -> bool:
    """A wikilink target that points at a path/asset, not a note stem."""
    return (not t) or t.startswith(("../", "raw/")) or "/" in t \
        or t.lower().endswith(_ASSET_EXT)


def note_stems(vault: Path | str | None = None) -> set:
    return {p.stem for p, _ in iter_notes(vault)}


def excluded_area_stems(vault: Path | str | None = None) -> set:
    """Stems under archive/ and raw/ — excluded from the index but still valid
    link targets (tombstone / source-material references)."""
    vault = _resolve(vault)
    out = set()
    for area in ("archive", "raw"):
        d = vault / area
        if d.exists():
            out |= {p.stem for p in d.rglob("*.md")}
    return out


def valid_link_targets(vault: Path | str | None = None) -> set:
    """Every legitimate [[link]] target: note stems + archived/raw notes + the
    conventional top-level index notes (Dashboard/MEMORY)."""
    return note_stems(vault) | excluded_area_stems(vault) | {"Dashboard", "MEMORY"}
