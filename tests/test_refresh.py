from __future__ import annotations

import json
import os
from pathlib import Path

from homestead_memory.core import refresh


def test_refresh_defers_when_lock_is_held(tmp_path, monkeypatch):
    if os.name == "nt":
        import pytest
        pytest.skip("fcntl lock contention is POSIX-specific; Windows uses O_EXCL")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# note\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    lock = (state / "refresh.lock").open("a+")
    try:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = refresh.refresh(vault, state_dir=state)
    finally:
        lock.close()
    assert report["outcome"] == "deferred_lock_busy"


def test_failed_refresh_does_not_commit_fingerprint(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# note\n", encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setattr(refresh.index, "qmd_available", lambda: True)
    monkeypatch.setattr(refresh.qmd_runtime, "status", lambda: {
        "ok": False, "endpoint_healthy": False, "pid_alive": False,
        "pid_owned": False,
    })
    monkeypatch.setattr(refresh.qmd_runtime, "doctor", lambda *args: {
        "collection_present": False, "pending_embeddings": 1,
    })
    monkeypatch.setattr(refresh, "_run_qmd", lambda *args, **kwargs: type("R", (), {
        "returncode": 1, "stdout": "", "stderr": "synthetic failure"
    })())
    report = refresh.refresh(vault, state_dir=state)
    assert report["ok"] is False
    assert report["outcome"] == "failed"
    assert not (state / "vault-fingerprint.sha256").exists()
    saved = json.loads((state / "refresh-state.json").read_text())
    assert saved["fresh"] is False


def test_refresh_updates_only_the_target_collection(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# note\n", encoding="utf-8")
    state = tmp_path / "state"
    runtime_state = tmp_path / "runtime"
    runtime_state.mkdir()
    calls: list[list[str]] = []
    doctor_calls = 0

    monkeypatch.setattr(refresh.index, "qmd_available", lambda: True)
    monkeypatch.setattr(refresh.index, "_collection_exists", lambda _name: True)
    monkeypatch.setattr(refresh.qmd_runtime, "status", lambda: {
        "ok": False, "endpoint_healthy": False, "pid_alive": False,
        "pid_owned": False,
    })

    def doctor(*_args):
        nonlocal doctor_calls
        doctor_calls += 1
        return {
            "collection_present": True,
            "pending_embeddings": 1 if doctor_calls == 1 else 0,
            "runtime_ok": True,
        }

    monkeypatch.setattr(refresh.qmd_runtime, "doctor", doctor)
    monkeypatch.setattr(refresh.qmd_runtime, "ensure_dirs", lambda: {
        "maintenance": runtime_state / "maintenance.json",
    })

    isolated_config = ""

    def run_qmd(args, *_pos, **kwargs):
        nonlocal isolated_config
        calls.append(args)
        if kwargs.get("config_dir"):
            isolated_config = (kwargs["config_dir"] / "index.yml").read_text()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(refresh, "_run_qmd", run_qmd)
    report = refresh.refresh(vault, state_dir=state)

    assert report["ok"] is True
    assert report["outcome"] == "success"
    assert calls[0] == ["update"]
    assert calls[1][0] == "embed"
    assert str(vault) in isolated_config
    assert "**/*.md" in isolated_config
