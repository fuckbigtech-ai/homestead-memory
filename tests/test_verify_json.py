import json

from homestead_memory.cli import main


def _init_vault(root, capsys):
    assert main(["init", str(root)]) == 0
    capsys.readouterr()


def _load_verify_json(out: str) -> dict:
    rep = json.loads(out)
    assert set(rep) >= {"ok", "score", "stamp", "notes", "rotbench_version", "findings"}
    assert isinstance(rep["findings"], list)
    assert all(isinstance(f, dict) for f in rep["findings"])
    return rep


def test_cli_verify_json_emits_valid_report(tmp_path, capsys):
    root = tmp_path / "v"
    _init_vault(root, capsys)

    rc = main(["verify", str(root), "--json"])

    assert rc == 0
    rep = _load_verify_json(capsys.readouterr().out)
    assert rep["ok"] is True
    assert rep["rotbench_version"] == "v1.1"


def test_cli_verify_json_rot_is_nonzero_with_findings(tmp_path, capsys):
    root = tmp_path / "v"
    _init_vault(root, capsys)
    (root / "rot.md").write_text(
        "---\nname: rot\nstatus: hot\nmetadata:\n  status: done\n---\nx\n",
        encoding="utf-8",
    )

    rc = main(["verify", str(root), "--json"])

    assert rc == 1
    rep = _load_verify_json(capsys.readouterr().out)
    assert rep["ok"] is False
    assert rep["rotbench_version"] == "v1.1"
    assert rep["findings"]
    assert {"level", "check", "note", "detail"} <= set(rep["findings"][0])


def test_cli_verify_demo_json_is_machine_readable(capsys):
    rc = main(["verify", "--demo", "--json"])

    assert rc == 1
    rep = _load_verify_json(capsys.readouterr().out)
    assert rep["rotbench_version"] == "v1.1"
    assert rep["findings"]


def test_cli_verify_non_json_still_prints_human_report(tmp_path, capsys):
    root = tmp_path / "v"
    _init_vault(root, capsys)

    rc = main(["verify", str(root), "--quiet"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "MEMORY INTACT" in out
    assert "/100" in out
    try:
        json.loads(out)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("non-json verify output should stay human-readable")
