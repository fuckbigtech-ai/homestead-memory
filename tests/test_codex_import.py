"""Codex session import: the first non-Claude-Code harness.

It is an IMPORT and not a hook on purpose. Measured 2026-09-09: Codex's PreToolUse and
PostToolUse did not fire at all under `codex exec`, while its rollout files record every
call with input, output and a pairing call_id.

The "shell commands only" caveat in Codex's migration docs is about porting Claude Code
hooks, where Read/Edit/Write are separate tools. Codex has none of those. Measured across
60 real sessions and 848 tool calls, `exec` is 799 of them (94%), because Codex does its
file work by shelling out. Shell coverage is near-total coverage for Codex.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homestead_memory.core import codex_import as ci
from homestead_memory.core import ledger


def _rollout(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "rollout-2026-09-09T00-00-00-abc123.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _call(call_id, name="exec", inp="ls -la"):
    return {"type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": call_id,
                        "name": name, "input": inp}}


def _out(call_id, text="done"):
    return {"type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": call_id,
                        "output": text}}


def test_a_call_becomes_two_records_paired_on_call_id(tmp_path):
    """Both phases, like the Claude Code hook. One row would lose the decision/outcome
    distinction that makes the record evidence of enforcement rather than observation."""
    p = _rollout(tmp_path, [_call("c1"), _out("c1")])
    recs = ci.records_from_rollout(p)
    assert [r["phase"] for r in recs] == ["pre_execution", "post_execution"]
    assert {r["meta"]["call_id"] for r in recs} == {"c1"}
    assert all(r["target"] == "exec" for r in recs)


def test_every_imported_record_says_it_was_imported(tmp_path):
    """The load-bearing honesty property. A row read from a file the harness wrote later
    must never be indistinguishable from one witnessed as it happened."""
    recs = ci.records_from_rollout(_rollout(tmp_path, [_call("c1"), _out("c1")]))
    assert recs, "no records produced"
    for r in recs:
        assert r["meta"]["source"] == "codex-rollout"
        assert r["meta"]["harness"] == "codex"
        assert r["agent"] == "codex"


def test_an_output_with_no_matching_call_is_recorded_not_dropped(tmp_path):
    """A truncated or rotated rollout can open mid-turn. Silently dropping the orphan
    would be an invisible gap, which is the failure mode this project exists to catch."""
    recs = ci.records_from_rollout(_rollout(tmp_path, [_out("ghost")]))
    assert len(recs) == 1
    assert recs[0]["meta"]["orphan"] is True


def test_secrets_in_a_rollout_do_not_reach_the_ledger(tmp_path):
    """Rollouts contain real command output, so the same redaction the hook uses applies."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    p = _rollout(tmp_path, [_call("c1", inp=f"export AWS_SECRET_ACCESS_KEY={secret}")])
    recs = ci.records_from_rollout(p)
    assert secret not in json.dumps(recs), "a credential-shaped value survived import"
    assert recs[0]["meta"]["sha256"], "the digest of the original must survive redaction"


def test_a_torn_final_line_does_not_abort_the_import(tmp_path):
    """A live session's last line is often half-written."""
    p = _rollout(tmp_path, [_call("c1"), _out("c1")])
    p.write_text(p.read_text() + '{"type": "response_item", "payl', encoding="utf-8")
    assert len(ci.records_from_rollout(p)) == 2


def test_imported_records_chain_like_any_other(tmp_path):
    """Imported rows are ordinary ledger rows. If they did not hash-chain, the ledger
    would be split into trusted and untrusted halves."""
    (tmp_path / ".hsm").mkdir(parents=True, exist_ok=True)
    for r in ci.records_from_rollout(_rollout(tmp_path, [_call("c1"), _out("c1")])):
        ledger.append(r.pop("action"), vault=tmp_path, **r)
    assert len(ledger.read_all(tmp_path)) == 2
    assert ledger.verify_chain(tmp_path) == []


def test_import_session_did_not_clobber_the_existing_import_command():
    """`hsm import` already existed and brings MEMORIES into the vault from Mem0/Zep/OKF.

    The session importer was first written as `cmd_import`, which would have shadowed it
    silently because it is defined later in the module. Two different nouns, two commands.
    """
    import argparse

    from homestead_memory import cli

    subs = [a for a in cli.build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)][0]
    assert subs.choices["import"].get_default("func").__name__ == "cmd_import"
    assert subs.choices["import-session"].get_default("func").__name__ == "cmd_import_session"


def test_missing_codex_installation_is_not_an_error(tmp_path):
    """Most users do not have Codex. Discovery returns nothing rather than raising."""
    assert ci.find_rollouts(home=tmp_path / "nope") == []
