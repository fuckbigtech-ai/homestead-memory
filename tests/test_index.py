"""Tests for the retrieval/QA core: chunking, parent-document expansion, the
question-type router, temporal rerank, and ask() with/without a reader.

The retrieval path had ZERO coverage before this — it's the first thing a serious
reviewer flags. qmd + the reader subprocess are the only external boundaries; both
are mocked (force the direct-scan fallback via _QMD=None; monkeypatch subprocess.run
for the reader) so the suite is fast, deterministic, and offline.
"""
from __future__ import annotations

import datetime

import pytest

from homestead_memory.core import chunking, index


def _write(vault, name, body, date=None):
    fm = f"---\nname: {name}\n" + (f"date: {date}\nupdated: {date}\n" if date else "") + "---\n\n"
    (vault / f"{name}.md").write_text(fm + body, encoding="utf-8")


# ----------------------------------------------------------------- chunking
def test_chunk_markdown_paragraphs():
    chunks = chunking.chunk_markdown("para one here.\n\npara two here.\n\npara three.", max_chars=20)
    assert chunks == ["para one here.", "para two here.", "para three."]
    assert all(len(c) <= 20 for c in chunks)


def test_chunk_markdown_strips_frontmatter():
    assert chunking.chunk_markdown("---\nname: x\n---\n\nreal body content.") == ["real body content."]


def test_chunk_markdown_empty():
    assert chunking.chunk_markdown("") == []
    assert chunking.chunk_markdown("---\nname: x\n---\n") == []


def test_chunk_oversized_hard_split():
    chunks = chunking.chunk_markdown("x" * 3000, max_chars=1000)
    assert len(chunks) == 3
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_bad_max_chars():
    with pytest.raises(ValueError):
        chunking.chunk_markdown("body", max_chars=0)


def test_select_relevant_prefers_query_terms():
    chunks = ["nothing here", "the crm is hubspot now", "unrelated text"]
    sel = chunking.select_relevant(chunks, "what crm hubspot", max_chars=25)
    assert sel == ["the crm is hubspot now"]   # budget fits only the top-ranked chunk


def test_select_relevant_budget_and_order():
    chunks = ["aaa crm", "bbb crm", "ccc crm"]
    sel = chunking.select_relevant(chunks, "crm", max_chars=8)
    assert len(sel) >= 1                       # always at least the best chunk
    idxs = [chunks.index(c) for c in sel]
    assert idxs == sorted(idxs)                # document order restored


def test_select_relevant_empty():
    assert chunking.select_relevant([], "q", max_chars=100) == []


def test_relevant_window():
    win = chunking.relevant_window("intro para.\n\nthe answer is berlin.\n\noutro.",
                                   "answer berlin", max_chars=100)
    assert "berlin" in win


# ----------------------------------------------------------------- question router
@pytest.mark.parametrize("q,expected", [
    ("how many days ago did I move", "temporal-reasoning"),
    ("what is my current crm", "knowledge-update"),
    ("how many gyms have I tried", "multi-session"),
    ("what color is the sky", "default"),
    ("when did I start the new job", "temporal-reasoning"),
])
def test_classify_question(q, expected):
    assert index.classify_question(q) == expected


# ----------------------------------------------------------------- resolve/date
def test_resolve_note_dash_underscore(tmp_path):
    (tmp_path / "session-000.md").write_text("---\nname: session-000\n---\nbody", encoding="utf-8")
    assert index._resolve_note(tmp_path, "session_000.md") is not None   # qmd '_'→'-' tolerance
    assert index._resolve_note(tmp_path, "session-000.md") is not None
    assert index._resolve_note(tmp_path, "missing.md") is None
    assert index._resolve_note(tmp_path, "") is None


def test_note_date_from_frontmatter(tmp_path):
    _write(tmp_path, "n", "body", date="2026-05-01")
    assert index._note_date(tmp_path, "n.md") == "2026-05-01"


def test_note_date_missing(tmp_path):
    _write(tmp_path, "n", "no date in here")
    assert index._note_date(tmp_path, "n.md") == ""


# ----------------------------------------------------------------- context assembly
def test_assemble_context_uses_full_body(tmp_path):
    body = "preamble.\n\nthe deploy password is hunter2 for the staging box.\n\ntrailer."
    _write(tmp_path, "secrets", body, date="2026-06-01")
    hits = [{"rel": "secrets.md", "title": "secrets", "score": 1.0,
             "snippet": "preamble. the deploy...", "engine": "qmd"}]
    ctx = index._assemble_context("staging deploy password", hits, tmp_path, token_budget=2000)
    assert "hunter2" in ctx                    # full body reached, not the truncated snippet
    assert "secrets · 2026-06-01" in ctx


def test_assemble_context_dedupes_parent(tmp_path):
    _write(tmp_path, "note", "alpha content.\n\nbeta content.", date="2026-01-01")
    hits = [
        {"rel": "note.md", "title": "note", "score": 2.0, "snippet": "alpha", "engine": "qmd"},
        {"rel": "note.md", "title": "note", "score": 1.0, "snippet": "beta", "engine": "qmd"},
    ]
    ctx = index._assemble_context("alpha beta", hits, tmp_path, token_budget=2000)
    assert ctx.count("[note") == 1             # same parent note appears once


def test_assemble_context_snippet_fallback(tmp_path):
    hits = [{"rel": "gone.md", "title": "gone", "score": 1.0,
             "snippet": "orphan snippet text", "engine": "qmd"}]
    ctx = index._assemble_context("orphan", hits, tmp_path, token_budget=2000)
    assert "orphan snippet text" in ctx        # unresolvable note → snippet fallback


# ----------------------------------------------------------------- search fallback
def test_direct_scan_fallback(tmp_path, monkeypatch):
    _write(tmp_path, "a", "the quick brown fox")
    _write(tmp_path, "b", "lazy dog sleeps")
    monkeypatch.setattr(index, "_QMD", None)   # force the dependency-free fallback
    hits = index.search("quick fox", tmp_path, k=5)
    assert hits and hits[0]["rel"].startswith("a")
    assert hits[0]["engine"] == "direct-scan"


def test_balanced_prefers_mcp_and_reports_metadata(tmp_path, monkeypatch):
    _write(tmp_path, "decision", "use the dedicated runtime")
    monkeypatch.setattr(index, "qmd_available", lambda: True)
    monkeypatch.setattr(index.qmd_runtime, "maintenance_active", lambda: False)
    monkeypatch.setattr(index.qmd_runtime, "health", lambda: {"ok": True})
    monkeypatch.setattr(index, "_mcp_search", lambda *a: [{
        "file": f"{index.collection_name(tmp_path)}/decision.md",
        "title": "decision", "score": 0.9, "snippet": "dedicated runtime"}])

    report = index.search_report("runtime", tmp_path, retrieval_mode="balanced")

    assert report["engine"] == "qmd-mcp"
    assert report["retrieval_mode"] == "balanced"
    assert report["degraded"] is False
    assert report["hits"][0]["rel"] == "decision.md"


def test_balanced_cli_fallback_is_explicitly_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "qmd_available", lambda: True)
    monkeypatch.setattr(index.qmd_runtime, "maintenance_active", lambda: False)
    monkeypatch.setattr(index, "_mcp_search", lambda *a: (_ for _ in ()).throw(
        RuntimeError("MCP unavailable")))
    monkeypatch.setattr(index, "_cli_search", lambda *a: [{
        "file": f"{index.collection_name(tmp_path)}/note.md",
        "title": "note", "score": 0.7, "snippet": "fallback"}])

    report = index.search_report("fallback", tmp_path)

    assert report["engine"] == "qmd-cli"
    assert report["degraded"] is True
    assert report["reason"] == "mcp_failed:RuntimeError"


def test_maintenance_forces_direct_scan(tmp_path, monkeypatch):
    _write(tmp_path, "note", "maintenance fallback survives")
    monkeypatch.setattr(index.qmd_runtime, "maintenance_active", lambda: True)
    monkeypatch.setattr(index, "qmd_available", lambda: True)
    report = index.search_report("fallback", tmp_path)
    assert report["engine"] == "direct-scan"
    assert report["reason"] == "maintenance_active"


# ----------------------------------------------------------------- ask()
def test_ask_no_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)
    _write(tmp_path, "n", "the capital is oslo.")
    res = index.ask("what is the capital", tmp_path, k=3)
    assert res["answer"] is None
    assert "oslo" in res["context"]
    assert res["context_tokens"] > 0
    assert res["question_type"] == "default"


def test_ask_with_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.setenv("HSM_READER", "fakereader")
    _write(tmp_path, "n", "the capital is oslo.")

    class _R:
        stdout = "reasoning about the context...\nANSWER: Oslo"
        stderr = ""

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        assert cmd == ["fakereader"]
        assert "oslo" in input                 # the reader got the full-body context
        return _R()

    monkeypatch.setattr(index.subprocess, "run", fake_run)
    res = index.ask("what is the capital", tmp_path, k=3)
    assert res["answer"] == "Oslo"             # parsed after the ANSWER: marker


def test_ask_explicit_type_overrides_router(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    _write(tmp_path, "n", "some content here")
    res = index.ask("anything at all", tmp_path, question_type="multi-session")
    assert res["question_type"] == "multi-session"


# ----------------------------------------------------------------- recency rerank
def test_recency_rerank_boosts_newer(tmp_path):
    _write(tmp_path, "old", "crm is salesforce", date="2026-01-01")
    _write(tmp_path, "new", "crm is hubspot", date="2026-06-01")
    hits = [
        {"rel": "old.md", "title": "old", "score": 1.0, "snippet": "", "engine": "qmd"},
        {"rel": "new.md", "title": "new", "score": 1.0, "snippet": "", "engine": "qmd"},
    ]
    reranked = index._recency_rerank(hits, tmp_path)
    assert reranked[0]["rel"].startswith("new")   # equal relevance → newer wins


def test_recency_rerank_no_dates_is_noop(tmp_path):
    _write(tmp_path, "a", "no date")
    _write(tmp_path, "b", "also no date")
    hits = [{"rel": "a.md", "title": "a", "score": 2.0, "snippet": "", "engine": "qmd"},
            {"rel": "b.md", "title": "b", "score": 1.0, "snippet": "", "engine": "qmd"}]
    assert index._recency_rerank(hits, tmp_path) == hits


# --------------------------------------------- audit fixes: security + budget + edges
def test_resolve_note_rejects_traversal(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "evil.md").write_text("secret sibling", encoding="utf-8")   # OUTSIDE the vault
    assert index._resolve_note(vault, "../evil.md") is None                 # traversal rejected
    assert index._resolve_note(vault, str(tmp_path / "evil.md")) is None    # absolute rejected


def test_resolve_note_subdir_dash_underscore(tmp_path):
    sub = tmp_path / "people"
    sub.mkdir()
    (sub / "john_doe.md").write_text("---\nname: john_doe\n---\nbio", encoding="utf-8")
    got = index._resolve_note(tmp_path, "people/john-doe.md")   # qmd normalized '_'→'-' in a subdir
    assert got is not None and got.name == "john_doe.md"


def test_resolve_note_normalized_directory_and_result_rel(tmp_path):
    sub = tmp_path / "Brands" / "FuckBigTech"
    sub.mkdir(parents=True)
    note = sub / "fuckbigtech_canonical_state.md"
    note.write_text("canonical", encoding="utf-8")

    got = index._resolve_note(tmp_path, "brands/fuckbigtech/fuckbigtech-canonical-state.md")
    assert got == note.resolve()

    results = index._normalize_qmd_results([{
        "file": f"{index.collection_name(tmp_path)}/brands/fuckbigtech/fuckbigtech-canonical-state.md",
        "title": "canonical", "score": 1.0, "snippet": "canonical",
    }], tmp_path, index.collection_name(tmp_path), "qmd-mcp", "balanced", False, None)
    assert results[0]["rel"] == "Brands/FuckBigTech/fuckbigtech_canonical_state.md"
    assert results[0]["path"] == str(note.resolve())


def test_relevant_window_respects_budget():
    body = "the answer is X. " * 300           # ~5100 chars, one paragraph
    win = chunking.relevant_window(body, "answer X", max_chars=300)
    assert 0 < len(win) <= 300                  # the always-kept best chunk is capped to budget


def test_assemble_context_continue_past_oversized(tmp_path):
    # snippet-fallback path (unresolvable notes) uses the raw snippet, which is NOT
    # length-capped — a huge middle snippet must not drop a later small one (continue,
    # not break).
    hits = [
        {"rel": "a.md", "title": "a", "score": 3.0, "snippet": "alpha fact", "engine": "qmd"},
        {"rel": "b.md", "title": "b", "score": 2.0, "snippet": "X" * 5000, "engine": "qmd"},
        {"rel": "c.md", "title": "c", "score": 1.0, "snippet": "gamma fact three", "engine": "qmd"},
    ]
    ctx = index._assemble_context("fact", hits, tmp_path, token_budget=300)   # char_budget 1200
    assert "alpha fact" in ctx
    assert "gamma fact three" in ctx            # 'b' overflows the budget but 'c' still fits


def test_ask_unknown_type_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    _write(tmp_path, "n", "some content")
    res = index.ask("anything", tmp_path, question_type="nonsense")
    assert res["question_type"] == "default"    # bad input from any surface → safe default


# --------------------------------------------- second-pass fixes (answer parse / date / gate / sep)
def test_ask_answer_parsing_ignores_reasoning(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.setenv("HSM_READER", "fakereader")
    _write(tmp_path, "n", "the crm is hubspot.")

    class _R:
        stdout = "Let me answer: the values were salesforce then hubspot.\nANSWER: hubspot"
        stderr = ""

    monkeypatch.setattr(index.subprocess, "run", lambda *a, **k: _R())
    res = index.ask("what is the crm", tmp_path)
    assert res["answer"] == "hubspot"           # the sentinel line, not the 'answer:' in reasoning


def test_ask_temporal_injects_current_date(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.setenv("HSM_READER", "fakereader")
    _write(tmp_path, "n", "I started the new job on 2026-01-01.")
    captured = {}

    class _R:
        stdout = "ANSWER: a while"
        stderr = ""

    def fake_run(cmd, input=None, **k):
        captured["prompt"] = input
        return _R()

    monkeypatch.setattr(index.subprocess, "run", fake_run)
    index.ask("how many days ago did I start the job", tmp_path, question_type="temporal-reasoning")
    assert "CURRENT DATE:" in captured["prompt"]
    assert datetime.date.today().isoformat() in captured["prompt"]


def test_ask_recency_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    _write(tmp_path, "n", "current crm hubspot; the first item recorded was alpha")
    calls = []
    real = index._recency_rerank
    monkeypatch.setattr(index, "_recency_rerank", lambda hits, v: (calls.append(1) or real(hits, v)))
    index.ask("what is the current crm", tmp_path, question_type="knowledge-update")
    assert calls                                # knowledge-update → recency-boosted
    calls.clear()
    index.ask("what was the first item recorded", tmp_path, question_type="temporal-reasoning")
    assert not calls                            # temporal 'first' must NOT be recency-boosted


def test_relevant_window_multichunk_budget():
    body = ("x" * 100) + "\n\n" + ("y" * 100)   # two 100-char chunks
    win = chunking.relevant_window(body, "x y", max_chars=201, chunk_chars=100)
    assert len(win) <= 201                      # '\n\n' separator counted → 200+2 would overflow


def test_strip_frontmatter_keeps_thematic_break():
    assert "Real Title" in chunking.strip_frontmatter("---\n# Real Title\n---\nbody content")
    assert chunking.strip_frontmatter("---\nname: x\nstatus: hot\n---\n\nbody") == "body"
