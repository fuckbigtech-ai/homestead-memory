from __future__ import annotations

import json
from pathlib import Path

from homestead_memory.core import refresh


def test_refresh_defers_when_lock_is_held(tmp_path, monkeypatch):
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
    assert report["outcome"] == "failed"
    assert not (state / "vault-fingerprint.sha256").exists()
    saved = json.loads((state / "refresh-state.json").read_text())
    assert saved["fresh"] is False

