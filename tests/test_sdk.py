from __future__ import annotations

from pathlib import Path

from homestead_memory import Memory, connect
from homestead_memory.core import index, temporal


def test_memory_client_round_trips_distilled_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    mem = connect(tmp_path, agent="sdk-test")

    assert isinstance(mem, Memory)
    assert mem.vault == tmp_path
    assert mem.agent == "sdk-test"

    wrote = mem.remember("user", "city", "Berlin")
    assert wrote["action"] == "recorded"
    assert wrote["agent"] == "sdk-test"
    assert wrote["note"] == "distilled/user.md"

    hits = mem.search("Berlin", k=5)
    assert any(h["rel"] == "distilled/user.md" for h in hits)

    answer = mem.ask("what city is the user in?", k=5)
    assert any(h["rel"] == "distilled/user.md" for h in answer["hits"])
    assert "Berlin" in answer["context"]

    verified = mem.verify()
    assert verified["ok"] is True

    temporal.build(mem.vault)
    rows = mem.history("user")
    assert rows
    assert any("Berlin" in row["text"] for row in rows)

    resolved = mem.resolve("user", field="city")
    assert resolved["note"] == "distilled/user.md"
    assert resolved["resolved"] == []
    assert resolved["agent"] == "sdk-test"


def test_openapi_spec_lists_current_routes():
    text = Path("docs/openapi.yaml").read_text(encoding="utf-8")

    assert "openapi: 3.1.0" in text
    assert "version: 0.0.1" in text
    assert "description: local-first verifiable memory API" in text
    assert "bearerAuth:" in text
    assert "type: http" in text
    assert "scheme: bearer" in text
    assert "$ref: \"#/components/schemas/Finding\"" in text

    for path in (
        "/health:",
        "/verify:",
        "/history:",
        "/ask:",
        "/ingest:",
        "/distill:",
        "/remember:",
        "/resolve:",
    ):
        assert path in text
