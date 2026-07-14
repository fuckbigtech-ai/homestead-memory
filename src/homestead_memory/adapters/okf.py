"""Open Knowledge Format import/export for markdown vaults."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core import portability, provenance, store, vault as vaultlib

_IMPORT_PROVENANCE_KEY = "hsm_import_provenance"
_YAML_STRUCTURAL_PREFIXES = ("[", "{", "#", "&", "*", "!", "|", ">")
_OKF_RESERVED_FILES = {"index.md", "log.md"}


def _frontmatter(text: str) -> tuple[dict[str, Any], Any] | None:
    parsed = vaultlib.parse_frontmatter(text)
    match = vaultlib._FM_BLOCK_RE.match(text)
    if parsed is None or match is None:
        return None
    return parsed, match


def _set_frontmatter_field(text: str, key: str, value: str) -> str:
    found = _frontmatter(text)
    if found is None:
        return f"---\n{key}: {portability._yaml_scalar(value)}\n---\n{text}"

    parsed, match = found
    scalar = portability._yaml_scalar(value)
    line_index = parsed["flat_line"].get(key)
    if line_index is not None:
        lines = text.splitlines(keepends=True)
        old_line = lines[line_index]
        ending = "\r\n" if old_line.endswith("\r\n") else "\n" if old_line.endswith("\n") else ""
        lines[line_index] = f"{key}: {scalar}{ending}"
        return "".join(lines)

    newline = "\r\n" if "\r\n" in text[:match.end()] else "\n"
    prefix = newline if match.group(1) else ""
    return text[:match.end(1)] + f"{prefix}{key}: {scalar}" + text[match.end(1):]


def _okf_type(text: str) -> str | None:
    found = _frontmatter(text)
    if found is None:
        return None
    parsed, _match = found
    value = str(parsed["flat"].get("type") or "").strip()
    if not value or value.startswith(_YAML_STRUCTURAL_PREFIXES):
        return None
    return value


def _as_okf(text: str, basename: str) -> str:
    if basename in _OKF_RESERVED_FILES:
        return text

    found = _frontmatter(text)
    if found is None:
        return _set_frontmatter_field(text, "type", "note")

    parsed, _match = found
    current_type = str(parsed["flat"].get("type") or "").strip()
    if current_type:
        return text
    mapped_type = str(parsed["flat"].get("node_type") or "note").strip() or "note"
    return _set_frontmatter_field(text, "type", mapped_type)


def _source_notes(source: Path) -> list[tuple[Path, Path]]:
    if source.is_dir():
        return [(path, path.relative_to(source)) for path in sorted(source.rglob("*.md"))]
    if source.is_file() and source.suffix.lower() == ".md":
        slug = portability._safe_slug(source.stem, "note")
        return [(source, Path(f"{slug}.md"))]
    return []


def okf_import(
    source: Path | str,
    vault: Path | str | None = None,
    agent: str = "okf-import",
) -> dict:
    """Import OKF markdown concepts into a vault, preserving paths and content."""
    src = Path(source).expanduser()
    root = vaultlib._resolve(vault)
    candidates = _source_notes(src)
    if not candidates:
        skipped = 0 if src.is_dir() else 1
        return {"imported": 0, "skipped": skipped, "vault": str(root)}

    writer = provenance.resolve_agent(agent)
    session = provenance.resolve_session()
    resolved_root = root.resolve()
    imported = skipped = 0
    with store.vault_lock(root):
        for path, rel in candidates:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                skipped += 1
                continue
            if _okf_type(text) is None:
                text = _set_frontmatter_field(text, "type", "note")

            dest = root / rel
            try:
                dest.resolve().relative_to(resolved_root)
                dest.parent.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                skipped += 1
                continue
            if dest.exists() or dest.is_symlink():
                skipped += 1
                continue

            token = provenance.format_token(writer, session, provenance.now_ts())
            stamped = _set_frontmatter_field(text, _IMPORT_PROVENANCE_KEY, json.dumps(token))
            store.atomic_write(dest, stamped)
            imported += 1

    return {"imported": imported, "skipped": skipped, "vault": str(root)}


def okf_export(
    vault: Path | str | None = None,
    out_dir: Path | str | None = None,
) -> dict:
    """Export every vault note as an OKF-compatible markdown concept."""
    root = vaultlib._resolve(vault)
    destination = (
        Path(out_dir).expanduser()
        if out_dir is not None
        else Path.cwd() / f"{root.name}-okf"
    )
    notes = sorted(vaultlib.iter_notes(root), key=lambda item: item[1].as_posix())

    exported = 0
    for path, rel in notes:
        text = path.read_text(encoding="utf-8")
        store.atomic_write(destination / rel, _as_okf(text, rel.name))
        exported += 1

    return {"exported": exported, "out_dir": str(destination)}


__all__ = ["okf_import", "okf_export"]
