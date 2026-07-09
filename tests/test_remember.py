"""core.remember/store — direct writes, provenance, atomicity, and locking."""
from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
import threading
import time
from datetime import date

import pytest

from homestead_memory.core import distill, index, remember, store, vault, verify


def test_remember_creates_distilled_note_with_provenance_and_retrieval(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    res = remember.remember("User", "current_crm", "HubSpot", vault=tmp_path,
                            agent="agent-a", session="sess-a")

    assert res["action"] == "recorded"
    assert res["note"] == "distilled/user.md"
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- current_crm: HubSpot (source: remember)" in note
    assert f'- {date.today().isoformat()}: recorded current_crm: "HubSpot" (source: remember)' in note
    assert "[agent=agent-a session=sess-a ts=" in note
    assert verify.verify_vault(tmp_path)["ok"] is True
    hits = index.search("HubSpot", tmp_path, k=5)
    assert any(h["rel"] == "distilled/user.md" for h in hits)

    cites = json.loads((tmp_path / ".hsm" / "citations.json").read_text())
    assert cites["user::current_crm"]["source"] == "remember"
    assert cites["user::current_crm"]["agent"] == "agent-a"


def test_remember_update_records_prior_value_and_new_provenance(tmp_path):
    remember.remember("User", "current_crm", "Salesforce", vault=tmp_path,
                      agent="agent-a", session="sess-a")
    res = remember.remember("User", "current_crm", "HubSpot", vault=tmp_path,
                            agent="agent-b", session="sess-b")

    assert res["action"] == "updated"
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- current_crm: HubSpot (source: remember)" in note
    assert 'update current_crm: "Salesforce" -> "HubSpot" (source: remember)' in note
    assert "[agent=agent-b session=sess-b ts=" in note
    assert note.count("current_crm:") == 3  # bullet + recorded line + update line


def test_remember_same_value_refreshes_citation_without_duplicate_changelog(tmp_path):
    remember.remember("User", "current_crm", "HubSpot", vault=tmp_path,
                      agent="agent-a", session="sess-a")
    first = (tmp_path / "distilled" / "user.md").read_text()
    res = remember.remember("User", "current_crm", "HubSpot", vault=tmp_path,
                            agent="agent-b", session="sess-b")

    assert res["action"] == "unchanged"
    second = (tmp_path / "distilled" / "user.md").read_text()
    assert second.count("recorded current_crm") == first.count("recorded current_crm") == 1
    cites = json.loads((tmp_path / ".hsm" / "citations.json").read_text())
    assert cites["user::current_crm"]["agent"] == "agent-b"
    assert cites["user::current_crm"]["session"] == "sess-b"


class _ParseOverlapGate:
    def __init__(self, parties: int, timeout: float):
        self.barrier = threading.Barrier(parties)
        self.timeout = timeout

    def wait(self) -> None:
        try:
            self.barrier.wait(timeout=self.timeout)
        except threading.BrokenBarrierError:
            pass


def _install_parse_overlap(monkeypatch, parties: int, timeout: float) -> None:
    real_parse = distill._parse_distilled

    def parse_with_overlap(text: str):
        fields = real_parse(text)
        _install_parse_overlap.gate.wait()
        return fields

    _install_parse_overlap.gate = _ParseOverlapGate(parties, timeout)
    monkeypatch.setattr(distill, "_parse_distilled", parse_with_overlap)


def test_remember_concurrency_lock_prevents_forced_lost_update(tmp_path, monkeypatch):
    jobs = [
        ("User", "field_0", "value-0"),
        ("User", "field_1", "value-1"),
        ("User", "field_2", "value-2"),
        ("User", "field_3", "value-3"),
        ("User", "field_4", "value-4"),
        ("User", "field_5", "value-5"),
        ("User", "field_6", "value-6"),
        ("User", "field_7", "value-7"),
    ]
    _install_parse_overlap(monkeypatch, parties=len(jobs), timeout=0.02)

    def write_one(job):
        ent, fld, val = job
        return remember.remember(ent, fld, val, vault=tmp_path, agent=f"agent-{fld}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(write_one, jobs))

    assert len(results) == len(jobs)
    assert {r["action"] for r in results} == {"recorded"}
    note_p = tmp_path / "distilled" / "user.md"
    text = note_p.read_text()
    fm = vault.parse_frontmatter(text)
    assert fm and fm["fields"]["type"] == "distilled"
    fields = distill._parse_distilled(text)
    assert len(fields) == len(jobs)
    for _ent, fld, val in jobs:
        assert fields[distill._san_field(fld)] == (distill._san_value(val), "remember")
    changelog = distill._existing_changelog(text)
    assert len(changelog) == len(jobs)
    assert all(line.startswith("- ") and "[agent=" in line for line in changelog)

    assert verify.verify_vault(tmp_path)["findings"] == []


def test_remember_concurrency_noop_lock_loses_forced_overlap(tmp_path, monkeypatch):
    jobs = [("User", f"field_{i}", f"value-{i}") for i in range(8)]
    _install_parse_overlap(monkeypatch, parties=len(jobs), timeout=2.0)
    monkeypatch.setattr(store, "vault_lock", lambda *a, **k: contextlib.nullcontext())

    def write_one(job):
        ent, fld, val = job
        return remember.remember(ent, fld, val, vault=tmp_path, agent=f"agent-{fld}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        list(ex.map(write_one, jobs))

    fields = distill._parse_distilled((tmp_path / "distilled" / "user.md").read_text())
    assert len(fields) < len(jobs)


def test_atomic_write_replaces_content_and_cleans_temps(tmp_path):
    target = tmp_path / "note.md"
    store.atomic_write(target, "old")
    store.atomic_write(target, "new text")

    assert target.read_text() == "new text"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".note.md.") and p.name.endswith(".tmp")]
    assert leftovers == []


def test_vault_lock_nested_acquire_times_out(tmp_path):
    started = time.monotonic()
    with store.vault_lock(tmp_path, timeout=1.0):
        with pytest.raises(TimeoutError):
            with store.vault_lock(tmp_path, timeout=0.15, stale=30.0):
                pass
    assert time.monotonic() - started < 1.0


def test_vault_lock_steals_stale_lock(tmp_path):
    lockdir = tmp_path / ".hsm"
    lockdir.mkdir()
    lockpath = lockdir / "write.lock"
    lockpath.write_text("dead 2000-01-01T00:00:00+00:00")
    old = time.time() - 3600
    os.utime(lockpath, (old, old))

    with store.vault_lock(tmp_path, timeout=1.0, stale=0.1):
        assert lockpath.exists()
        assert "dead" not in lockpath.read_text()
    assert not lockpath.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX liveness uses os.kill(pid, 0)")
def test_vault_lock_does_not_steal_live_holder_even_if_stale(tmp_path):
    lockdir = tmp_path / ".hsm"
    lockdir.mkdir()
    lockpath = lockdir / "write.lock"
    owner = f"{os.getpid()} 2000-01-01T00:00:00+00:00"
    lockpath.write_text(owner)
    old = time.time() - 3600
    os.utime(lockpath, (old, old))

    with pytest.raises(TimeoutError):
        with store.vault_lock(tmp_path, timeout=0.15, stale=0.1):
            pass
    assert lockpath.read_text() == owner


def test_remember_source_sanitizes_path_looking_values(tmp_path):
    remember.remember("User", "crm", "HubSpot", vault=tmp_path, source="../raw\\note.md")

    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "(source: .._raw_note_md)" in note
    assert "/" not in note.split("(source: ", 1)[1].split(")", 1)[0]
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_direct_citation_sidecar_exemption_rejects_path_sources(tmp_path):
    d = tmp_path / "distilled"
    d.mkdir()
    (tmp_path / ".hsm").mkdir()
    (d / "user.md").write_text(
        "---\nname: user\ntype: distilled\nentity: User\nupdated: 2026-07-03\n---\n\n"
        "# User\n\n"
        "- crm: HubSpot (source: ghost.md)\n\n"
        "## Changelog\n- 2026-07-03: recorded crm: \"HubSpot\" (source: ghost.md)\n")
    (tmp_path / ".hsm" / "citations.json").write_text(json.dumps({
        "user::crm": {
            "source": "ghost.md",
            "value": "HubSpot",
            "sha256": distill._sha256("HubSpot"),
        }
    }))

    rep = verify.verify_vault(tmp_path)
    assert any(f.check == "dangling_citation" for f in rep["fails"])
