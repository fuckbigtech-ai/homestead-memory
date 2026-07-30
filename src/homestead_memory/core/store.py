#!/usr/bin/env python3
"""Crash-safe store primitives shared by mutating memory writers."""
from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

from . import provenance


def _unlink_lock(path: Path, attempts: int = 50) -> bool:
    """Remove a lock despite short Windows reader-handle races."""
    for attempt in range(attempts):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(0.01)
    return False


def atomic_write(path: Path | str, text: str) -> None:
    """Write text via fsynced same-directory temp file + atomic replace."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        # newline="" writes the string's \n verbatim (no CRLF translation), so notes
        # are LF on every platform — matches the signing hash + keeps round-trips
        # byte-identical on Windows.
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Windows raises PermissionError (WinError 5) if the destination is open in
        # another thread; retry briefly so concurrent writers do not spuriously fail.
        for _attempt in range(10):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:
                if os.name != "nt" or _attempt == 9:
                    raise
                time.sleep(0.02)
        tmp = None
        dir_fd = None
        try:
            dir_fd = os.open(str(p.parent), os.O_RDONLY)
            os.fsync(dir_fd)
        except Exception:
            pass
        finally:
            if dir_fd is not None:
                with contextlib.suppress(Exception):
                    os.close(dir_fd)
    finally:
        if tmp is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)


# Evaluated once at import. A module-level flag rather than an inline
# `os.name == "nt"` so tests can exercise the Windows lock path without
# monkeypatching os.name itself, which also makes pathlib treat POSIX paths as
# Windows paths and breaks the fixture before the code under test even runs.
_WINDOWS = os.name == "nt"


@contextlib.contextmanager
def vault_lock(vault: Path | str, timeout: float = 10.0, stale: float = 120.0):
    """Cross-platform advisory vault write lock using atomic O_EXCL creation.

    The lock serializes cooperating writers. On POSIX, a dead pid is stolen
    immediately; otherwise stale mtime is only the fallback for unchecked locks.
    """
    root = Path(vault)
    lockdir = root / ".hsm"
    lockpath = lockdir / "write.lock"
    lockdir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False
    owner = None

    while True:
        try:
            fd = os.open(str(lockpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                owner = f"{os.getpid()} {provenance.now_ts()}"
                os.write(fd, owner.encode("utf-8"))
                os.close(fd)
            except BaseException:
                with contextlib.suppress(Exception):
                    os.close(fd)
                _unlink_lock(lockpath)
                raise
            acquired = True
            break
        except PermissionError:
            # WINDOWS ONLY. When another writer has unlinked the lock but a handle
            # is still open, the file enters "delete pending" and Windows answers a
            # fresh O_CREAT|O_EXCL with ERROR_ACCESS_DENIED (errno 13) instead of
            # EEXIST. That is contention, not a permissions problem, but it escaped
            # the FileExistsError arm below and crashed the writer.
            #
            # Caught in CI 2026-07-30: test_remember_concurrency_lock_prevents_forced_lost_update
            # failed on windows/py3.12 while windows/py3.10 passed in the SAME run,
            # i.e. an intermittent race, not a deterministic break. It is a real
            # defect in `hsm remember`, which is the multi-agent write path.
            #
            # POSIX re-raises unchanged: there, errno 13 really does mean the
            # filesystem said no, and swallowing it would hide a genuine problem.
            if not _WINDOWS:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for vault lock: {lockpath}")
            time.sleep(0.05)
            continue
        except FileExistsError:
            pid = None
            raw = ""
            try:
                raw = lockpath.read_text(errors="replace")
                first = raw.split(maxsplit=1)[0]
                pid = int(first)
                if pid <= 0:
                    pid = None
            except (FileNotFoundError, IndexError, ValueError):
                if not lockpath.exists():
                    continue

            # os.kill(pid, 0) is a POSIX liveness idiom. On Windows os.kill exists
            # but maps to TerminateProcess (it is NOT a liveness probe and can wedge
            # the process), so restrict pid-liveness stealing to POSIX and let other
            # platforms fall through to the mtime-stale path below.
            if pid is not None and os.name == "posix":
                alive = True
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
                except OSError:
                    alive = True
                if not alive:
                    _unlink_lock(lockpath)
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for vault lock: {lockpath}")
                time.sleep(0.05)
                continue

            try:
                age = time.time() - lockpath.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale:
                _unlink_lock(lockpath)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for vault lock: {lockpath}")
            time.sleep(0.05)

    try:
        yield
    finally:
        if acquired:
            try:
                if lockpath.read_text(errors="replace") == owner:
                    _unlink_lock(lockpath)
            except FileNotFoundError:
                pass
