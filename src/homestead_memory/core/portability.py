#!/usr/bin/env python3
"""Portable vault export/import helpers.

The bundle format is deliberately boring: gzip tar, markdown notes at their
relative paths, and JSON sidecars that are either source-of-truth
(`.hsm/citations.json`) or rebuildable (`temporal.json`, `manifest.json`).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Any

from . import provenance, store, temporal, vault as vaultlib

EXPORT_FORMAT = "homestead-export"
EXPORT_VERSION = 1


def _fallback_vault_hash(vault: Path) -> str:
    records: list[tuple[str, str]] = []
    for p, rel in vaultlib.iter_notes(vault):
        body = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        records.append((rel.as_posix(), hashlib.sha256(body).hexdigest()))
    canonical = "".join(f"{rel}\n{body_hash}\n" for rel, body_hash in sorted(records))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _vault_hash(vault: Path) -> str:
    try:
        from . import signing

        return signing.vault_state_hash(vault)
    except Exception:
        return _fallback_vault_hash(vault)


def _temporal_rows(vault: Path) -> list[dict]:
    rows: list[dict] = []
    for p, rel in vaultlib.iter_notes(vault):
        for e in temporal.parse_changelog(p.read_text(errors="replace")):
            rows.append({
                "note": rel.as_posix(),
                "valid_date": e.get("date"),
                "field": e.get("field"),
                "old_val": e.get("old"),
                "new_val": e.get("new"),
                "text": e.get("text"),
                "agent": e.get("agent"),
                "session": e.get("session"),
                "ts": e.get("ts"),
            })
    return rows


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def export_vault(vault: Path | str | None = None, out_path: Path | str | None = None) -> dict:
    """Export a vault to a portable `.tar.gz` bundle."""
    root = vaultlib._resolve(vault)
    notes = sorted(vaultlib.iter_notes(root), key=lambda t: t[1].as_posix())
    bundle = Path(out_path).expanduser() if out_path else Path.cwd() / f"{root.name}-export.tar.gz"
    bundle.parent.mkdir(parents=True, exist_ok=True)

    vault_hash = _vault_hash(root)
    manifest = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": provenance.now_ts(),
        "vault_state_hash": vault_hash,
        "note_count": len(notes),
    }
    temporal_rows = _temporal_rows(root)

    entries: list[tuple[str, bytes]] = []
    for p, rel in notes:
        entries.append((rel.as_posix(), p.read_bytes()))
    citations = root / ".hsm" / "citations.json"
    if citations.exists() and citations.is_file():
        entries.append((".hsm/citations.json", citations.read_bytes()))
    entries.append(("manifest.json", json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8") + b"\n"))
    entries.append(("temporal.json", json.dumps(temporal_rows, sort_keys=True, indent=2).encode("utf-8") + b"\n"))

    with tarfile.open(bundle, "w:gz") as tar:
        for arcname, data in sorted(entries, key=lambda t: t[0]):
            _add_bytes(tar, arcname, data)

    return {"bundle": str(bundle), "notes": len(notes), "vault_hash": vault_hash}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_slug(value: Any, fallback: str) -> str:
    raw = str(value or "").strip() or fallback
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-")
    return raw[:80] or fallback


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return json.dumps(text) if ("\n" in text or text.strip() != text or text == "") else text


def _note_text(frontmatter: dict[str, Any], body: str, agent: str) -> str:
    session = provenance.resolve_session()
    ts = provenance.now_ts()
    token = provenance.format_token(agent, session, ts)
    today = ts[:10]
    lines = ["---"]
    for key in sorted(frontmatter):
        value = frontmatter[key]
        if value is None:
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.extend([
        "---",
        "",
        body.strip(),
        "",
        "## Changelog",
        f"- {today}: imported memory (source: {frontmatter.get('source', 'import')}) {token}",
        "",
    ])
    return "\n".join(lines)


def _as_list(data: Any, key: str | None = None) -> list[Any]:
    if isinstance(data, list):
        return data
    if key and isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    return []


def _detect_json_format(data: Any) -> str:
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return "mem0"
        if isinstance(data.get("facts"), list) or isinstance(data.get("messages"), list):
            return "zep"
        if data.get("format") == EXPORT_FORMAT:
            return "homestead"
    items = data if isinstance(data, list) else []
    if items:
        first_dict = next((x for x in items if isinstance(x, dict)), None)
        if first_dict:
            if "memory" in first_dict or "user_id" in first_dict:
                return "mem0"
            if "fact" in first_dict or "role" in first_dict:
                return "zep"
            if "text" in first_dict or "content" in first_dict:
                return "generic"
    return "generic"


def _detect_format(source: Path, fmt: str) -> str:
    if fmt != "auto":
        return fmt
    if source.is_dir() or source.suffixes[-2:] == [".tar", ".gz"]:
        return "homestead"
    try:
        return _detect_json_format(_load_json(source))
    except Exception:
        return "generic"


def _mem0_items(data: Any) -> list[dict]:
    out = []
    for i, item in enumerate(_as_list(data, "results")):
        if not isinstance(item, dict):
            out.append({"malformed": True, "index": i})
            continue
        body = item.get("memory") or item.get("text")
        fm = {
            "name": f"mem0-{item.get('id', i)}",
            "type": "note",
            "source": "mem0",
            "id": item.get("id"),
            "user_id": item.get("user_id"),
            "created_at": item.get("created_at"),
            "metadata": item.get("metadata"),
        }
        out.append({"body": body, "frontmatter": fm, "slug": item.get("id") or i})
    return out


def _zep_items(data: Any) -> list[dict]:
    source = _as_list(data, "facts")
    source_name = "fact"
    if not source and isinstance(data, dict) and isinstance(data.get("messages"), list):
        source = data["messages"]
        source_name = "message"
    if isinstance(data, list):
        source = data
    out = []
    for i, item in enumerate(source):
        if not isinstance(item, dict):
            out.append({"malformed": True, "index": i})
            continue
        body = item.get("fact") or item.get("content")
        role = item.get("role")
        fm = {
            "name": f"zep-{source_name}-{i}",
            "type": "note",
            "source": "zep",
            "role": role,
            "created_at": item.get("created_at"),
        }
        if item.get("id") is not None:
            fm["id"] = item.get("id")
        out.append({"body": body, "frontmatter": fm, "slug": item.get("id") or i})
    return out


def _generic_items(data: Any) -> list[dict]:
    out = []
    for i, item in enumerate(data if isinstance(data, list) else []):
        if isinstance(item, str):
            body = item
            fm: dict[str, Any] = {}
        elif isinstance(item, dict):
            body = item.get("text") or item.get("content")
            fm = {k: item.get(k) for k in ("id", "created_at", "user_id", "entity_id") if k in item}
        else:
            out.append({"malformed": True, "index": i})
            continue
        fm.update({"name": f"generic-{fm.get('id', i)}", "type": "note", "source": "generic"})
        out.append({"body": body, "frontmatter": fm, "slug": fm.get("id") or i})
    return out


def _json_import_items(data: Any, fmt: str) -> list[dict]:
    if fmt == "mem0":
        return _mem0_items(data)
    if fmt == "zep":
        return _zep_items(data)
    return _generic_items(data)


def _write_imported_json(data: Any, fmt: str, root: Path, agent: str) -> dict:
    imported = skipped = 0
    notes: list[str] = []
    base = root / "imported" / fmt
    with store.vault_lock(root):
        for i, item in enumerate(_json_import_items(data, fmt)):
            if item.get("malformed") or not str(item.get("body") or "").strip():
                skipped += 1
                continue
            slug = _safe_slug(item.get("slug"), str(i))
            rel = Path("imported") / fmt / f"{slug}.md"
            target = base / f"{slug}.md"
            if target.exists():
                target = base / f"{slug}-{i}.md"
                rel = Path("imported") / fmt / target.name
            fm = item["frontmatter"]
            fm["source"] = fmt
            store.atomic_write(target, _note_text(fm, str(item["body"]), agent))
            imported += 1
            notes.append(rel.as_posix())
    return {"format": fmt, "imported": imported, "skipped": skipped, "notes": notes}


def _is_homestead_manifest(data: bytes) -> bool:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except Exception:
        return False
    return manifest.get("format") == EXPORT_FORMAT


def _safe_member_path(root: Path, arcname: str) -> Path | None:
    rel = Path(arcname)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    return root / rel


def _import_homestead(source: Path, root: Path) -> dict:
    imported = skipped = 0
    notes: list[str] = []
    with store.vault_lock(root):
        if source.is_dir():
            candidates = sorted(source.rglob("*.md"))
            for p in candidates:
                try:
                    rel = p.relative_to(source)
                except ValueError:
                    skipped += 1
                    continue
                if vaultlib.is_excluded(rel):
                    continue
                target = _safe_member_path(root, rel.as_posix())
                if target is None:
                    skipped += 1
                    continue
                store.atomic_write(target, p.read_text(encoding="utf-8", errors="replace"))
                imported += 1
                notes.append(rel.as_posix())
            citations = source / ".hsm" / "citations.json"
            if citations.exists():
                store.atomic_write(root / ".hsm" / "citations.json",
                                   citations.read_text(encoding="utf-8", errors="replace"))
            return {"format": "homestead", "imported": imported, "skipped": skipped, "notes": notes}

        try:
            with tarfile.open(source, "r:gz") as tar:
                members = sorted(tar.getmembers(), key=lambda m: m.name)
                manifest = next((m for m in members if m.name == "manifest.json"), None)
                if manifest:
                    f = tar.extractfile(manifest)
                    if f is None or not _is_homestead_manifest(f.read()):
                        skipped += 1
                for member in members:
                    if not member.isfile():
                        continue
                    name = member.name
                    if name == ".hsm/citations.json":
                        f = tar.extractfile(member)
                        if f is not None:
                            store.atomic_write(root / ".hsm" / "citations.json",
                                               f.read().decode("utf-8", errors="replace"))
                        continue
                    if not name.endswith(".md"):
                        continue
                    target = _safe_member_path(root, name)
                    if target is None:
                        skipped += 1
                        continue
                    f = tar.extractfile(member)
                    if f is None:
                        skipped += 1
                        continue
                    store.atomic_write(target, f.read().decode("utf-8", errors="replace"))
                    imported += 1
                    notes.append(name)
        except (tarfile.TarError, OSError):
            skipped += 1
    return {"format": "homestead", "imported": imported, "skipped": skipped, "notes": notes}


def import_memories(source: Path | str, vault: Path | str | None = None, fmt: str = "auto",
                    agent: str | None = None) -> dict:
    """Import memories from Mem0, Zep, generic JSON, or a Homestead bundle/dir."""
    src = Path(source).expanduser()
    root = vaultlib._resolve(vault)
    detected = _detect_format(src, fmt)
    writer = provenance.resolve_agent(agent or f"imported:{detected}")

    if detected == "homestead":
        source_for_homestead = src.parent if src.name == "manifest.json" and src.is_file() else src
        return _import_homestead(source_for_homestead, root)

    try:
        data = _load_json(src)
    except Exception:
        return {"format": detected, "imported": 0, "skipped": 1, "notes": []}
    if fmt == "auto":
        detected = _detect_json_format(data)
        writer = provenance.resolve_agent(agent or f"imported:{detected}")
    return _write_imported_json(data, detected, root, writer)
