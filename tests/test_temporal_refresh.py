from __future__ import annotations

from datetime import date

from homestead_memory.core import remember, resolve, temporal


def test_remember_refreshes_temporal_history_on_fresh_vault(tmp_path):
    assert temporal.history("user", vault=tmp_path) == []

    remember.remember("User", "city", "Berlin", vault=tmp_path,
                      agent="agent-a", session="sess-a")

    rows = temporal.history("user", vault=tmp_path)
    assert len(rows) >= 1
    assert rows[0]["field"] == "city"
    assert rows[0]["new_val"] == "Berlin"
    assert rows[0]["agent"] == "agent-a"
    assert rows[0]["session"] == "sess-a"
    assert rows[0]["ts"] is not None


def test_remember_update_refreshes_newest_temporal_change(tmp_path):
    remember.remember("User", "city", "Paris", vault=tmp_path,
                      agent="agent-a", session="sess-a")
    remember.remember("User", "city", "Berlin", vault=tmp_path,
                      agent="agent-b", session="sess-b")

    rows = temporal.history("user", vault=tmp_path)
    assert rows[0]["field"] == "city"
    assert rows[0]["old_val"] == "Paris"
    assert rows[0]["new_val"] == "Berlin"
    assert rows[0]["agent"] == "agent-b"
    assert rows[0]["session"] == "sess-b"


def test_resolve_refreshes_temporal_history(tmp_path):
    today = date.today().isoformat()
    (tmp_path / "distilled").mkdir()
    (tmp_path / "distilled" / "user.md").write_text(
        f"---\nname: user\ntype: distilled\nentity: User\nupdated: {today}\n---\n\n"
        "# User\n\n"
        "- city: Berlin (source: remember)\n"
        "- city: Tokyo (source: remember)\n\n"
        "## Changelog\n"
        "- 2026-07-01: recorded city: \"Berlin\" (source: remember) "
        "[agent=agent-a session=sess-a ts=2026-07-01T10:00:00+00:00]\n"
        "- 2026-07-02: update city: \"Berlin\" -> \"Tokyo\" (source: remember) "
        "[agent=agent-b session=sess-b ts=2026-07-02T10:00:00+00:00]\n",
        encoding="utf-8",
    )

    resolve.resolve("User", vault=tmp_path, field="city",
                    agent="resolver", session="resolve-sess")

    rows = temporal.history("user", vault=tmp_path)
    resolved = [r for r in rows if "resolved city:" in r["text"]]
    assert len(resolved) == 1
    assert resolved[0]["field"] == "city"
    assert resolved[0]["new_val"] == "Tokyo"
    assert resolved[0]["agent"] == "resolver"
    assert resolved[0]["session"] == "resolve-sess"


def test_incremental_refresh_matches_full_temporal_build(tmp_path):
    remember.remember("User", "city", "Paris", vault=tmp_path,
                      agent="agent-a", session="sess-a")
    remember.remember("User", "city", "Berlin", vault=tmp_path,
                      agent="agent-b", session="sess-b")

    incremental = temporal.history("distilled/user.md", vault=tmp_path)
    rep = temporal.build(tmp_path)
    rebuilt = temporal.history("distilled/user.md", vault=tmp_path)

    assert rep["entries"] == len(incremental)
    assert rebuilt == incremental
