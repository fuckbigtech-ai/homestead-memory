#!/usr/bin/env python3
"""Crash-safe store primitives shared by mutating memory writers."""
from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

from . import provenance


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
        os.replace(tmp, p)
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
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(lockpath)
                raise
            acquired = True
            break
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

            if pid is not None and hasattr(os, "kill"):
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
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(lockpath)
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
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(lockpath)
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
                    os.unlink(lockpath)
            except FileNotFoundError:
                pass
