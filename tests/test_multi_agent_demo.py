from __future__ import annotations

from examples import multi_agent_demo


def test_multi_agent_demo_end_to_end(capsys):
    rc = multi_agent_demo.run()

    out = capsys.readouterr().out
    assert rc == 0
    assert "1. INTACT" in out
    assert "2. ROT" in out
    assert "duplicate_value" in out
    assert "agent=claude" in out
    assert "agent=codex" in out
    assert "3. RESOLVE" in out
    assert "4. INTACT" in out
    assert "MEMORY INTACT" in out
    assert "N agents, one verified memory" in out
