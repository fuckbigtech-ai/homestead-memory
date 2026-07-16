from pathlib import Path

from homestead_memory.core import index, qmd_runtime


def test_environment_uses_dedicated_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state"))
    env = qmd_runtime.environment({"PATH": "/bin"})
    assert env["INDEX_PATH"] == str(tmp_path / "cache" / "index.sqlite")
    assert env["QMD_CONFIG_DIR"] == str(tmp_path / "config")
    assert Path(env["QMD_CONFIG_DIR"]).is_dir()


def test_version_gate(monkeypatch):
    class Result:
        stdout = "qmd 2.1.0 (abc)"
        stderr = ""

    qmd_runtime.version.cache_clear()
    monkeypatch.setattr(qmd_runtime.subprocess, "run", lambda *a, **k: Result())
    assert qmd_runtime.version("qmd") == (2, 1, 0)
    assert qmd_runtime.compatible("qmd") is True
    qmd_runtime.version.cache_clear()


def test_status_does_not_claim_stale_pid_is_healthy(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config"))
    qmd_runtime.ensure_dirs()["pid"].write_text("99999999\n")
    monkeypatch.setattr(qmd_runtime, "health", lambda timeout=2.0: {"ok": False})
    report = qmd_runtime.status()
    assert report["ok"] is False
    assert report["pid_alive"] is False


def test_stop_refuses_unowned_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config"))
    qmd_runtime.ensure_dirs()["pid"].write_text(f"{__import__('os').getpid()}\n")
    monkeypatch.setattr(qmd_runtime, "health", lambda timeout=2.0: {"ok": False})
    report = qmd_runtime.stop()
    assert report["stopped"] is False
    assert report["reason"] == "pid_not_owned"


def test_find_qmd_skips_incompatible_binary(tmp_path, monkeypatch):
    old = tmp_path / "old" / "qmd"
    new = tmp_path / "new" / "qmd"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_text("old")
    new.write_text("new")
    old.chmod(0o755)
    new.chmod(0o755)
    monkeypatch.setenv("PATH", f"{old.parent}:{new.parent}")
    monkeypatch.delenv("HSM_QMD_BIN", raising=False)
    monkeypatch.setattr(qmd_runtime, "compatible", lambda path: path == str(new))
    assert index._find_qmd() == str(new)
