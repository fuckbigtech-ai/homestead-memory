"""The install snippet must produce a hook that actually fires.

This file exists because the shipped snippet used a bare `hsm`, and that is a silent
failure, not a loud one. Measured on a clean machine: `python -m venv` +
`pip install homestead-memory` puts `hsm` inside the venv only. The shell the harness
spawns for a PostToolUse hook does not have the venv active, so `hsm hook` is not
found, the hook fails, and PostToolUse failures are invisible to the user. The ledger
stays empty and `hsm watch` used to respond "install the hook" - telling someone who
had just installed it to install it again.

A tool whose entire pitch is "your agent reported success while doing nothing" cannot
ship that in its own onboarding. The repo had already shipped this defect once: a
sibling hook read env vars the harness never sent and quietly did nothing for weeks.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from homestead_memory import cli


def _snippet(capsys) -> dict:
    """Run `hsm hook --install` and parse the JSON block back out of stdout."""
    assert cli.cmd_hook_install(None) == 0
    out = capsys.readouterr().out
    m = re.search(r"\{.*\n\}", out, re.S)
    assert m, f"no JSON object found in output:\n{out}"
    return json.loads(m.group(0))


def _command(snippet: dict) -> str:
    return snippet["hooks"]["PostToolUse"][0]["hooks"][0]["command"]


def test_the_hook_command_names_a_real_executable(capsys):
    """The bug. A bare name is not evidence that the hook can run."""
    cmd = _command(_snippet(capsys))
    argv = shlex.split(cmd, posix=(os.name != "nt"))
    assert argv, f"empty command: {cmd!r}"
    exe = argv[0].strip('"')

    assert argv[1:] == ["hook"], f"expected `<hsm> hook`, got {argv!r}"
    assert Path(exe).is_absolute(), (
        f"command is {cmd!r}. A relative or bare name only works if the hook's shell "
        f"happens to have it on PATH, which a venv install does not guarantee. When it "
        f"does not, the hook fails silently and the ledger stays empty."
    )
    assert Path(exe).exists(), f"resolved hsm path does not exist: {exe}"


def test_a_path_with_spaces_survives_the_shell(monkeypatch, tmp_path, capsys):
    """An absolute path is only a fix if it is still one word to the shell.

    Homebrew and system installs are fine, but plenty of real machines have a space
    somewhere in the path ("/Users/A B/..", "Application Support"). Unquoted, that
    turns into two argv entries and fails exactly as silently as the bare name did.
    """
    spaced = tmp_path / "my tools" / "bin" / "hsm"
    spaced.parent.mkdir(parents=True)
    spaced.write_text("#!/bin/sh\n")
    monkeypatch.setattr(cli, "_hsm_executable", lambda: str(spaced))

    cmd = _command(_snippet(capsys))
    argv = shlex.split(cmd, posix=(os.name != "nt"))
    assert argv[0] == str(spaced), f"quoting lost the path: {cmd!r} -> {argv!r}"
    assert argv[1:] == ["hook"]


def test_the_explicit_timeout_is_still_there(capsys):
    """Regression guard: the harness default is 600s.

    A hook inheriting that would stall a session for ten minutes if it ever hung, so
    the explicit short timeout is load-bearing and easy to drop in a refactor.
    """
    hook = _snippet(capsys)["hooks"]["PostToolUse"][0]["hooks"][0]
    assert hook["timeout"] == 5, "explicit timeout lost; would inherit the 600s default"
    assert _snippet(capsys)["hooks"]["PostToolUse"][0]["matcher"] == "*", (
        "matcher must stay '*' or the ledger silently covers only some tools"
    )


def test_output_explains_why_the_path_is_absolute(capsys):
    """Nobody should 'tidy' the absolute path back into a bare name."""
    cli.cmd_hook_install(None)
    out = capsys.readouterr().out
    assert "deliberate" in out.lower() or "WARNING" in out, (
        "the snippet gives no reason for the absolute path, so a reader will shorten it"
    )


# --- the other half: `hsm watch` must not misdiagnose an empty ledger --------------


def test_watch_does_not_tell_an_installed_user_to_install(monkeypatch, tmp_path, capsys):
    """The misleading message. Configured-but-not-firing is the dangerous state.

    The user believes every tool call is being recorded. Nothing is. Telling them to
    install the hook hides the real cause and they walk away thinking the tool is
    broken - on launch day, in public.
    """
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "hooks": {"PostToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "hsm hook"}]}
        ]}
    }), encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    cli._explain_no_records(0)
    out = capsys.readouterr().out

    assert "is configured" in out.lower() or "not firing" in out.lower(), out
    assert "install the hook with" not in out, (
        "told a user who has the hook configured to install the hook"
    )


def test_watch_still_says_install_when_nothing_is_configured(monkeypatch, tmp_path, capsys):
    """The opposite case must not regress into the diagnostic message."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.chdir(tmp_path)

    cli._explain_no_records(0)
    out = capsys.readouterr().out
    assert "install the hook" in out, out


def test_watch_distinguishes_filtered_out_from_empty(capsys):
    """A filter that matches nothing is not an install problem."""
    cli._explain_no_records(42)
    out = capsys.readouterr().out
    assert "42" in out and "filter" in out.lower(), out
    assert "install the hook" not in out, (
        "a non-empty ledger was reported as if recording had never started"
    )


def test_malformed_settings_never_crash_the_diagnosis(monkeypatch, tmp_path):
    """Someone's settings.json will be invalid JSON, or a list, or unreadable.

    Diagnosis is best-effort. It must degrade to "not configured", never raise, or a
    broken config file turns `hsm watch` into a traceback.
    """
    d = tmp_path / ".claude"
    d.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.chdir(tmp_path)

    for bad in ("{not json", "[]", '"a string"', "null", '{"hooks": []}',
                '{"hooks": {"PostToolUse": null}}', '{"hooks": {"PostToolUse": [null]}}'):
        (d / "settings.json").write_text(bad, encoding="utf-8")
        assert cli._hook_configured_in() is None, f"bad config treated as configured: {bad}"
