"""core.portability — portable export bundles and external memory imports."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from homestead_memory.core import index, portability, verify


NOTE_A = """---
name: alpha
status: hot
updated: 2026-07-01
---
# Alpha
portable memory note.

## Changelog
- 2026-07-01: recorded.
"""

NOTE_B = """---
name: beta
status: reference
updated: 2026-07-01
---
# Beta
second portable note.
"""


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_export_vault_bundle_layout(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write(vault, "alpha.md", NOTE_A)
    _write(vault, "nested/beta.md", NOTE_B)
    (vault / ".hsm").mkdir()
    (vault / ".hsm" / "citations.json").write_text('{"x": "y"}', encoding="utf-8")
    (vault / ".hsm" / "tuning.json").write_text("{}", encoding="utf-8")

    out = tmp_path / "bundle.tar.gz"
    res = portability.export_vault(vault, out_path=out)

    assert Path(res["bundle"]).exists()
    assert res["notes"] == 2
    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
        manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
    assert {"manifest.json", "temporal.json", "alpha.md", "nested/beta.md",
            ".hsm/citations.json"} <= names
    assert ".hsm/tuning.json" not in names
    assert manifest["format"] == "homestead-export"
    assert manifest["version"] == 1
    assert manifest["note_count"] == 2
    assert manifest["exported_at"]
    assert manifest["vault_state_hash"] == res["vault_hash"]


def test_homestead_export_round_trip_imports_notes_and_verifies(tmp_path):
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    vault_a.mkdir()
    vault_b.mkdir()
    _write(vault_a, "alpha.md", NOTE_A)
    _write(vault_a, "nested/beta.md", NOTE_B)

    bundle = portability.export_vault(vault_a, out_path=tmp_path / "roundtrip.tar.gz")
    res = portability.import_memories(bundle["bundle"], vault=vault_b, fmt="auto")

    assert res["format"] == "homestead"
    assert sorted(res["notes"]) == ["alpha.md", "nested/beta.md"]
    assert (vault_b / "alpha.md").read_text(encoding="utf-8") == NOTE_A
    assert (vault_b / "nested" / "beta.md").read_text(encoding="utf-8") == NOTE_B
    rep = verify.verify_vault(vault_b)
    assert rep["ok"] is True
    assert rep["stamp"] == "MEMORY INTACT"


def test_mem0_import_creates_findable_intact_note(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    source = tmp_path / "mem0.json"
    source.write_text(json.dumps({
        "results": [{
            "id": "1",
            "memory": "user likes espresso",
            "user_id": "u1",
            "created_at": "2026-01-01",
        }]
    }), encoding="utf-8")

    res = portability.import_memories(source, vault=tmp_path, fmt="auto", agent="test-agent")

    assert res["format"] == "mem0"
    assert res["imported"] == 1
    note = tmp_path / res["notes"][0]
    text = note.read_text(encoding="utf-8")
    assert note.parent.name == "mem0"
    assert "user likes espresso" in text
    assert "user_id: u1" in text
    assert "created_at: 2026-01-01" in text
    assert "[agent=test-agent session=" in text
    assert verify.verify_vault(tmp_path)["ok"] is True
    assert any("espresso" in h["snippet"].lower()
               for h in index.search("espresso", tmp_path, k=5))


def test_zep_import_creates_findable_intact_note(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    source = tmp_path / "zep.json"
    source.write_text(json.dumps({
        "facts": [{
            "fact": "user lives in Berlin",
            "created_at": "2026-01-02",
        }]
    }), encoding="utf-8")

    res = portability.import_memories(source, vault=tmp_path, fmt="auto")

    assert res["format"] == "zep"
    assert res["imported"] == 1
    note = tmp_path / res["notes"][0]
    text = note.read_text(encoding="utf-8")
    assert note.parent.name == "zep"
    assert "user lives in Berlin" in text
    assert "created_at: 2026-01-02" in text
    assert verify.verify_vault(tmp_path)["ok"] is True
    assert any("berlin" in h["snippet"].lower()
               for h in index.search("Berlin", tmp_path, k=5))


def test_malformed_import_entries_are_skipped_not_fatal(tmp_path):
    source = tmp_path / "mem0-bad.json"
    source.write_text(json.dumps({
        "results": [
            None,
            {},
            {"id": "blank", "memory": ""},
            {"id": "ok", "text": "valid imported memory"},
        ]
    }), encoding="utf-8")

    res = portability.import_memories(source, vault=tmp_path, fmt="mem0")

    assert res["format"] == "mem0"
    assert res["imported"] == 1
    assert res["skipped"] == 3
    assert res["notes"] == ["imported/mem0/ok.md"]
    assert verify.verify_vault(tmp_path)["ok"] is True
