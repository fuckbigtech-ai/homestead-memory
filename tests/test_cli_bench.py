"""CLI surface + benchmark unit helpers (no model calls, no qmd)."""
from pathlib import Path

from homestead_memory.benchmarks import longmemeval as lme
from homestead_memory.cli import main


def test_cli_init_scaffolds(tmp_path, capsys):
    rc = main(["init", str(tmp_path / "v")])
    assert rc == 0
    assert (tmp_path / "v" / "welcome.md").exists()
    assert (tmp_path / "v" / ".hsmignore").exists()


def test_cli_verify_clean_and_rot(tmp_path):
    root = tmp_path / "v"
    main(["init", str(root)])
    assert main(["verify", str(root), "--quiet"]) == 0
    (root / "rot.md").write_text(
        "---\nname: rot\nstatus: hot\nmetadata:\n  status: done\n---\nx\n")
    assert main(["verify", str(root), "--quiet"]) == 1     # nonzero on rot


# ------------------------------------------------------- benchmark unit bits
def test_resolve_note_tolerates_dash_underscore(tmp_path):
    (tmp_path / "session-000.md").write_text("x")
    assert lme._resolve_note(tmp_path, "session-000.md") is not None
    assert lme._resolve_note(tmp_path, "session_000.md") is not None   # qmd '_'→'-' URI
    assert lme._resolve_note(tmp_path, "missing-001.md") is None


def test_span_extracts_relevant_turns():
    text = ("**user:** I upgraded my RAM to 16GB.\n"
            "**assistant:** Nice.\n"
            "**user:** unrelated coffee chat.\n")
    out = lme._span(text, "how much RAM did I upgrade", 500)
    assert "16GB" in out and "coffee" not in out


def test_scores_correct_handles_int_gold_and_rewording():
    assert lme.scores_correct("The answer is 2 sessions", 2) is True   # int gold
    assert lme.scores_correct("Berlin", "berlin") is True
    assert lme.scores_correct("no idea", "16GB") is False


def test_est_tokens_rough():
    assert lme._est_tokens("a" * 400) == 100


def test_build_question_vault_hyphen_names(tmp_path):
    item = {"haystack_sessions": [[{"role": "user", "content": "hi"}]],
            "haystack_dates": ["2026-01-01"]}
    lme.build_question_vault(item, tmp_path)
    assert (tmp_path / "session-000.md").exists()          # hyphen: round-trips qmd URIs
