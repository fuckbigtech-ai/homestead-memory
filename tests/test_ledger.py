"""Tamper-evidence tests for the agent ledger.

These are the load-bearing tests for the whole evidence claim. If someone can edit,
delete, or reorder a record and `verify_chain` still returns clean, then the file is
a log with extra steps and every downstream claim about Article 12 evidence is false.

So each test performs the actual attack against a real file on disk rather than
asserting on a mock, and requires the break to be reported at the RIGHT index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homestead_memory.core import ledger


@pytest.fixture()
def vault(tmp_path):
    (tmp_path / "note.md").write_text("---\nname: n\n---\n", encoding="utf-8")
    for i in range(5):
        ledger.append("tool_call", target=f"tool{i}", summary=f"step {i}", vault=tmp_path)
    return tmp_path


def _lines(v):
    return (v / ledger.LEDGER_REL).read_text(encoding="utf-8").splitlines()


def _write(v, lines):
    (v / ledger.LEDGER_REL).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_intact_chain_verifies(vault):
    assert ledger.verify_chain(vault) == []
    assert len(ledger.read_all(vault)) == 5


def test_genesis_and_linkage(vault):
    recs = ledger.read_all(vault)
    assert recs[0]["prev_hash"] == ledger.GENESIS_HASH
    for prev, cur in zip(recs, recs[1:]):
        assert cur["prev_hash"] == prev["hash"], "each record must name its predecessor"
        assert cur["seq"] == prev["seq"] + 1


def test_editing_a_record_in_place_is_detected_at_that_index(vault):
    """The canonical attack: change what the record says and leave everything else."""
    lines = _lines(vault)
    rec = json.loads(lines[2])
    rec["summary"] = "something the agent never did"
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    _write(vault, lines)

    breaks = ledger.verify_chain(vault)
    assert breaks, "an edited record must not verify"
    assert any(b.index == 2 and b.kind == "hash_mismatch" for b in breaks), \
        f"expected hash_mismatch at index 2, got {breaks}"


def test_deleting_a_record_is_detected(vault):
    """Deletion is the attack a plain append-only log cannot see: what is gone leaves
    no trace unless each record names its predecessor."""
    lines = _lines(vault)
    del lines[2]
    _write(vault, lines)

    breaks = ledger.verify_chain(vault)
    assert breaks, "a deleted record must not verify"
    assert any(b.kind == "prev_mismatch" for b in breaks)
    assert any(b.kind == "seq_gap" for b in breaks), "the seq gap names what was removed"


def test_reordering_records_is_detected(vault):
    lines = _lines(vault)
    lines[1], lines[3] = lines[3], lines[1]
    _write(vault, lines)

    breaks = ledger.verify_chain(vault)
    assert breaks, "reordered records must not verify"
    assert any(b.kind in ("prev_mismatch", "seq_gap") for b in breaks)


def test_torn_final_line_is_reported_not_silently_dropped(vault):
    """A crash mid-append leaves half a line. read_all skips it so the tool stays
    usable, but verification must still say it happened."""
    p = vault / ledger.LEDGER_REL
    with p.open("a", encoding="utf-8") as f:
        f.write('{"seq":5,"action":"tool_ca')      # truncated write

    breaks = ledger.verify_chain(vault)
    assert any(b.kind == "torn" for b in breaks), \
        "a truncated record must be reported, never silently ignored"


def test_append_after_a_torn_line_still_chains_from_the_last_intact_record(vault):
    """The tool must keep working after a crash. The new record chains from the last
    record that actually survived, so the tear is isolated rather than fatal."""
    intact_head = ledger.head_hash(vault)
    with (vault / ledger.LEDGER_REL).open("a", encoding="utf-8") as f:
        f.write('{"seq":5,"action":"tor')

    rec = ledger.append("tool_call", target="after_crash", vault=vault)
    assert rec["prev_hash"] == intact_head
    kinds = {b.kind for b in ledger.verify_chain(vault)}
    assert "torn" in kinds


def test_a_fully_rebuilt_chain_is_internally_consistent_which_is_why_signing_exists(vault):
    """Honesty test for the limits of hashing alone.

    An attacker who rewrites EVERY record can recompute every hash, and the chain
    will verify. That is not a bug in verify_chain, it is the reason `checkpoint`
    signs the head with a key the rewriter does not have. This test documents the
    boundary so nobody later mistakes chain-verification for proof of authenticity.
    """
    forged = []
    prev = ledger.GENESIS_HASH
    for i in range(3):
        rec = {"v": ledger.LEDGER_VERSION, "seq": i, "ts": "2026-01-01T00:00:00Z",
               "agent": "attacker", "session": "x", "action": "tool_call",
               "target": "fake", "summary": f"forged {i}", "meta": {}, "prev_hash": prev}
        rec["hash"] = ledger.record_hash(rec)
        prev = rec["hash"]
        forged.append(json.dumps(rec, sort_keys=True, separators=(",", ":")))
    _write(vault, forged)

    assert ledger.verify_chain(vault) == [], "a wholly rebuilt chain IS self-consistent"
    # ...which is exactly why the signature layer is not optional.


def test_concurrent_appends_do_not_fork_the_chain(vault):
    """Two writers must not read the same head and both chain from it."""
    import threading

    errors: list[BaseException] = []

    def writer(n):
        try:
            for i in range(5):
                ledger.append("tool_call", target=f"t{n}-{i}", vault=vault)
        except BaseException as e:     # noqa: BLE001 - surfaced via the assert below
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent appends raised: {errors}"
    assert ledger.verify_chain(vault) == [], "concurrent appends must not fork the chain"
    recs = ledger.read_all(vault)
    assert [r["seq"] for r in recs] == list(range(len(recs))), "sequence must stay dense"


# --- signature layer -------------------------------------------------------
# verify_chain proves nobody edited/removed a record. It CANNOT catch someone who
# rebuilt the whole file, because a rebuilt chain is self-consistent by
# construction (see the test above). These cover the layer that closes that hole.

def _sig_available():
    try:
        from homestead_memory.core import signing
        signing._ed25519()
        return True
    except Exception:
        return False


sig_required = pytest.mark.skipif(not _sig_available(),
                                  reason="requires the optional [sign] extra (cryptography)")


@sig_required
def test_checkpoint_verifies_an_untouched_ledger(vault, tmp_path):
    sig = ledger.checkpoint(vault=vault, key_path=tmp_path / "k")
    assert sig["records"] == 5
    ok, why = ledger.verify_checkpoint(vault)
    assert ok, why


@sig_required
def test_appending_after_a_checkpoint_is_valid_but_reported_as_uncovered(vault, tmp_path):
    """Ordinary use must not read as tampering, or operators learn to ignore it."""
    ledger.checkpoint(vault=vault, key_path=tmp_path / "k")
    ledger.append("tool_call", target="later", vault=vault)
    ok, why = ledger.verify_checkpoint(vault)
    assert ok, "growth past a checkpoint is not tampering"
    assert "appended since" in why, f"must say the new records are uncovered: {why}"


@sig_required
def test_checkpoint_catches_a_wholly_rebuilt_chain(vault, tmp_path):
    """The attack verify_chain provably cannot see."""
    ledger.checkpoint(vault=vault, key_path=tmp_path / "k")

    forged, prev = [], ledger.GENESIS_HASH
    for i in range(3):
        rec = {"v": ledger.LEDGER_VERSION, "seq": i, "ts": "2026-01-01T00:00:00Z",
               "agent": "attacker", "session": "x", "action": "tool_call",
               "target": "fake", "summary": f"forged {i}", "meta": {}, "prev_hash": prev}
        rec["hash"] = ledger.record_hash(rec)
        prev = rec["hash"]
        forged.append(json.dumps(rec, sort_keys=True, separators=(",", ":")))
    _write(vault, forged)

    assert ledger.verify_chain(vault) == [], "precondition: the forgery is self-consistent"
    ok, _why = ledger.verify_checkpoint(vault)
    assert not ok, "the signature must reject a rebuilt chain the hash chain accepts"


@sig_required
def test_missing_checkpoint_is_reported_not_treated_as_success(vault):
    ok, why = ledger.verify_checkpoint(vault)
    assert not ok and "no ledger checkpoint" in why


@sig_required
def test_unexpected_signer_is_rejected(vault, tmp_path):
    """This branch reports tampering, so it must not itself be broken. It was:
    the first version called a helper that lives in verify.py, not signing.py,
    and would have raised AttributeError instead of returning a verdict."""
    ledger.checkpoint(vault=vault, key_path=tmp_path / "k")
    ok, why = ledger.verify_checkpoint(vault, expect_pubkey="ab" * 32)
    assert not ok
    assert "unexpected key" in why


# --- `hsm watch --demo` ------------------------------------------------------------
#
# The README embeds a recording of this command, so it is a published claim about what
# the tool does. Two ways that becomes a lie: the demo renders its own output and drifts
# from the real renderer, or it stages a break it did not actually cause. Both are
# asserted against here.


def _run_demo_capturing(capsys):
    from homestead_memory import cli
    code = cli.run_watch_demo()
    return code, capsys.readouterr()


def test_the_demo_actually_breaks_the_chain(capsys):
    """Nonzero exit, and the break names the record that was edited."""
    code, out = _run_demo_capturing(capsys)
    assert code == 1, "a demo that exits 0 is not demonstrating tamper-evidence"
    assert "chain break at index 1" in out.err, out.err
    assert "hash_mismatch" in out.err


def test_the_demo_leaves_nothing_behind(capsys):
    """It runs in a TemporaryDirectory and must not touch the user's vault."""
    import re
    _, out = _run_demo_capturing(capsys)
    leaked = [Path(p) for p in re.findall(r"/\S*fbt-watch-demo-\S+", out.out + out.err)]
    assert not any(p.exists() for p in leaked), f"demo left files on disk: {leaked}"


def test_the_demo_uses_the_same_renderer_as_real_watch(tmp_path, capsys):
    """The anti-drift test.

    If the demo ever formats rows itself, its output can diverge from what `hsm watch`
    prints and the README GIF becomes a fabricated screenshot. Compare the column shape
    of a demo row against a row rendered from a real ledger.
    """
    import re
    from homestead_memory import cli

    ledger.append("tool_call", target="Bash", summary="npm test", vault=tmp_path)
    cli._print_ledger_rows(ledger.read_all(tmp_path))
    real = capsys.readouterr().out.strip("\n")

    _, out = _run_demo_capturing(capsys)
    demo_rows = [ln for ln in out.out.splitlines() if re.match(r"^\s+\d+\s+\d\d:\d\d:\d\d\s", ln)]
    assert demo_rows, f"no ledger rows found in demo output:\n{out.out}"

    shape = lambda s: re.sub(r"[^\s]", "x", s)      # noqa: E731 - column geometry only
    assert shape(demo_rows[0])[:24] == shape(real)[:24], (
        f"demo row geometry differs from real watch output:\n"
        f"  demo: {demo_rows[0]!r}\n  real: {real!r}"
    )


def test_break_is_reported_after_the_rows_it_refers_to(capsys):
    """stderr is unbuffered, stdout is not.

    Without an explicit flush the warning jumps ahead of the records that caused it in
    every pipe, every CI log, and the demo recording. Found while building the demo.
    """
    from homestead_memory import cli
    ledger_out = []

    class _Tee:
        def __init__(self, name): self.name = name
        def write(self, s): ledger_out.append((self.name, s)); return len(s)
        def flush(self): pass

    import sys as _sys
    old_out, old_err = _sys.stdout, _sys.stderr
    _sys.stdout, _sys.stderr = _Tee("out"), _Tee("err")
    try:
        cli.run_watch_demo()
    finally:
        _sys.stdout, _sys.stderr = old_out, old_err

    order = [name for name, s in ledger_out if s.strip()]
    assert "err" in order, "no stderr written; the demo did not report a break"
    first_err = order.index("err")
    assert "out" in order[:first_err], "the break was written before any records"


# --- record_phase: proving enforcement, not just observation ------------------------
#
# draft-sharif-agent-audit-trail-01 section 4.1: "a denial that is only logged after
# execution provides no evidence that the denial was enforced". A PostToolUse-only
# ledger is therefore evidence of observation and nothing more, which is a weaker claim
# than this project makes for it. Capturing the decision phase is what closes that.


def test_phase_is_recorded_for_both_sides_of_an_action(tmp_path):
    from homestead_memory.core import capture
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
           "tool_input": {"command": "rm -rf build"}, "session_id": "s1"}
    post = {**pre, "hook_event_name": "PostToolUse", "tool_response": {"ok": True}}
    for payload in (pre, post):
        ledger.append(vault=tmp_path, **capture.from_hook_payload(payload))

    recs = ledger.read_all(tmp_path)
    assert [r["phase"] for r in recs] == [ledger.PHASE_PRE, ledger.PHASE_POST]
    assert ledger.verify_chain(tmp_path) == []


def test_phase_is_inferred_when_the_harness_does_not_say(tmp_path):
    """A PreToolUse payload has no tool_response, because the tool has not run.

    The harness is trusted when it names the event and the payload shape is used when it
    does not, rather than depending on one signal. A sibling hook on this machine once
    silently did nothing for weeks by assuming a single input shape.
    """
    from homestead_memory.core import capture
    assert capture.from_hook_payload({"tool_name": "Bash"})["phase"] == ledger.PHASE_PRE
    assert capture.from_hook_payload(
        {"tool_name": "Bash", "tool_response": {"ok": 1}})["phase"] == ledger.PHASE_POST
    # An explicit event name outranks the shape.
    assert capture.from_hook_payload(
        {"hook_event_name": "PreToolUse", "tool_name": "B",
         "tool_response": {"ok": 1}})["phase"] == ledger.PHASE_PRE


def test_records_written_before_phase_existed_still_verify(tmp_path):
    """The compatibility guarantee, proven rather than assumed.

    `phase` is written only when known, so a pre-0.4.0 record simply lacks the key and
    hashes exactly as it did. If this ever fails, shipping the phase field silently
    invalidated every ledger already on disk.
    """
    ledger.append("tool_call", target="Bash", summary="old record", vault=tmp_path)
    ledger.append("tool_call", target="Edit", summary="also old", vault=tmp_path)

    recs = ledger.read_all(tmp_path)
    assert all("phase" not in r for r in recs), "phase must be absent when not supplied"
    assert ledger.verify_chain(tmp_path) == []

    # A phase-carrying record chains onto phase-less history without breaking it.
    ledger.append("tool_call", target="Read", summary="new", vault=tmp_path,
                  phase=ledger.PHASE_PRE)
    assert ledger.verify_chain(tmp_path) == []
    assert ledger.read_all(tmp_path)[-1]["phase"] == ledger.PHASE_PRE
