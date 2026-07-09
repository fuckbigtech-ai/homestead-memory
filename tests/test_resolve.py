from __future__ import annotations

import contextlib
import json
from datetime import date

from homestead_memory import cli
from homestead_memory.api import mcp_server as mcp
from homestead_memory.core import distill, remember, resolve, store, verify


def _write_conflict(root, *, tokenized=True):
    (root / "distilled").mkdir(exist_ok=True)
    today = date.today().isoformat()
    suffix_a = " [agent=claude session=s1 ts=2026-07-01T10:00:00+00:00]" if tokenized else ""
    suffix_b = " [agent=codex session=s2 ts=2026-07-01T11:00:00+00:00]" if tokenized else ""
    (root / "distilled" / "user.md").write_text(
        f"---\nname: user\ntype: distilled\nentity: User\nupdated: {today}\n---\n\n"
        "# User\n\n"
        "- city: Berlin (source: remember)\n"
        "- city: Tokyo (source: remember)\n\n"
        "## Changelog\n"
        f"- 2026-07-01: recorded city: \"Berlin\" (source: remember){suffix_a}\n"
        f"- 2026-07-02: update city: \"Berlin\" -> \"Tokyo\" (source: remember){suffix_b}\n",
        encoding="utf-8",
    )


def _write_distilled(root, body: str):
    (root / "distilled").mkdir(exist_ok=True)
    today = date.today().isoformat()
    (root / "distilled" / "user.md").write_text(
        f"---\nname: user\ntype: distilled\nentity: User\nupdated: {today}\n---\n\n"
        f"# User\n\n{body}",
        encoding="utf-8",
    )


def test_resolve_latest_picks_newest_provenance_and_verify_passes(tmp_path):
    _write_conflict(tmp_path)

    before = verify.verify_vault(tmp_path)
    assert any(f.check == "duplicate_value" for f in before["fails"])

    res = resolve.resolve("User", vault=tmp_path, field="city", agent="resolver", session="rs")

    assert res["note"] == "distilled/user.md"
    assert res["resolved"] == [{
        "field": "city",
        "winner": "Tokyo",
        "winner_ts": "2026-07-01T11:00:00+00:00",
        "losers": ["Berlin"],
        "strategy": "latest",
    }]
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert note.count("- city:") == 1
    assert "- city: Tokyo (source: resolve)" in note
    assert 'recorded city: "Berlin"' in note
    assert 'update city: "Berlin" -> "Tokyo"' in note
    assert 'resolved city: kept "Tokyo" over "Berlin" (source: resolve)' in note
    assert "[agent=resolver session=rs ts=" in note
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_resolve_equal_timestamp_tie_picks_later_body_value(tmp_path):
    same_ts = " [agent=codex session=same ts=2026-07-01T10:00:00+00:00]"
    _write_distilled(
        tmp_path,
        "- city: Zurich (source: remember)\n"
        "- city: Amsterdam (source: remember)\n\n"
        "## Changelog\n"
        f'- 2026-07-01: recorded city: "Zurich" (source: remember){same_ts}\n'
        f'- 2026-07-01: update city: "Zurich" -> "Amsterdam" (source: remember){same_ts}\n',
    )

    res = resolve.resolve("User", vault=tmp_path, field="city", agent="resolver")

    assert res["resolved"][0]["winner"] == "Amsterdam"
    assert res["resolved"][0]["losers"] == ["Zurich"]
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- city: Amsterdam (source: resolve)" in note
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_resolve_keep_both_merges_values_and_passes_verify(tmp_path):
    _write_conflict(tmp_path)

    res = resolve.resolve("User", vault=tmp_path, field="city", strategy="keep-both",
                          agent="resolver")

    item = res["resolved"][0]
    assert item["winner"] == "Berlin | Tokyo"
    assert item["losers"] == []
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- city: Berlin | Tokyo (source: resolve)" in note
    assert 'resolved city: merged "Berlin", "Tokyo" (source: resolve)' in note
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_quote_containing_values_remember_resolve_and_verify(tmp_path):
    remember.remember("User", "nickname", 'Alice "Ace"', vault=tmp_path, agent="claude")
    remember.remember("User", "nickname", 'Bob "Bee"', vault=tmp_path, agent="codex")
    note_path = tmp_path / "distilled" / "user.md"
    text = note_path.read_text()
    text = text.replace("- nickname: Bob 'Bee' (source: remember)\n",
                        "- nickname: Alice 'Ace' (source: remember)\n"
                        "- nickname: Bob 'Bee' (source: remember)\n")
    note_path.write_text(text, encoding="utf-8")

    res = resolve.resolve("User", vault=tmp_path, field="nickname", agent="resolver")

    assert res["resolved"][0]["winner"] == "Bob 'Bee'"
    note = note_path.read_text()
    assert "- nickname: Bob 'Bee' (source: resolve)" in note
    assert 'Bob "Bee"' not in note
    assert 'resolved nickname: kept "Bob \'Bee\'" over "Alice \'Ace\'" (source: resolve)' in note
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_resolve_falls_back_to_changelog_date_for_old_notes(tmp_path):
    _write_conflict(tmp_path, tokenized=False)

    res = resolve.resolve("User", vault=tmp_path, field="city", agent="resolver")

    assert res["resolved"][0]["winner"] == "Tokyo"
    assert res["resolved"][0]["winner_ts"] == "2026-07-02"
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_verify_ignores_spoofed_resolve_keep_without_source_resolve(tmp_path):
    (tmp_path / "a.md").write_text(
        "---\nname: a\nupdated: 2026-07-01\n---\nsource evidence\n",
        encoding="utf-8",
    )
    _write_distilled(
        tmp_path,
        "- x: Y (source: a.md)\n\n"
        "## Changelog\n"
        '- 2026-07-01: recorded x: "X" (source: a.md)\n'
        '- 2026-07-02: resolved x: kept "Y" over "X"\n',
    )

    rep = verify.verify_vault(tmp_path)

    assert any(f.check == "temporal_mismatch" for f in rep["fails"])


def test_resolve_uppercase_field_conflict_and_verify_passes(tmp_path):
    _write_distilled(
        tmp_path,
        "- City: Berlin (source: remember)\n"
        "- City: Tokyo (source: remember)\n\n"
        "## Changelog\n"
        '- 2026-07-01: recorded City: "Berlin" (source: remember)\n'
        '- 2026-07-02: update City: "Berlin" -> "Tokyo" (source: remember)\n',
    )

    res = resolve.resolve("User", vault=tmp_path, field="city", agent="resolver")

    assert res["resolved"][0]["field"] == "city"
    assert res["resolved"][0]["winner"] == "Tokyo"
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- city: Tokyo (source: resolve)" in note
    assert "- City:" not in note
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_resolve_no_conflict_is_noop(tmp_path):
    remember.remember("User", "city", "Berlin", vault=tmp_path, agent="claude")
    before = (tmp_path / "distilled" / "user.md").read_text()

    res = resolve.resolve("User", vault=tmp_path, field="city", agent="resolver")

    assert res["note"] == "distilled/user.md"
    assert res["resolved"] == []
    assert (tmp_path / "distilled" / "user.md").read_text() == before


def test_resolve_missing_note_is_noop(tmp_path):
    assert resolve.resolve("Missing", vault=tmp_path)["note"] is None


def test_resolve_uses_lock_and_atomic_write(tmp_path, monkeypatch):
    _write_conflict(tmp_path)
    calls = {"lock": 0, "atomic": []}
    real_atomic = store.atomic_write

    @contextlib.contextmanager
    def fake_lock(v, *args, **kwargs):
        calls["lock"] += 1
        yield

    def fake_atomic(path, text):
        calls["atomic"].append(path)
        real_atomic(path, text)

    monkeypatch.setattr(resolve.store, "vault_lock", fake_lock)
    monkeypatch.setattr(resolve.store, "atomic_write", fake_atomic)

    resolve.resolve("User", vault=tmp_path, field="city")

    assert calls["lock"] == 1
    assert tmp_path / "distilled" / "user.md" in calls["atomic"]
    assert tmp_path / ".hsm" / "citations.json" in calls["atomic"]


def test_cli_resolve_prints_winner_and_losers(tmp_path, capsys):
    _write_conflict(tmp_path)

    rc = cli.main(["resolve", "User", str(tmp_path), "--field", "city",
                   "--agent", "resolver"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "city: kept Tokyo over Berlin (latest)" in out


def test_mcp_memory_resolve_tool(tmp_path):
    _write_conflict(tmp_path)
    state = mcp.ServerState(tmp_path)
    state.initialized = True

    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "memory_resolve",
                      "arguments": {"entity": "User", "field": "city",
                                    "agent": "resolver"}}}
    res = mcp.handle_message(msg, state)

    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["resolved"][0]["winner"] == "Tokyo"
    assert verify.verify_vault(tmp_path)["ok"] is True


def test_resolve_preserves_other_fields(tmp_path):
    _write_conflict(tmp_path)
    text = (tmp_path / "distilled" / "user.md").read_text()
    text = text.replace("- city: Tokyo (source: remember)\n",
                        "- city: Tokyo (source: remember)\n- crm: HubSpot (source: remember)\n")
    (tmp_path / "distilled" / "user.md").write_text(text, encoding="utf-8")

    resolve.resolve("User", vault=tmp_path, field="city")

    fields = distill._parse_distilled((tmp_path / "distilled" / "user.md").read_text())
    assert fields["crm"] == ("HubSpot", "remember")
