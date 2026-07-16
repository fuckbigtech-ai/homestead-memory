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
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
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


def start(qmd_bin: str, wait_seconds: float = 12.0) -> dict:
    current = status()
    if current["ok"]:
        return {**current, "started": False}
    if current["endpoint_healthy"] and not current["pid_owned"]:
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
    p["pid"].write_text(f"{proc.pid}\n")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        live = health(timeout=0.5)
        if live["ok"]:
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
