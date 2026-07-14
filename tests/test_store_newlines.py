"""atomic_write must produce LF line endings on every platform (no CRLF).

Without newline="" on the write, Windows translates \n -> \r\n, which breaks
round-trips (portability/OKF export->import) and diverges from the signing hash,
which normalizes to LF. This test fails on Windows if that regresses.
"""
from __future__ import annotations

from homestead_memory.core import store


def test_atomic_write_is_lf_only(tmp_path):
    text = "---\nname: x\nstatus: hot\n---\n\n# X\nbody line one\nbody line two\n"
    p = tmp_path / "note.md"
    store.atomic_write(p, text)
    raw = p.read_bytes()
    assert b"\r\n" not in raw
    assert raw == text.encode("utf-8")
