import os
import signal
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


def test_posix_terminate_signals_dedicated_process_group(monkeypatch):
    calls = []
    monkeypatch.setattr(qmd_runtime, "_platform_is_windows", lambda: False)
    monkeypatch.setattr(qmd_runtime.os, "getpgid", lambda pid: 4200, raising=False)
    monkeypatch.setattr(qmd_runtime.os, "getpgrp", lambda: 7, raising=False)
    monkeypatch.setattr(
        qmd_runtime.os,
        "killpg",
        lambda process_group, sig: calls.append((process_group, sig)),
        raising=False,
    )

    assert qmd_runtime._terminate(42) is True
    assert calls == [(4200, signal.SIGTERM)]


def test_posix_terminate_refuses_callers_process_group(monkeypatch):
    calls = []
    monkeypatch.setattr(qmd_runtime, "_platform_is_windows", lambda: False)
    monkeypatch.setattr(qmd_runtime.os, "getpgid", lambda pid: 7, raising=False)
    monkeypatch.setattr(qmd_runtime.os, "getpgrp", lambda: 7, raising=False)
    monkeypatch.setattr(qmd_runtime.os, "killpg", lambda *args: calls.append(args), raising=False)

    assert qmd_runtime._terminate(42) is False
    assert calls == []


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


# --- adoption: losing the pidfile must not be permanent -----------------------------
#
# Observed twice on this machine (2026-08-27, 2026-08-31). The pidfile goes missing
# while the server keeps serving, and every route back is then closed: status reports
# pid_owned False, `start` refuses with port_in_use_unowned, `stop` refuses with
# pid_not_owned, and `refresh` raises "refusing to adopt a foreign QMD runtime". The
# index silently goes stale and retrieval degrades until someone kills the server by
# hand. It also caused a flaky memory-degradation gate that looked transient.
#
# The second half of the bug: `qmd` here is a version-manager shim that SPAWNS the real
# server as a child, so start() recorded the wrapper while the child held the socket.
# Kill the wrapper and ownership is lost forever.


def _adopt_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_QMD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HSM_QMD_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HSM_QMD_STATE_DIR", str(tmp_path / "state"))
    qmd_runtime.ensure_dirs()


def _fake_healthy(monkeypatch, *, pid, cmdline, holds_files):
    monkeypatch.setattr(qmd_runtime, "health", lambda **k: {"ok": True})
    monkeypatch.setattr(qmd_runtime, "_listener_pid", lambda: pid)
    monkeypatch.setattr(qmd_runtime, "_alive", lambda p: p == pid)
    monkeypatch.setattr(qmd_runtime, "_process_commandline", lambda p: cmdline)
    monkeypatch.setattr(qmd_runtime, "_holds_our_files", lambda p: holds_files)


def test_adopts_our_own_listener_when_the_pidfile_was_lost(tmp_path, monkeypatch):
    """The recovery that did not exist. Without it the state is unrecoverable."""
    _adopt_env(tmp_path, monkeypatch)
    port = qmd_runtime.port()
    _fake_healthy(monkeypatch, pid=4242,
                  cmdline=f"node /x/qmd.js mcp --http --port {port}", holds_files=True)

    result = qmd_runtime.adopt()

    assert result["adopted"] is True, result
    assert result["pid"] == 4242
    assert qmd_runtime.paths()["pid"].read_text().strip() == "4242"


def test_adopts_the_listener_not_the_wrapper(tmp_path, monkeypatch):
    """`qmd` is a shim that spawns the real server; the socket belongs to the CHILD.

    Recording the wrapper means its death orphans a live listener and ownership can
    never be regained.
    """
    _adopt_env(tmp_path, monkeypatch)
    port = qmd_runtime.port()
    wrapper, listener = 111, 222
    _fake_healthy(monkeypatch, pid=listener,
                  cmdline=f"node /x/qmd.js mcp --http --port {port}", holds_files=True)
    qmd_runtime.paths()["pid"].write_text(f"{wrapper}\n")

    qmd_runtime.adopt()

    assert qmd_runtime.paths()["pid"].read_text().strip() == str(listener)


def test_refuses_a_genuinely_foreign_command_line(tmp_path, monkeypatch):
    """The guard must still mean something: adoption is not 'trust anything on the port'."""
    _adopt_env(tmp_path, monkeypatch)
    _fake_healthy(monkeypatch, pid=4242,
                  cmdline="node /somebody/else/server.js --port 9999", holds_files=True)

    result = qmd_runtime.adopt()

    assert result["adopted"] is False
    assert result["reason"] == "foreign_command_line"
    assert not qmd_runtime.paths()["pid"].exists()


def test_refuses_a_lookalike_that_does_not_use_our_index_or_log(tmp_path, monkeypatch):
    """Command shape alone is weak: any qmd on this port matches it.

    Holding OUR index or log is what makes the process ours, so a second qmd install
    serving the same port is still refused.
    """
    _adopt_env(tmp_path, monkeypatch)
    port = qmd_runtime.port()
    _fake_healthy(monkeypatch, pid=4242,
                  cmdline=f"node /other/install/qmd.js mcp --http --port {port}",
                  holds_files=False)

    result = qmd_runtime.adopt()

    assert result["adopted"] is False
    assert result["reason"] == "not_our_index_or_log"
    assert not qmd_runtime.paths()["pid"].exists()


def test_adopt_is_a_noop_when_nothing_is_listening(tmp_path, monkeypatch):
    _adopt_env(tmp_path, monkeypatch)
    monkeypatch.setattr(qmd_runtime, "health", lambda **k: {"ok": False})

    result = qmd_runtime.adopt()

    assert result["adopted"] is False
    assert result["reason"] == "no_healthy_listener"
