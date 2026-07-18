import os
from pathlib import Path

from homestead_memory import cli
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


def test_environment_aligns_xdg_paths_with_homestead(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache" / "qmd"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config" / "qmd"))
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state" / "qmd"))
    env = qmd_runtime.environment({"PATH": "/bin"})
    assert Path(env["INDEX_PATH"]) == (tmp_path / "cache" / "qmd" / "index.sqlite")
    assert Path(env["QMD_CONFIG_DIR"]) == (tmp_path / "config" / "qmd")
    # QMD itself appends its qmd leaf to XDG_CACHE_HOME; the parent is
    # intentional and verified against qmd's runtime implementation.
    assert Path(env["XDG_CACHE_HOME"]) == (tmp_path / "cache")
    assert Path(env["XDG_CONFIG_HOME"]) == (tmp_path / "config")
    assert Path(env["XDG_STATE_HOME"]) == (tmp_path / "state")


def test_stop_refuses_unowned_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config"))
    qmd_runtime.ensure_dirs()["pid"].write_text(f"{__import__('os').getpid()}\n")
    monkeypatch.setattr(qmd_runtime, "health", lambda timeout=2.0: {"ok": False})
    report = qmd_runtime.stop()
    assert report["stopped"] is False
    assert report["reason"] == "pid_not_owned"


def test_windows_liveness_uses_win32_process_check(monkeypatch):
    calls = []
    monkeypatch.setattr(qmd_runtime, "_platform_is_windows", lambda: True)
    monkeypatch.setattr(
        qmd_runtime, "_windows_process_alive",
        lambda pid: calls.append(pid) or pid == 42,
    )

    assert qmd_runtime._alive(42) is True
    assert qmd_runtime._alive(43) is False
    assert calls == [42, 43]


def test_windows_ownership_uses_commandline(monkeypatch):
    monkeypatch.setattr(qmd_runtime, "_platform_is_windows", lambda: True)
    monkeypatch.setattr(qmd_runtime, "_windows_process_alive", lambda pid: True)
    monkeypatch.setattr(
        qmd_runtime,
        "_windows_commandline",
        lambda pid: r'"C:\Program Files\nodejs\node.exe" qmd.cmd mcp --http --port=8182',
    )

    assert qmd_runtime._owned(42) is True


def test_windows_spawn_uses_new_process_group(monkeypatch):
    monkeypatch.setattr(qmd_runtime, "_platform_is_windows", lambda: True)
    monkeypatch.setattr(qmd_runtime.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    assert qmd_runtime._spawn_options() == {"creationflags": 512}


def test_windows_stop_terminates_only_owned_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config"))
    qmd_runtime.ensure_dirs()["pid"].write_text("42\n")
    alive = iter([True, True, False, False, False])
    monkeypatch.setattr(qmd_runtime, "_alive", lambda pid: next(alive, False))
    monkeypatch.setattr(qmd_runtime, "_owned", lambda pid: True)
    monkeypatch.setattr(qmd_runtime, "health", lambda timeout=2.0: {"ok": False})
    terminated = []
    monkeypatch.setattr(
        qmd_runtime, "_terminate",
        lambda pid, force=False: terminated.append((pid, force)) or True,
    )

    report = qmd_runtime.stop(wait_seconds=0.2)
    assert report["stopped"] is True
    assert terminated == [(42, False)]


def test_qmd_stop_returns_success_after_process_is_gone(monkeypatch, capsys):
    monkeypatch.setattr(
        qmd_runtime,
        "stop",
        lambda: {
            "ok": False,
            "pid": None,
            "pid_alive": False,
            "endpoint_healthy": False,
            "stopped": True,
        },
    )
    args = type("Args", (), {"action": "stop", "path": None, "json": True})()
    assert cli.cmd_qmd(args) == 0
    assert '"stopped": true' in capsys.readouterr().out


def test_find_qmd_skips_incompatible_binary(tmp_path, monkeypatch):
    old = tmp_path / "old" / "qmd"
    new = tmp_path / "new" / "qmd"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_text("old")
    new.write_text("new")
    old.chmod(0o755)
    new.chmod(0o755)
    monkeypatch.setenv("PATH", f"{old.parent}{os.pathsep}{new.parent}")
    monkeypatch.delenv("HSM_QMD_BIN", raising=False)
    monkeypatch.setattr(index.shutil, "which", lambda name: None)
    monkeypatch.setattr(qmd_runtime, "compatible", lambda path: path == str(new))
    assert index._find_qmd() == str(new)
