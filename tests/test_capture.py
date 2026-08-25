"""Capture-layer tests: what reaches disk, and what must never.

Every credential below is FABRICATED. The point of these tests is that a payload
containing something key-shaped produces a ledger record that does not contain it,
while still producing a digest that proves what the original was.

The redaction pattern set is not trusted on its own. Measured on one real machine on
2026-07-31, an earlier version of it caught 1 of 8 actual credential files. Truncation
and hashing are the defences that do not depend on the pattern list being complete, so
they are tested independently.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from homestead_memory.core import capture, ledger

# (label, text, the fabricated secret substring that must NOT survive)
SECRET_SHAPES = [
    ("openai",        "OPENAI_API_KEY=sk-" + "a" * 40,                  "a" * 40),
    ("anthropic",     "sk-ant-" + "b" * 40,                             "b" * 40),
    ("github_pat",    "ghp_" + "c" * 36,                                "c" * 36),
    ("aws",           "AKIA" + "D" * 16,                                "D" * 16),
    ("google",        "AIza" + "e" * 35,                                "e" * 35),
    ("stripe",        "live_mode_api_key = 'rk_live_" + "f" * 30 + "'", "f" * 30),
    ("slack",         "xoxb-" + "1" * 24,                               "1" * 24),
    ("env_assign",    "OPENROUTER_API_KEY=" + "g" * 50,                 "g" * 50),
    ("lower_toml",    "client_secret = '" + "h" * 40 + "'",             "h" * 40),
    ("oauth_json",    '{"refresh_token": "' + "i" * 60 + '"}',          "i" * 60),
]


@pytest.mark.parametrize("label,text,secret", SECRET_SHAPES, ids=[s[0] for s in SECRET_SHAPES])
def test_every_claimed_shape_is_redacted(label, text, secret):
    out, n = capture.redact(text)
    assert secret not in out, f"{label}: the secret survived redaction"
    assert n >= 1


def test_pem_body_is_redacted_not_just_the_header():
    """Caught in testing: matching only the BEGIN line left the base64 key material,
    which is the part that actually matters."""
    pem = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
           "MIIEowIBAAKCAQEAsecretkeymaterial\n"
           "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU=\n"
           "-----END OPENSSH PRIVATE KEY-----")
    out, _ = capture.redact(pem)
    assert "MIIEow" not in out and "b3BlbnNz" not in out


def test_unterminated_pem_is_still_redacted():
    """Truncation may cut the END marker off before redaction ever sees it."""
    out, _ = capture.redact("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAsecret")
    assert "MIIEow" not in out


def test_keypair_byte_array_is_redacted():
    arr = "[" + ",".join(str(n % 256) for n in range(64)) + "]"
    out, n = capture.redact(arr)
    assert n == 1 and "<redacted>" in out


def test_redaction_keeps_the_variable_name():
    """Knowing WHICH credential appeared is the debugging signal. Only the value goes."""
    out, _ = capture.redact("OPENAI_API_KEY=sk-" + "a" * 40)
    assert "OPENAI_API_KEY" in out and "a" * 40 not in out


def test_code_that_reads_an_env_var_is_not_mangled():
    """`X = process.env.Y` is not a secret. Over-redaction destroys the debugging value
    the ledger exists to provide."""
    for benign in ("API_KEY=${OPENAI_API_KEY}", "token = os.environ['T']",
                   "export KEY=$(vault read secret)"):
        out, n = capture.redact(benign)
        assert n == 0, f"false positive on: {benign!r} -> {out!r}"


def test_hash_is_of_the_original_so_evidence_survives_redaction():
    """If we hashed the redacted text the digest would be unverifiable against the real
    output, which destroys the whole reason the hash is there."""
    secret = "OPENAI_API_KEY=sk-" + "z" * 40
    s = capture.summarize(secret)
    assert s["sha256"] == hashlib.sha256(secret.encode()).hexdigest()
    assert "z" * 40 not in json.dumps(s)
    assert s["bytes"] == len(secret.encode())


def test_truncation_bounds_anything_the_patterns_miss():
    """The defence that does not depend on being clever."""
    unknown_shape = "TOTALLY_NOVEL_CREDENTIAL_FORMAT " + "q" * 5000
    s = capture.summarize(unknown_shape)
    assert len(s["head"]) <= capture.HEAD_CHARS
    assert s["truncated"] is True
    assert s["bytes"] == len(unknown_shape.encode())


def test_payload_mapping_accepts_both_field_spellings():
    """A hook on this machine silently did nothing for weeks because it assumed one
    input shape. Take both."""
    snake = capture.from_hook_payload({
        "tool_name": "Bash", "tool_input": {"command": "npm test"},
        "tool_response": "ok", "session_id": "s1", "tool_use_id": "t1"})
    camel = capture.from_hook_payload({
        "toolName": "Bash", "toolInput": {"command": "npm test"},
        "toolResponse": "ok", "sessionId": "s1", "toolUseId": "t1"})
    for m in (snake, camel):
        assert m["action"] == "tool_call"
        assert m["target"] == "Bash"
        assert m["summary"] == "npm test"
        assert m["session"] == "s1"


def test_unknown_tool_still_gets_a_useful_target():
    m = capture.from_hook_payload({"tool_name": "SomeFutureTool",
                                   "tool_input": {"url": "https://example.com/x"}})
    assert m["target"] == "SomeFutureTool"
    assert "example.com" in (m["summary"] or "")


def test_a_secret_in_a_bash_command_does_not_become_the_summary():
    """`command` is the target for Bash and also the field most likely to carry an
    inline secret."""
    m = capture.from_hook_payload({
        "tool_name": "Bash",
        "tool_input": {"command": "export OPENAI_API_KEY=sk-" + "a" * 40 + " && deploy"}})
    assert "a" * 40 not in (m["summary"] or "")


# --- the hook entry point --------------------------------------------------

def _run_hook(monkeypatch, capsys, stdin_text, vault, env=None):
    """Invoke the CLI exactly as the harness would."""
    import io
    from homestead_memory import cli

    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    rc = cli.main(["hook", str(vault)])
    capsys.readouterr()
    return rc


@pytest.mark.parametrize("bad", [
    "not json at all {{{", "", "   ", "\x00\x01\x02", "[]", "null",
    '{"tool_name": null}', '{"tool_input": {"command": "x"}}',
], ids=["malformed", "empty", "whitespace", "nullbytes", "array", "null", "nulltool", "notool"])
def test_hook_never_fails_a_session(monkeypatch, capsys, tmp_path, bad):
    """This runs after every tool call. A traceback here is worse than the feature is
    good. PostToolUse cannot block, but a noisy hook gets uninstalled."""
    assert _run_hook(monkeypatch, capsys, bad, tmp_path) == 0


def test_hook_records_a_real_payload(monkeypatch, capsys, tmp_path):
    payload = json.dumps({
        "session_id": "sess-abc", "cwd": "/repo", "hook_event_name": "PostToolUse",
        "tool_name": "Bash", "tool_input": {"command": "npm test"},
        "tool_response": "3 passing", "tool_use_id": "toolu_01"})
    assert _run_hook(monkeypatch, capsys, payload, tmp_path) == 0

    recs = ledger.read_all(tmp_path)
    assert len(recs) == 1
    assert recs[0]["target"] == "Bash" and recs[0]["summary"] == "npm test"
    assert recs[0]["meta"]["response"]["sha256"] == hashlib.sha256(b"3 passing").hexdigest()
    assert ledger.verify_chain(tmp_path) == []


def test_a_secret_in_tool_response_never_reaches_the_ledger_file(monkeypatch, capsys, tmp_path):
    """The end-to-end version of the claim the product makes."""
    secret = "sk-" + "z" * 40
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/repo/.env"},
                          "tool_response": f"OPENAI_API_KEY={secret}"})
    _run_hook(monkeypatch, capsys, payload, tmp_path)

    on_disk = (tmp_path / ledger.LEDGER_REL).read_text(encoding="utf-8")
    assert secret not in on_disk, "a credential reached the ledger file"
    assert "OPENAI_API_KEY" in on_disk, "the variable NAME should survive for debugging"


def test_env_fallback_is_used_when_stdin_is_empty(monkeypatch, capsys, tmp_path):
    rc = _run_hook(monkeypatch, capsys, "", tmp_path,
                   env={"CLAUDE_TOOL_NAME": "Bash", "CLAUDE_TOOL_INPUT": '{"command": "ls"}'})
    assert rc == 0
    recs = ledger.read_all(tmp_path)
    assert len(recs) == 1 and recs[0]["target"] == "Bash"


def test_a_failed_append_is_recorded_as_a_drop_not_silently_lost(tmp_path):
    """An audit log with invisible gaps is the defect this product sells against."""
    ledger.record_drop("vault lock timed out", vault=tmp_path)
    drops = ledger.read_drops(tmp_path)
    assert len(drops) == 1 and "lock" in drops[0]["reason"]


def test_watch_exits_nonzero_when_the_log_has_known_gaps(monkeypatch, capsys, tmp_path):
    from homestead_memory import cli

    ledger.append("tool_call", target="Bash", vault=tmp_path)
    assert cli.main(["watch", str(tmp_path)]) == 0
    capsys.readouterr()

    ledger.record_drop("simulated failure", vault=tmp_path)
    assert cli.main(["watch", str(tmp_path)]) == 1, "known gaps must not report success"
    capsys.readouterr()
