"""Crash-safe, incremental refresh orchestration for the dedicated QMD index.

The vault is read-only to this module. Only the derived QMD cache and the
``.hsm`` refresh state are written. A failed refresh never advances the source
fingerprint, so the next run retries instead of declaring stale data fresh.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from . import index, qmd_runtime, store, vault as vaultlib


class RefreshReport(TypedDict, total=False):
    """Stable machine-readable result returned by :func:`refresh`."""

    ok: bool
    outcome: str
    phase: str
    run_id: str
    vault: str
    error: str
    reason: str
    fresh: bool
    source_fresh: bool
    embedding_fresh: bool
    pending_embeddings: int
    runtime_ok: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _fingerprint(vault: Path) -> str:
    digest = hashlib.sha256()
    for path, rel in sorted(vaultlib.iter_notes(vault), key=lambda item: item[1].as_posix()):
        try:
            stat = path.stat()
            digest.update(rel.as_posix().encode("utf-8", "surrogateescape"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode())
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode())
            digest.update(b"\0")
        except OSError:
            continue
    return digest.hexdigest()


def _state_paths(vault: Path, state_dir: Path | str | None) -> tuple[Path, Path]:
    configured = state_dir or os.environ.get("HSM_REFRESH_STATE_DIR")
    root = Path(configured).expanduser() if configured else vault / ".hsm"
    return root / "refresh-state.json", root / "refresh.lock"


def _write(state_file: Path, state: dict[str, Any], **updates: Any) -> None:
    state.update(updates)
    state["heartbeat_at"] = _now()
    _atomic_json(state_file, state)


def _run_qmd(args: list[str], timeout: float, *, state_file: Path,
             state: dict[str, Any], phase: str) -> subprocess.CompletedProcess[str]:
    """Run QMD with a progress heartbeat and a hard process-group timeout."""
    started = time.monotonic()
    process = subprocess.Popen(
        [index._QMD, *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=(os.name != "nt"),
        env=qmd_runtime.environment(qmd_bin=index._QMD),
    )
    while process.poll() is None:
        elapsed = time.monotonic() - started
        _write(state_file, state, phase=phase, elapsed_seconds=round(elapsed, 1))
        if elapsed >= timeout:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            raise TimeoutError(f"QMD {phase} exceeded {timeout:.0f}s")
        time.sleep(min(5.0, max(0.1, timeout - elapsed)))
    output = process.stdout.read() if process.stdout else ""
    return subprocess.CompletedProcess([index._QMD, *args], process.returncode,
                                       output, output)


def refresh(vault: Path | str | None = None, *, state_dir: Path | str | None = None,
            watchdog_seconds: float = 3600.0, batch_docs: int = 100,
            batch_mb: int = 64) -> RefreshReport:
    """Refresh only changed QMD documents and return a machine-readable report."""
    if watchdog_seconds <= 0 or batch_docs <= 0 or batch_mb <= 0:
        return {"ok": False, "outcome": "failed", "reason": "ValueError",
                "error": "watchdog_seconds, batch_docs, and batch_mb must be positive"}
    root = vaultlib._resolve(vault)
    state_file, lock_file = _state_paths(root, state_dir)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "schema_version": 1, "run_id": uuid.uuid4().hex, "phase": "starting",
        "outcome": "running", "started_at": _now(), "vault": str(root),
    }
    lock_created = False
    lock: Any
    if os.name == "nt":
        try:
            descriptor = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()}\n".encode())
            os.close(descriptor)
            lock_created = True
        except FileExistsError:
            return {"ok": True, "outcome": "deferred_lock_busy"}
        lock = None
    else:
        lock = lock_file.open("a+", encoding="utf-8")
    try:
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return {"ok": True, "outcome": "deferred_lock_busy"}
        _write(state_file, state, phase="ownership_check")
        if not index.qmd_available():
            raise RuntimeError("qmd 2.1+ is required for refresh")
        runtime = qmd_runtime.status()
        if runtime.get("endpoint_healthy") and not runtime.get("pid_owned"):
            raise RuntimeError("refusing to adopt a foreign QMD runtime")
        if runtime.get("pid_alive") and runtime.get("pid_owned") and not runtime.get("endpoint_healthy"):
            raise RuntimeError("owned QMD runtime is unhealthy")
        fingerprint = _fingerprint(root)
        previous = (state_file.parent / "vault-fingerprint.sha256").read_text().strip() \
            if (state_file.parent / "vault-fingerprint.sha256").exists() else ""
        _write(state_file, state, source_fingerprint=fingerprint,
               indexed_fingerprint=previous or None,
               source_fresh=fingerprint == previous)
        doctor = qmd_runtime.doctor(index._QMD, index.collection_name(root))
        pending = doctor.get("pending_embeddings")
        required = fingerprint != previous or doctor.get("collection_present") is not True \
            or not isinstance(pending, int) or pending != 0
        _write(state_file, state, phase="refresh_pending" if required else "refresh_not_required",
               refresh_required=required, pending_before=pending)
        if required:
            marker = qmd_runtime.ensure_dirs()["maintenance"]
            marker.write_text(json.dumps({"operation": "refresh", "started_at": _now()}))
            started = time.monotonic()
            was_running = bool(runtime.get("ok"))
            if was_running:
                stopped = qmd_runtime.stop()
                if stopped.get("reason") and not stopped.get("stopped"):
                    raise RuntimeError(f"unable to stop owned QMD runtime: {stopped.get('reason')}")
            try:
                name = index.collection_name(root)
                add_or_update = (["update"] if index._collection_exists(name)
                                 else ["collection", "add", str(root), "--name", name, "--mask", "**/*.md"])
                result = _run_qmd(add_or_update, min(900.0, watchdog_seconds),
                                  state_file=state_file, state=state, phase="qmd_update")
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "qmd update failed").strip()[-1000:])
                embed = _run_qmd(["embed", "--max-docs-per-batch", str(batch_docs),
                                  "--max-batch-mb", str(batch_mb)],
                                 max(1.0, watchdog_seconds - (time.monotonic() - started)),
                                 state_file=state_file, state=state, phase="qmd_embed")
                if embed.returncode != 0:
                    raise RuntimeError((embed.stderr or embed.stdout or "qmd embed failed").strip()[-1000:])
            finally:
                marker.unlink(missing_ok=True)
                if was_running and index._QMD:
                    qmd_runtime.start(index._QMD)
            runtime = qmd_runtime.status()
        doctor = qmd_runtime.doctor(index._QMD, index.collection_name(root))
        pending = doctor.get("pending_embeddings")
        if not isinstance(pending, int) or pending != 0:
            raise RuntimeError(f"pending_embeddings={pending!r}")
        fingerprint_path = state_file.parent / "vault-fingerprint.sha256"
        store.atomic_write(fingerprint_path, fingerprint + "\n")
        completed = _now()
        _write(state_file, state, phase="complete", outcome="success", completed_at=completed,
               last_success_at=completed, source_fresh=True, embedding_fresh=True, fresh=True,
               pending_embeddings=0, collection_present=doctor.get("collection_present") is True,
               runtime_ok=doctor.get("runtime_ok", False), retrieval="balanced")
        return state
    except Exception as exc:
        _write(state_file, state, phase="failed", outcome="failed", completed_at=_now(),
               reason=type(exc).__name__, error=str(exc)[-1000:], fresh=False)
        return state
    finally:
        if os.name == "posix":
            try:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()
        elif lock_created:
            lock_file.unlink(missing_ok=True)
