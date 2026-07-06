"""core.telemetry (opt-in local log) + core.tuning (the compounding loop v0)."""
from __future__ import annotations

import json

from homestead_memory.core import index, telemetry, tuning


def _write(v, name, body):
    (v / f"{name}.md").write_text(f"---\nname: {name}\n---\n{body}\n", encoding="utf-8")


# ------------------------------------------------------------------- telemetry
def test_telemetry_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HSM_TELEMETRY", raising=False)
    telemetry.log(tmp_path, {"type": "ask", "query": "x"})
    assert telemetry.events(tmp_path) == []          # opt-in: nothing written when disabled


def test_telemetry_logs_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_TELEMETRY", "1")
    telemetry.log(tmp_path, {"type": "ask", "query": "hello"})
    telemetry.log(tmp_path, {"type": "ask", "query": "world"})
    evs = telemetry.events(tmp_path)
    assert len(evs) == 2 and evs[0]["query"] == "hello" and "ts" in evs[0]


def test_telemetry_skips_corrupt_line(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_TELEMETRY", "1")
    (tmp_path / ".hsm").mkdir()
    (tmp_path / ".hsm" / "telemetry.jsonl").write_text('{"query":"ok"}\nNOT JSON\n', encoding="utf-8")
    assert [e["query"] for e in telemetry.events(tmp_path)] == ["ok"]


# ------------------------------------------------------------------- tuning
def test_tune_no_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    _write(tmp_path, "a", "content")
    rep = tuning.tune(tmp_path)
    assert rep["ok"] is False and rep["fixtures"] == 0


def test_tune_measures_improvement(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)         # deterministic direct-scan
    # 6 'loud' notes bury the target: at k=5 they crowd it out (recall 0); a bigger k
    # recovers it. tuning must MEASURE that and pick the smallest k that reaches it.
    for i in range(6):
        _write(tmp_path, f"loud{i}", "needle needle needle needle needle")
    _write(tmp_path, "target", "the needle appears here just once")
    hsm = tmp_path / ".hsm"; hsm.mkdir()
    (hsm / "fixtures.json").write_text(json.dumps([{"query": "needle", "expect": "target"}]))
    rep = tuning.tune(tmp_path)
    assert rep["ok"]
    assert rep["recall_before"] == 0.0 and rep["recall_after"] == 1.0   # measured improvement
    assert rep["chosen_k"] == 8                                          # smallest k that recovers
    assert (hsm / "tuning.json").exists()
    assert tuning.load(tmp_path)["k"] == 8


def test_ask_uses_tuned_k(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    _write(tmp_path, "n", "content here")
    (tmp_path / ".hsm").mkdir()
    (tmp_path / ".hsm" / "tuning.json").write_text(json.dumps({"k": 9}))
    captured = {}
    real = index.search
    monkeypatch.setattr(index, "search", lambda q, v, k: captured.update(k=k) or real(q, v, k))
    index.ask("anything", tmp_path)                  # k unset → resolves to tuned k=9
    assert captured["k"] == 9


def test_ask_explicit_k_overrides_tuning(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    _write(tmp_path, "n", "content here")
    (tmp_path / ".hsm").mkdir()
    (tmp_path / ".hsm" / "tuning.json").write_text(json.dumps({"k": 9}))
    captured = {}
    real = index.search
    monkeypatch.setattr(index, "search", lambda q, v, k: captured.update(k=k) or real(q, v, k))
    index.ask("anything", tmp_path, k=2)             # explicit k wins over tuning
    assert captured["k"] == 2


# ------------------------------------------------ audit fixes: validation + privacy
import pytest   # noqa: E402


@pytest.mark.parametrize("bad", [
    '{"k":"x"}', '{"k":null}', '{"k":-1}', '{"k":0}', '{"k":1000000000}', 'not json', '[]', '{}'])
def test_tuned_k_validates_bad_json(tmp_path, bad):
    (tmp_path / ".hsm").mkdir()
    (tmp_path / ".hsm" / "tuning.json").write_text(bad)
    assert tuning.tuned_k(tmp_path) == 5          # any bogus value → safe default, never crash


def test_tuned_k_valid(tmp_path):
    (tmp_path / ".hsm").mkdir()
    (tmp_path / ".hsm" / "tuning.json").write_text('{"k":12}')
    assert tuning.tuned_k(tmp_path) == 12


def test_telemetry_off_case_insensitive(tmp_path, monkeypatch):
    for val in ("False", "NO", "Off", "0", ""):
        monkeypatch.setenv("HSM_TELEMETRY", val)
        telemetry.log(tmp_path, {"type": "ask", "q": "x"})
    assert telemetry.events(tmp_path) == []       # 'False'/'NO'/'Off' must all mean off


def test_ask_telemetry_hashes_query(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.setenv("HSM_TELEMETRY", "1")
    _write(tmp_path, "n", "content")
    index.ask("my secret query about salaries", tmp_path)
    evs = telemetry.events(tmp_path)
    assert evs and "query_hash" in evs[0] and "query" not in evs[0]
    assert "salaries" not in json.dumps(evs)      # raw query text is never written to disk
