"""Which extraction model actually ran, and is that recorded.

Context (2026-07-30): the default was `llama3.1:latest`, a ~23-month-old release
that stalled for >10 minutes on five one-line notes. Worse, the working override
lived in `.zshrc`, which is interactive-only: a launchd or CI run does NOT inherit
it and silently fell back to the stale default. A weaker extractor proposes fewer
facts, so fewer survive cite-or-drop, so the distilled layer is thinner and any
RotBench score over it is lower. Silent fallback therefore changes published
numbers invisibly. These tests keep the resolution explicit and recorded.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from homestead_memory.core import distill as D


@pytest.fixture
def vault():
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "a.md").write_text("---\nname: a\n---\n\nSome text here.\n")
    return d


def _rep(vault, **kw):
    # extract_fn keeps this hermetic: no ollama, no preflight.
    return D.distill(vault, dry=True, extract_fn=lambda rel, body: [], **kw)


def test_default_is_not_the_stale_model(monkeypatch):
    monkeypatch.delenv("HSM_DISTILL_MODEL", raising=False)
    assert D.resolve_model() == D.DEFAULT_DISTILL_MODEL
    assert D.DEFAULT_DISTILL_MODEL != "llama3.1:latest"


def test_resolution_precedence(monkeypatch):
    monkeypatch.setenv("HSM_DISTILL_MODEL", "env-model")
    assert D.resolve_model() == "env-model"
    assert D.resolve_model("arg-model") == "arg-model", "an explicit arg must win"
    monkeypatch.delenv("HSM_DISTILL_MODEL", raising=False)
    assert D.resolve_model() == D.DEFAULT_DISTILL_MODEL


def test_report_records_the_model_and_where_it_came_from(vault, monkeypatch):
    monkeypatch.delenv("HSM_DISTILL_MODEL", raising=False)
    r = _rep(vault)
    assert r["model"] == D.DEFAULT_DISTILL_MODEL
    assert r["model_source"] == "default"

    monkeypatch.setenv("HSM_DISTILL_MODEL", "env-model")
    r = _rep(vault)
    assert (r["model"], r["model_source"]) == ("env-model", "HSM_DISTILL_MODEL")

    r = _rep(vault, model="arg-model")
    assert (r["model"], r["model_source"]) == ("arg-model", "argument")


def test_env_less_fallback_is_visible_in_the_artifact(vault, monkeypatch):
    """The launchd/CI case. The run must SAY it fell back, so a benchmark scored
    with a crippled extractor is caught in the report rather than never."""
    monkeypatch.delenv("HSM_DISTILL_MODEL", raising=False)
    assert _rep(vault)["model_source"] == "default"


def test_preflight_names_the_fix_for_a_missing_model(monkeypatch):
    monkeypatch.setattr(D, "available_models", lambda timeout=10: ["qwen3:8b", "gemma3"])
    problem = D.preflight_model("no-such-model")
    assert problem and "no-such-model" in problem
    assert "ollama pull" in problem or "HSM_DISTILL_MODEL" in problem


def test_preflight_accepts_a_bare_name_against_a_tagged_install(monkeypatch):
    """ollama treats `qwen3` as `qwen3:latest`; a bare name must not false-alarm."""
    monkeypatch.setattr(D, "available_models", lambda timeout=10: ["qwen3:latest"])
    assert D.preflight_model("qwen3") is None


def test_preflight_is_silent_when_ollama_is_unreachable(monkeypatch):
    """Do not invent a model error when the real problem is a dead daemon; let the
    run surface that itself rather than misdiagnosing it."""
    monkeypatch.setattr(D, "available_models", lambda timeout=10: [])
    assert D.preflight_model("anything") is None
