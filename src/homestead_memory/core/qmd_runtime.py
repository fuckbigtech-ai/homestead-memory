"""Dedicated qmd runtime management for Homestead Memory.

QMD reads its database and collection configuration from environment variables.
Keeping both paths explicit prevents Homestead from sharing or mutating a user's
default qmd index. The HTTP server is launched in the foreground as our child;
qmd's built-in daemon mode cannot reliably retain a named index.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

DEFAULT_PORT = 8182
MIN_QMD_VERSION = (2, 1, 0)


def paths() -> dict[str, Path]:
    home = Path.home()
    cache = Path(os.environ.get("HSM_QMD_CACHE_DIR", home / ".cache/homestead-memory/qmd")).expanduser()
    config = Path(os.environ.get("HSM_QMD_CONFIG_DIR", home / ".config/homestead-memory/qmd")).expanduser()
    state = Path(os.environ.get("HSM_QMD_STATE_DIR", home / ".local/state/homestead-memory/qmd")).expanduser()
    return {
        "cache": cache,
        "config": config,
        "state": state,
        "index": Path(os.environ.get("HSM_QMD_INDEX_PATH", cache / "index.sqlite")).expanduser(),
        "pid": state / "mcp.pid",
        "log": state / "mcp.log",
        "maintenance": state / "maintenance.json",
    }


def ensure_dirs() -> dict[str, Path]:
    result = paths()
    for key in ("cache", "config", "state"):
        result[key].mkdir(parents=True, exist_ok=True)
    return result


def environment(base: dict[str, str] | None = None, qmd_bin: str | None = None) -> dict[str, str]:
    p = ensure_dirs()
    env = dict(os.environ if base is None else base)
    env["INDEX_PATH"] = str(p["index"])
    env["QMD_CONFIG_DIR"] = str(p["config"])
    # QMD uses XDG paths for its index/model state. Keep those aligned with
    # Homestead's dedicated paths so CLI and MCP clients cannot silently fall
    # back to ~/.cache/qmd or ~/.config/qmd.
    env["XDG_CACHE_HOME"] = str(p["cache"].parent)
    env["XDG_CONFIG_HOME"] = str(p["config"].parent)
    env["XDG_STATE_HOME"] = str(p["state"].parent)
    if qmd_bin:
        # Keep the symlink's bin directory. Resolving it jumps into node_modules,
        # dropping the Node runtime that compiled qmd's native extensions.
        env["PATH"] = str(Path(qmd_bin).expanduser().parent) + os.pathsep + env.get("PATH", "")
    return env


def port() -> int:
    try:
        return int(os.environ.get("HSM_QMD_PORT", str(DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


def endpoint(path: str = "/mcp") -> str:
    return f"http://localhost:{port()}{path}"


@lru_cache(maxsize=4)
def version(qmd_bin: str | None) -> tuple[int, int, int] | None:
    if not qmd_bin:
        return None
    try:
        run = subprocess.run([qmd_bin, "--version"], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL,
                             env=environment(qmd_bin=qmd_bin))
    except (OSError, subprocess.SubprocessError):
        return None
    import re
    match = re.search(r"(?:qmd\s+)?(\d+)\.(\d+)\.(\d+)", run.stdout or run.stderr or "")
    return tuple(map(int, match.groups())) if match else None


def compatible(qmd_bin: str | None) -> bool:
    found = version(qmd_bin)
    return found is not None and found >= MIN_QMD_VERSION


def health(timeout: float = 2.0) -> dict:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(endpoint("/health"), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": response.status == 200 and payload.get("status") == "ok",
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1), **payload}
    except (OSError, ValueError, urllib.error.URLError):
        return {"ok": False, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}


def _read_pid() -> int | None:
    try:
        return int(paths()["pid"].read_text().strip())
    except (OSError, ValueError):
        return None


def _platform_is_windows() -> bool:
    return os.name == "nt"


def _windows_process_alive(pid: int) -> bool:
    """Check process liveness without using Windows' signal-emulating os.kill."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == error_access_denied


def _alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if _platform_is_windows():
        try:
            return _windows_process_alive(pid)
        except (OSError, ValueError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _windows_commandline(pid: int) -> str:
    command = (
        "$p = Get-CimInstance Win32_Process -Filter 'ProcessId = "
        f"{pid}'; if ($null -ne $p) {{ $p.CommandLine }}"
    )
    try:
        run = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", command],
            capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return run.stdout.strip() if run.returncode == 0 else ""


def _process_commandline(pid: int) -> str:
    if _platform_is_windows():
        return _windows_commandline(pid)
    try:
        run = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=2,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return run.stdout or ""


def _command_is_owned(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    port_arg = f"--port {port()}"
    port_equals_arg = f"--port={port()}"
    return (
        "qmd" in normalized
        and "mcp" in normalized
        and (port_arg in normalized or port_equals_arg in normalized)
    )


def _owned(pid: int | None) -> bool:
    if not _alive(pid):
        return False
    return _command_is_owned(_process_commandline(pid))


def _spawn_options() -> dict:
    if _platform_is_windows():
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_terminate = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_terminate, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def _terminate(pid: int, force: bool = False) -> bool:
    if _platform_is_windows():
        try:
            return _terminate_windows(pid)
        except (OSError, ValueError):
            return False
    try:
        process_group = os.getpgid(pid)
    except OSError:
        return False
    # Every runtime that Homestead starts has its own session. Never signal the
    # caller's process group if a stale or malformed PID file points elsewhere.
    if process_group == os.getpgrp():
        return False
    os.killpg(process_group, signal.SIGKILL if force else signal.SIGTERM)
    return True


def status() -> dict:
    p = paths()
    pid = _read_pid()
    live = health()
    owned = _owned(pid)
    return {
        "ok": bool(live["ok"] and owned),
        "pid": pid,
        "pid_alive": _alive(pid),
        "pid_owned": owned,
        "endpoint_healthy": bool(live["ok"]),
        "endpoint": endpoint(),
        "health": live,
        "index": str(p["index"]),
        "config": str(p["config"]),
        "maintenance": p["maintenance"].exists(),
    }


def _listener_pid() -> int | None:
    """PID actually LISTENING on our port, which is not always the pid we spawned.

    `qmd` on this machine resolves to a version-manager shim that SPAWNS the real
    server as a child rather than exec'ing into it, so `start()` records the wrapper
    while the child holds the socket. If the wrapper dies the child keeps serving and
    ownership is lost permanently.
    """
    if _platform_is_windows():
        return None
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port()}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    for tok in out:
        try:
            return int(tok)
        except ValueError:
            continue
    return None


def _holds_our_files(pid: int) -> bool:
    """Is this process using OUR index or log? The anti-foreign-runtime guard.

    Command-line shape alone is weak evidence: any qmd on this port matches it. Having
    our index.sqlite or mcp.log open is strong evidence the process is the one this
    install started, which is what makes adoption safe rather than reckless.
    """
    if _platform_is_windows():
        return False
    p = paths()
    try:
        out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return str(p["index"]) in out or str(p["log"]) in out


def adopt() -> dict:
    """Reclaim a healthy listener that is ours but whose pidfile was lost.

    Without this, losing the pidfile is UNRECOVERABLE: status reports pid_owned False
    forever, `start` refuses with port_in_use_unowned, `stop` refuses with
    pid_not_owned, and `refresh` refuses to "adopt a foreign QMD runtime" - so the
    index silently goes stale and retrieval degrades with no path back except killing
    the server by hand. Observed twice on this machine.

    Adoption requires BOTH an ownable command line AND our own index/log held open, so
    a genuinely foreign qmd on the port is still refused.
    """
    current = status()
    if current["pid_owned"]:
        return {**current, "adopted": False, "reason": "already_owned"}
    if not current["endpoint_healthy"]:
        return {**current, "adopted": False, "reason": "no_healthy_listener"}
    pid = _listener_pid()
    if not pid or not _alive(pid):
        return {**current, "adopted": False, "reason": "listener_pid_unknown"}
    if not _command_is_owned(_process_commandline(pid)):
        return {**current, "adopted": False, "reason": "foreign_command_line"}
    if not _holds_our_files(pid):
        return {**current, "adopted": False, "reason": "not_our_index_or_log"}
    ensure_dirs()
    paths()["pid"].write_text(f"{pid}\n")
    return {**status(), "adopted": True, "pid": pid}


def start(qmd_bin: str, wait_seconds: float = 12.0) -> dict:
    current = status()
    if current["ok"]:
        return {**current, "started": False}
    if current["endpoint_healthy"] and not current["pid_owned"]:
        # It may be OUR server with a lost pidfile. Reclaim before refusing.
        reclaimed = adopt()
        if reclaimed.get("adopted"):
            return {**reclaimed, "started": False, "reason": "adopted_existing"}
        return {**current, "started": False, "reason": "port_in_use_unowned"}
    if current["pid_alive"]:
        return {**current, "started": False, "reason": "owned_process_unhealthy"}
    if not compatible(qmd_bin):
        return {**current, "started": False, "reason": "qmd_2_1_required"}
    p = ensure_dirs()
    log = p["log"].open("ab")
    proc = subprocess.Popen(
        [qmd_bin, "mcp", "--http", "--port", str(port())],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        env=environment(qmd_bin=qmd_bin), **_spawn_options(),
    )
    log.close()
    # Provisional: the wrapper may spawn the real server as a child, so the
    # listening pid is resolved once healthy (below) and rewritten there.
    p["pid"].write_text(f"{proc.pid}\n")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        live = health(timeout=0.5)
        if live["ok"]:
            listening = _listener_pid()
            if listening and listening != proc.pid and _alive(listening):
                p["pid"].write_text(f"{listening}\n")
            return {**status(), "started": True}
        time.sleep(0.15)
    return {**status(), "started": False, "reason": "startup_failed"}


def stop(wait_seconds: float = 8.0) -> dict:
    p = paths()
    pid = _read_pid()
    if not _alive(pid):
        p["pid"].unlink(missing_ok=True)
        return {**status(), "stopped": False}
    if not _owned(pid):
        return {**status(), "stopped": False, "reason": "pid_not_owned"}
    if not _terminate(pid):
        return {**status(), "stopped": False, "reason": "termination_failed"}
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        if not _terminate(pid, force=True):
            return {**status(), "stopped": False, "reason": "termination_failed"}
    p["pid"].unlink(missing_ok=True)
    return {**status(), "stopped": True}


def maintenance_active() -> bool:
    return paths()["maintenance"].exists()


def doctor(qmd_bin: str | None, collection: str | None = None) -> dict:
    p = ensure_dirs()
    found = version(qmd_bin)
    report = status()
    report["runtime_ok"] = report["ok"]
    report.update({
        "qmd_bin": qmd_bin,
        "qmd_version": ".".join(map(str, found)) if found else None,
        "qmd_compatible": bool(found and found >= MIN_QMD_VERSION),
        "index_exists": p["index"].exists(),
        "index_bytes": p["index"].stat().st_size if p["index"].exists() else 0,
        "index_age_seconds": round(max(0.0, time.time() - p["index"].stat().st_mtime), 1)
        if p["index"].exists() else None,
        "pending_embeddings": None,
    })
    if qmd_bin and report["qmd_compatible"]:
        try:
            run = subprocess.run([qmd_bin, "collection", "list"], capture_output=True,
                                 text=True, timeout=15, env=environment(qmd_bin=qmd_bin),
                                 stdin=subprocess.DEVNULL)
            report["collection_present"] = collection in (run.stdout or "") if collection else None
            report["qmd_status_ok"] = run.returncode == 0
            health_run = subprocess.run([qmd_bin, "status"], capture_output=True, text=True,
                                        timeout=15, env=environment(qmd_bin=qmd_bin),
                                        stdin=subprocess.DEVNULL)
            import re
            pending = re.search(r"Pending:\s+(\d+)", health_run.stdout or "")
            report["pending_embeddings"] = int(pending.group(1)) if pending else 0
            report["qmd_status_ok"] = report["qmd_status_ok"] and health_run.returncode == 0
        except (OSError, subprocess.SubprocessError):
            report["qmd_status_ok"] = False
    report["ok"] = bool(report.get("qmd_compatible") and report.get("qmd_status_ok") and
                        (report.get("collection_present") is not False))
    return report
