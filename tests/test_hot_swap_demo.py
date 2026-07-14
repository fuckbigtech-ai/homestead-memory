from __future__ import annotations

from examples import hot_swap_demo
from homestead_memory import Memory
from homestead_memory.core import index, provenance


def test_run_returns_zero(monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    assert hot_swap_demo.run() == 0


def test_context_survives_swap(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    mem_a = Memory(tmp_path, agent="assistant@claude-sonnet-4.7")
    mem_a.remember("User", "project", "orchard-ledger")
    mem_a.ingest()

    mem_b = Memory(tmp_path, agent="assistant@glm-4.7")
    mem_b.ingest()
    hits = mem_b.search("orchard-ledger project", k=5)
    text = "\n".join(hit.get("snippet", "") for hit in hits)

    assert "orchard-ledger" in text


def test_cross_model_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)

    mem_a = Memory(tmp_path, agent="assistant@claude-sonnet-4.7")
    mem_a.remember("User", "project", "orchard-ledger")

    mem_b = Memory(tmp_path, agent="assistant@glm-4.7")
    mem_b.remember("User", "handoff_model", "glm-4.7")

    agents = {row["agent"] for row in mem_b.history("user")}
    assert "assistant@claude-sonnet-4.7" in agents
    assert "assistant@glm-4.7" in agents


def test_at_symbol_survives_provenance(tmp_path):
    mem = Memory(tmp_path, agent="x@glm-4.7")
    mem.remember("User", "model", "glm-4.7")

    note = tmp_path / "distilled" / "user.md"
    changelog = [
        line
        for line in note.read_text(encoding="utf-8").splitlines()
        if "recorded model" in line
    ]
    assert changelog
    assert provenance.parse_token(changelog[0])["agent"] == "x@glm-4.7"
    assert mem.history("user")[0]["agent"] == "x@glm-4.7"
