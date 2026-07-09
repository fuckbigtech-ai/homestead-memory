import hashlib
import json
import re
import socket

from homestead_memory import cli
from homestead_memory.core import distill, provenance, temporal, verify


RAW = """---
name: chat1
status: reference
updated: 2026-07-01
---
**user:** I'm allergic to penicillin, please remember that.
**assistant:** Noted.
"""


def _fx(facts_by_rel):
    def fn(rel, body):
        return facts_by_rel.get(rel, [])
    return fn


def _fact():
    return {"entity": "User", "field": "allergy", "value": "penicillin",
            "fact": "User allergy = penicillin",
            "quote": "I'm allergic to penicillin, please remember that."}


def _body_text(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)


def test_distill_stamps_changelog_and_citations_with_provenance(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW)
    distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [_fact()]}),
                    agent="agent one", session="session one")

    note = (tmp_path / "distilled" / "user.md").read_text()
    line = next(ln for ln in note.splitlines() if "recorded allergy" in ln)
    parsed = provenance.parse_token(line)
    assert parsed["agent"] == "agent_one"
    assert parsed["session"] == "session_one"
    assert parsed["ts"].endswith("+00:00")

    cites = json.loads((tmp_path / ".hsm" / "citations.json").read_text())
    c = cites["user::allergy"]
    assert c["agent"] == parsed["agent"]
    assert c["session"] == parsed["session"]
    assert c["ts"] == parsed["ts"]
    assert c["sha256"] == hashlib.sha256(_body_text(RAW).encode()).hexdigest()


def test_temporal_history_recovers_provenance(tmp_path, capsys):
    (tmp_path / "chat1.md").write_text(RAW)
    distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [_fact()]}),
                    agent="agent-a", session="sess-a")
    temporal.build(tmp_path)

    rows = temporal.history("user", vault=tmp_path)
    assert rows[0]["agent"] == "agent-a"
    assert rows[0]["session"] == "sess-a"
    assert rows[0]["ts"].endswith("+00:00")

    assert cli.main(["history", "user", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "agent=agent-a session=sess-a" in out


def test_old_changelog_without_token_parses_and_scores_same(tmp_path):
    for name, suffix in (("old", ""), ("new", " [agent=a session=s ts=2026-07-09T00:00:00+00:00]")):
        root = tmp_path / name
        root.mkdir()
        (root / "a.md").write_text(
            "---\nname: a\nstatus: reference\nupdated: 2026-07-01\n---\nevidence\n")
        (root / "user.md").write_text(
            "---\nname: user\ntype: distilled\nupdated: 2026-07-01\n---\n\n# User\n\n"
            "- crm: HubSpot (source: a.md)\n\n## Changelog\n"
            f"- 2026-07-01: recorded crm: \"HubSpot\" (source: a.md){suffix}\n")

    old_entries = temporal.parse_changelog((tmp_path / "old" / "user.md").read_text())
    assert old_entries[0]["agent"] is None
    assert old_entries[0]["session"] is None
    assert old_entries[0]["ts"] is None

    old_rep = verify.verify_vault(tmp_path / "old")
    new_rep = verify.verify_vault(tmp_path / "new")
    assert old_rep["score"] == new_rep["score"] == 100
    assert old_rep["ok"] and new_rep["ok"]


def test_env_defaults_hostname_and_cli_agent_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HSM_AGENT", "env agent")
    monkeypatch.setenv("HSM_SESSION", "env session")
    assert provenance.resolve_agent() == "env_agent"
    assert provenance.resolve_session() == "env_session"

    monkeypatch.delenv("HSM_AGENT")
    monkeypatch.setattr(provenance.socket, "gethostname", lambda: "host name")
    assert provenance.resolve_agent() == "host_name"

    seen = {}
    def fake_distill(path, model=None, dry=False, agent=None):
        seen.update({"path": path, "model": model, "dry": dry, "agent": agent})
        return {"dry": dry, "scanned": 0, "changed": 0, "facts": 0, "dropped": 0,
                "failed_notes": 0, "entities_created": 0, "entities_updated": 0,
                "changelog_lines": 0}

    monkeypatch.setattr(distill, "distill", fake_distill)
    assert cli.main(["distill", str(tmp_path), "--agent", "cli agent"]) == 0
    assert seen["agent"] == "cli agent"


def test_default_agent_is_hostname(tmp_path, monkeypatch):
    monkeypatch.delenv("HSM_AGENT", raising=False)
    monkeypatch.setenv("HSM_SESSION", "stable")
    monkeypatch.setattr(provenance.socket, "gethostname", lambda: "test host")
    (tmp_path / "chat1.md").write_text(RAW)

    distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [_fact()]}))
    note = (tmp_path / "distilled" / "user.md").read_text()
    line = next(ln for ln in note.splitlines() if "recorded allergy" in ln)
    assert provenance.parse_token(line)["agent"] == "test_host"


def test_sha256_in_citations_matches_source_body(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW)
    distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [_fact()]}),
                    agent="a", session="s")
    cites = json.loads((tmp_path / ".hsm" / "citations.json").read_text())
    assert cites["user::allergy"]["sha256"] == hashlib.sha256(_body_text(RAW).encode()).hexdigest()
    assert len(cites["user::allergy"]["sha256"]) == 64
