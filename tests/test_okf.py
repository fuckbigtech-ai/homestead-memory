"""Open Knowledge Format import/export behavior."""
from __future__ import annotations

from pathlib import Path

import pytest

from homestead_memory import connect
from homestead_memory.adapters.okf import okf_export, okf_import
from homestead_memory.cli import main
from homestead_memory.core import vault as vaultlib


def _body(text: str) -> str:
    match = vaultlib._FM_BLOCK_RE.match(text)
    assert match is not None
    return text[match.end():]


def test_okf_round_trip_preserves_bodies_and_all_original_frontmatter(tmp_path):
    source_vault = tmp_path / "source-vault"
    fresh_vault = tmp_path / "fresh-vault"
    memory = connect(source_vault, agent="okf-test")
    memory.remember("user", "city", "Berlin", source="profile")
    memory.remember("project", "status", "active", source="roadmap")

    original = {
        rel.as_posix(): path.read_text(encoding="utf-8")
        for path, rel in vaultlib.iter_notes(source_vault)
    }
    exported = okf_export(source_vault, tmp_path / "okf")
    imported = okf_import(exported["out_dir"], fresh_vault)

    assert exported["exported"] == 2
    assert imported["imported"] == 2
    assert imported["skipped"] == 0
    for rel, original_text in original.items():
        imported_text = (fresh_vault / rel).read_text(encoding="utf-8")
        original_fm = vaultlib.parse_frontmatter(original_text)["fields"]
        imported_fm = vaultlib.parse_frontmatter(imported_text)["fields"]
        assert _body(imported_text) == _body(original_text)
        assert {
            key: value
            for key, value in imported_fm.items()
            if key != "hsm_import_provenance"
        } == {
            key: value
            for key, value in original_fm.items()
            if key != "hsm_import_provenance"
        }
    assert "Berlin" in (fresh_vault / "distilled/user.md").read_text(encoding="utf-8")


def test_handwritten_okf_file_imports_as_readable_note(tmp_path):
    source = tmp_path / "fixture.md"
    source.write_text("---\ntype: reference\nname: x\nsource: handbook\n---\nbody\n", encoding="utf-8")
    vault = tmp_path / "vault"

    result = okf_import(source, vault)

    note = vault / "fixture.md"
    assert result == {"imported": 1, "skipped": 0, "vault": str(vault)}
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert _body(text) == "body\n"
    assert vaultlib.parse_frontmatter(text)["fields"]["name"] == "x"
    assert "source: handbook" in text


def test_okf_import_defaults_missing_type_to_note(tmp_path):
    source = tmp_path / "missing-type.md"
    source.write_text("---\nname: invalid\n---\nbody\n", encoding="utf-8")
    vault = tmp_path / "vault"

    result = okf_import(source, vault)

    assert result == {"imported": 1, "skipped": 0, "vault": str(vault)}
    imported = (vault / "missing-type.md").read_text(encoding="utf-8")
    assert vaultlib.parse_frontmatter(imported)["flat"]["type"] == "note"
    assert _body(imported) == "body\n"


def test_okf_import_defaults_blank_type_to_note(tmp_path):
    source = tmp_path / "blank-type.md"
    source.write_text("---\ntype:   \nname: blank\n---\nbody\n", encoding="utf-8")
    vault = tmp_path / "vault"

    result = okf_import(source, vault)

    assert result == {"imported": 1, "skipped": 0, "vault": str(vault)}
    imported = (vault / "blank-type.md").read_text(encoding="utf-8")
    assert vaultlib.parse_frontmatter(imported)["flat"]["type"] == "note"


@pytest.mark.parametrize("malformed_type", ["[", "{", "#", "&anchor", "*alias", "!tag", "|", ">"])
def test_okf_import_defaults_structural_yaml_type_to_note(tmp_path, malformed_type):
    source = tmp_path / "malformed-type.md"
    source.write_text(
        f"---\ntype: {malformed_type}\nname: malformed\n---\nbody\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"

    result = okf_import(source, vault)

    assert result == {"imported": 1, "skipped": 0, "vault": str(vault)}
    imported = (vault / "malformed-type.md").read_text(encoding="utf-8")
    assert vaultlib.parse_frontmatter(imported)["flat"]["type"] == "note"


def test_okf_import_does_not_follow_symlinked_vault_subdirectory(tmp_path):
    source = tmp_path / "source"
    (source / "linked").mkdir(parents=True)
    (source / "linked/pwn.md").write_text("---\ntype: note\n---\npwned\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (vault / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    result = okf_import(source, vault)

    assert result == {"imported": 0, "skipped": 1, "vault": str(vault)}
    assert not (outside / "pwn.md").exists()


def test_okf_import_does_not_overwrite_existing_note(tmp_path):
    source = tmp_path / "source"
    (source / "distilled").mkdir(parents=True)
    (source / "distilled/user.md").write_text(
        "---\ntype: distilled\n---\nmalicious replacement\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    (vault / "distilled").mkdir(parents=True)
    existing = vault / "distilled/user.md"
    original = "---\ntype: distilled\n---\nreal memory\n"
    existing.write_text(original, encoding="utf-8")

    result = okf_import(source, vault)

    assert result == {"imported": 0, "skipped": 1, "vault": str(vault)}
    assert existing.read_text(encoding="utf-8") == original


def test_okf_export_writes_top_level_type_for_non_reserved_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "mapped.md").write_text("---\nname: mapped\nnode_type: decision\n---\nMapped body\n")
    (vault / "defaulted.md").write_text("Body without frontmatter\n")

    result = okf_export(vault, tmp_path / "out")

    assert result["exported"] == 2
    mapped = vaultlib.parse_frontmatter((tmp_path / "out/mapped.md").read_text())["flat"]
    defaulted = vaultlib.parse_frontmatter((tmp_path / "out/defaulted.md").read_text())["flat"]
    assert mapped["type"] == "decision"
    assert defaulted["type"] == "note"


def test_okf_export_leaves_reserved_index_and_log_files_verbatim(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    reserved = {
        "index.md": "---\nname: index\n---\nIndex body\n",
        "log.md": "Log body without frontmatter\n",
    }
    for name, text in reserved.items():
        (vault / name).write_text(text, encoding="utf-8")

    result = okf_export(vault, tmp_path / "out")

    assert result["exported"] == 2
    for name, text in reserved.items():
        assert (tmp_path / "out" / name).read_text(encoding="utf-8") == text


def test_okf_import_stamps_existing_provenance_token_pattern(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("---\ntype: note\n---\nUnchanged body\n", encoding="utf-8")

    result = okf_import(source, tmp_path / "vault", agent="okf-import")

    text = (tmp_path / "vault/source.md").read_text(encoding="utf-8")
    assert result["imported"] == 1
    assert "hsm_import_provenance:" in text
    assert "[agent=okf-import session=" in text
    assert _body(text) == "Unchanged body\n"


def test_cli_routes_okf_import_and_export(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    (source / "concept.md").write_text("---\ntype: reference\n---\nCLI body\n")
    vault = tmp_path / "vault"
    out = tmp_path / "out"

    assert main(["import", "--format", "okf", str(source), str(vault)]) == 0
    assert main(["export", "--format", "okf", str(vault), "--out", str(out)]) == 0

    assert (out / "concept.md").exists()
    assert "from okf" in capsys.readouterr().out


def test_sdk_exposes_okf_import_and_export(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("---\ntype: playbook\n---\nSDK body\n", encoding="utf-8")
    memory = connect(tmp_path / "vault")

    imported = memory.okf_import(source)
    exported = memory.okf_export(tmp_path / "out")

    assert imported["imported"] == 1
    assert exported["exported"] == 1
    assert (tmp_path / "out/source.md").exists()
