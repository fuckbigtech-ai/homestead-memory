"""The claims gate must not rot silently, and must not cry wolf.

Why this exists. `scripts/published_claims_gate.py` compares the commands our published
copy names against the commands the PUBLISHED wheel actually provides. It found two live
defects the day it was written: 0.4.0 on PyPI had neither `hsm checkpoint` nor
`hsm import-session`, while the README, ROADMAP, ROTBENCH.md and the launch copy named
both, and `verify.py` told users to run `hsm ledger checkpoint`, which has never existed.

Two ways a gate like this dies, and both are silent:

1. **It stops seeing commands.** It reads subcommands by regex over `sub.add_parser("x")`.
   Change that call shape and the gate finds nothing, compares nothing, and passes
   everything. The script raises on an empty result rather than returning a clean sweep,
   and that behaviour is pinned here.
2. **It starts crying wolf.** A loose `hsm \\w+` grep matches prose ("hsm and the ledger"),
   flags (`hsm --version`), and typos. Noise gets a gate deleted within a month. Claims are
   therefore intersected with the real local subcommand set, which is tested here against
   text containing exactly those traps.

The network half is deliberately untested. It was verified by running the gate against
0.4.0 (fails, naming both commands) and against 0.5.0 (passes), which is stronger evidence
than a mock of PyPI would be.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _gate():
    spec = importlib.util.spec_from_file_location(
        "published_claims_gate", ROOT / "scripts" / "published_claims_gate.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_it_reads_the_real_subcommands_from_this_tree():
    """If the add_parser shape changes, this fails loudly instead of passing vacuously."""
    names = _gate().local_subcommands()
    # Spot-check the commands whose absence from a release caused the incident.
    for expected in ("checkpoint", "import-session", "watch", "verify", "export"):
        assert expected in names, f"gate cannot see `hsm {expected}`"
    assert len(names) >= 15


def test_prose_and_flags_are_not_mistaken_for_claims(tmp_path):
    """The trap text that a bare `hsm \\w+` grep would flag. None of it is a claim."""
    gate = _gate()
    doc = tmp_path / "d.md"
    doc.write_text(
        "hsm and the ledger work together.\n"
        "Run `hsm --version` to check.\n"
        "Use hsm frobnicate if you enjoy inventing commands.\n"
        "The hsm tool is small.\n",
        encoding="utf-8",
    )
    found = gate.claims([doc], {"watch", "verify", "checkpoint"})
    assert found == {}, f"gate would have cried wolf on {sorted(found)}"


def test_a_real_command_in_real_copy_is_detected(tmp_path):
    """The positive case, including inside a fenced block, which is where copy lives."""
    gate = _gate()
    doc = tmp_path / "launch.md"
    doc.write_text(
        "Getting started:\n\n```\npip install homestead-memory\nhsm import-session\n```\n\n"
        "Then run hsm watch to see it.\n",
        encoding="utf-8",
    )
    found = gate.claims([doc], {"watch", "import-session", "verify"})
    assert set(found) == {"import-session", "watch"}
    assert str(doc) in found["import-session"]


def test_directories_are_walked(tmp_path):
    """`--also <dir>` must reach nested copy, since launch docs live in folders."""
    gate = _gate()
    nested = tmp_path / "content" / "deep"
    nested.mkdir(parents=True)
    (nested / "post.md").write_text("run hsm checkpoint --export\n", encoding="utf-8")
    found = gate.claims([tmp_path], {"checkpoint"})
    assert "checkpoint" in found


@pytest.mark.parametrize("missing", ["checkpoint", "import-session"])
def test_the_incident_shape_is_what_gets_reported(missing):
    """Both commands that shipped-0.4.0 lacked are visible to the gate on this tree.

    This is the regression guard for the actual 2026-09-11 incident: the copy named them,
    the published wheel did not have them, and nothing noticed for nine days.
    """
    assert missing in _gate().local_subcommands()
