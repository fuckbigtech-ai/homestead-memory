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
    assert "chain break at index 3" in out.err, out.err
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

    # Must carry a phase: demo rows do, and a record without one renders a blank
    # phase column. Comparing a phased row against an unphased one would fail on a
    # real difference rather than on drift, which is not what this test is for.
    ledger.append("tool_call", target="Bash", summary="npm test", vault=tmp_path,
                  phase=ledger.PHASE_PRE)
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


def test_watch_distinguishes_the_two_phases(tmp_path, capsys):
    """A pre/post pair must not render as two identical rows.

    0.4.0 made `hook --install` write BOTH a PreToolUse and a PostToolUse entry, so
    every tool call produces two records. The renderer did not show the phase, which
    meant a user saw the same line twice with no explanation, and the field that makes
    the record evidence of enforcement rather than of observation was invisible in the
    tool's own output. Shipped that way; caught while re-recording the demo GIF.
    """
    from homestead_memory import cli

    for phase in (ledger.PHASE_PRE, ledger.PHASE_POST):
        ledger.append("tool_call", target="Edit", summary="src/api/billing.py",
                      vault=tmp_path, phase=phase)

    cli._print_ledger_rows(ledger.read_all(tmp_path))
    rows = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(rows) == 2, rows
    assert " pre " in rows[0], rows[0]
    assert " post " in rows[1], rows[1]
    assert rows[0] != rows[1], "the two phases must be distinguishable"


def test_records_without_a_phase_still_render(tmp_path, capsys):
    """Pre-0.4.0 ledgers carry no phase and must render cleanly, not 'None'."""
    from homestead_memory import cli

    ledger.append("tool_call", target="Bash", summary="npm test", vault=tmp_path)
    cli._print_ledger_rows(ledger.read_all(tmp_path))
    out = capsys.readouterr().out
    assert "None" not in out, out
    assert "npm test" in out


def _recommended_commands(text: str) -> list[str]:
    """Pull every `hsm ...` command string a message tells the user to run."""
    import re
    return [m.group(1).strip() for m in re.finditer(r"`hsm ([^`]+)`", text)]


def test_every_command_we_tell_users_to_run_actually_exists(vault, tmp_path):
    """The tool must not name a command that its own parser cannot accept.

    `hsm verify` warned about a missing checkpoint and pointed at a "ledger checkpoint"
    verify_checkpoint repeats it, but there is no `ledger` subcommand and no `checkpoint`
    subcommand that never existed, so the user was told the exact hole in the guarantee
    and then sent nowhere.

    This is the same defect class as the hook snippet that shipped a bare `hsm`: a message
    that points somewhere that is not there. Asserting it here means it cannot recur.
    """
    import argparse
    from homestead_memory import cli

    parser = cli.build_parser()
    messages = []

    _, why = ledger.verify_checkpoint(vault)
    messages.append(why)

    from homestead_memory.core import verify as verify_mod
    # deep=True is load-bearing: _ledger_checks runs ONLY under deep, so the default
    # returned an empty finding list and this loop scanned nothing. A guard that passes
    # on an empty list is not a guard.
    for f in verify_mod.verify_vault(vault, deep=True).get("findings", []):
        messages.append(f"{f.get('note', '')} {f.get('detail', '')}")

    scanned = sum(len(_recommended_commands(m)) for m in messages)
    assert scanned, "no `hsm ...` recommendation was scanned; the guard would pass vacuously"

    for msg in messages:
        for cmd in _recommended_commands(msg):
            try:
                parser.parse_args(cmd.split())
            except SystemExit:
                raise AssertionError(
                    f"the tool tells users to run `hsm {cmd}`, which the parser rejects.\n"
                    f"  message: {msg}"
                )


@sig_required
def test_checkpoint_is_reachable_from_the_cli(vault, tmp_path):
    """The guarantee is only real if a user can invoke it.

    ledger.checkpoint() is implemented and covered by four tests, but shipped with no CLI
    command at all, which made the one mechanism that catches a wholly rebuilt chain
    unreachable in practice.
    """
    from homestead_memory import cli

    args = cli.build_parser().parse_args(["checkpoint", str(vault)])
    assert hasattr(args, "func"), "checkpoint parsed but is bound to no handler"
    assert args.func(args) == 0
    assert (Path(vault) / ledger.CHECKPOINT_REL).exists(), "checkpoint wrote no file"


@sig_required
def test_a_forged_chain_LONGER_than_the_checkpoint_is_caught(vault, tmp_path):
    """The checkpoint must verify the head is still a PREFIX, not just count records.

    verify_checkpoint compared record counts only, so a forgery that replaced every
    record and then grew past the checkpoint returned True with "valid as of N records;
    M appended since". Reproduced: 5 checkpointed records replaced by 9 fabricated ones
    verified clean, which made the README's "catches a wholly rebuilt chain" claim false
    for any forgery longer than the checkpoint, i.e. the normal case since ledgers grow.
    """
    for t in ("Bash", "Read", "Edit", "Write", "Grep"):
        ledger.append("tool_call", target=t, summary="x", vault=vault)
    ledger.checkpoint(vault)

    lp = Path(vault) / ledger.LEDGER_REL
    recs = [json.loads(l) for l in lp.read_text().splitlines()]
    for r in recs:
        r["summary"] = "FORGED"
    recs += [dict(recs[-1]) for _ in range(4)]          # grow PAST the checkpoint
    prev = ledger.GENESIS_HASH
    for i, r in enumerate(recs):                         # re-chain so it is self-consistent
        r["seq"] = i
        r["prev_hash"] = prev
        r.pop("hash", None)
        r["hash"] = ledger.record_hash(r)
        prev = r["hash"]
    lp.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in recs) + "\n")

    assert ledger.verify_chain(vault) == [], "a re-chained forgery is internally consistent"
    ok, why = ledger.verify_checkpoint(vault)
    assert not ok, f"a forged chain longer than the checkpoint verified clean: {why}"
    assert "prefix" in why or "does not match" in why, why


@sig_required
def test_checkpoint_signed_by_an_unexpected_key_is_rejected_through_verify(vault, tmp_path):
    """Pinning a signer must reach the LEDGER checkpoint, not only the vault signature.

    verify.py threaded expect_pubkey into signing.verify_signature but never into
    ledger.verify_checkpoint, so an attacker who rebuilt the chain could sign it with a
    freshly generated key of their own and `hsm verify --signer <real key>` reported zero
    ledger findings.

    Asserted through verify_vault at deep=False since v1.5, which is strictly stronger:
    it pins the behaviour of the command people actually run, not of a helper.
    """
    from homestead_memory.core import verify as verify_mod

    ledger.append("tool_call", target="Bash", summary="npm test", vault=vault)
    ledger.checkpoint(vault)

    attacker = tmp_path / "attacker_key"
    ledger.checkpoint(vault, key_path=attacker)          # re-sign under a DIFFERENT key

    rep = verify_mod.verify_vault(vault, deep=False, expect_pubkey="00" * 32)
    sig_findings = [f for f in rep["findings"] if "ledger" in f["check"]]
    assert sig_findings, "pinning a key produced no ledger finding for a foreign signer"


@sig_required
def test_records_appended_after_the_checkpoint_are_not_covered(vault, tmp_path):
    """Known and documented limit: a checkpoint protects its PREFIX, not the future.

    Found by an adversarial pass on the prefix-check fix. Everything after the last
    checkpoint can be replaced with a correctly re-chained forgery and still verify,
    because a signature cannot cover records that did not exist when it was made.

    This is inherent rather than a defect, so it is pinned here and stated in the README
    instead of being quietly true. Re-checkpoint often, or the uncovered tail grows.
    """
    for i in range(5):
        ledger.append("tool_call", target="Bash", summary=f"real{i}", vault=vault)
    n = ledger.checkpoint(vault)["records"]      # derive the boundary; the fixture may
    for i in range(4):                            # already hold records of its own
        ledger.append("tool_call", target="Edit", summary=f"after{i}", vault=vault)

    lp = Path(vault) / ledger.LEDGER_REL
    recs = [json.loads(l) for l in lp.read_text().splitlines()]
    for r in recs[n:]:
        r["summary"] = "FORGED AFTER CHECKPOINT"
    prev = recs[n - 1]["hash"]
    for r in recs[n:]:
        r["prev_hash"] = prev
        r.pop("hash", None)
        r["hash"] = ledger.record_hash(r)
        prev = r["hash"]
    lp.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in recs) + "\n")

    ok, why = ledger.verify_checkpoint(vault)
    assert ok, "the checkpointed prefix is intact, so this must not be reported as tampering"
    assert "appended since" in why, why


def _rebuild_chain(v, summary="FORGED"):
    """Rebuild the whole ledger from scratch the way a COMPETENT attacker would.

    Genesis matters. A first attempt at this used prev_hash=None and verify_chain caught
    it at index 0 with `bad_genesis`, which looked like the chain detecting the attack
    and was really the chain detecting a sloppy forgery. Use the real sentinel so the
    rebuilt file is internally perfect and the test proves what it claims.
    """
    recs = [json.loads(x) for x in _lines(v)]
    prev = recs[0]["prev_hash"]                       # whatever the real genesis is
    for r in recs:
        r["summary"] = summary
        r["prev_hash"] = prev
        r.pop("hash", None)
        r["hash"] = ledger.record_hash(r)
        prev = r["hash"]
    _write(v, [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in recs])
    assert ledger.verify_chain(v) == [], "the rebuild must be internally perfect"


@sig_required
def test_a_rebuilt_chain_is_caught_by_a_published_attestation(vault):
    """The export line is only worth printing if something can check a ledger against it."""
    sig = ledger.checkpoint(vault)
    line = (f"hsm-checkpoint v1 head={sig['head_hash']} records={sig['records']}"
            f" ts={sig['ts']} pubkey={sig['signer_pubkey']} sig={sig['signature']}")

    ok, _ = ledger.verify_attestation(line, vault)
    assert ok, "an untouched ledger must verify against its own attestation"

    _rebuild_chain(vault)
    ok, why = ledger.verify_attestation(line, vault)
    assert not ok, "a wholly rebuilt chain must fail against the published line"
    assert "head hash does not match" in why or "not a prefix" in why, why


def test_a_malformed_attestation_is_a_parse_error_not_a_failed_check():
    """Distinct outcomes. Paste half a line from a gist and the honest answer is 'that is
    not an attestation', not 'your ledger was tampered with'."""
    for bad in ["", "nope", "hsm-checkpoint v1 head=aa", "hsm-checkpoint v1 head=a "
                "records=NaN ts=t pubkey=p sig=s"]:
        with pytest.raises(ValueError):
            ledger.parse_attestation(bad)


@sig_required
def test_watch_catches_a_rebuilt_chain(vault, capsys):
    """`watch` is the daily command, and until 0.4.2 it could not see this attack at all.

    verify_chain returns clean on a correct rebuild BY DESIGN, so a command that only
    reported chain breaks was blind to the exact thing the checkpoint exists to catch.
    """
    from homestead_memory import cli

    ledger.checkpoint(vault)
    _rebuild_chain(vault)
    args = cli.build_parser().parse_args(["watch", str(vault)])
    assert cli.cmd_watch(args) == 1, "watch must exit nonzero on a rebuilt chain"
    assert "checkpoint does not verify" in capsys.readouterr().err


@sig_required
def test_watch_names_checkpoint_when_there_is_none(vault, capsys):
    from homestead_memory import cli

    cli.cmd_watch(cli.build_parser().parse_args(["watch", str(vault)]))
    err = capsys.readouterr().err
    assert "hsm checkpoint" in err, "the daily command must name the command that fixes this"


@sig_required
def test_verify_reports_ledger_findings_WITHOUT_deep(vault):
    """The regression this guards: ledger checks were --deep only through v1.4.

    A broken chain is inadmissible, and the plain `hsm verify` is what people run. A
    defect the default command cannot see is a defect the tool does not really check.
    """
    from homestead_memory.core import verify

    lines = _lines(vault)
    rec = json.loads(lines[2]); rec["summary"] = "tampered"
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    _write(vault, lines)

    rep = verify.verify_vault(vault, deep=False)
    assert any(f["check"] == "ledger_chain" for f in rep["findings"]), \
        "a tampered ledger must be reported without --deep"


@sig_required
def test_an_unpinned_signer_is_reported_but_does_not_move_the_score(vault):
    """It describes the INVOCATION, not the ledger.

    Scoring it would mean a byte-identical ledger scores differently depending on whether
    the caller passed --signer, which is the same defect the v1.4 `not_indexed` correction
    fixed after CI and the author's machine disagreed by exactly 8 points.
    """
    from homestead_memory.core import verify

    ledger.checkpoint(vault)
    rep = verify.verify_vault(vault, deep=False)
    assert any(f["check"] == "ledger_signer_unpinned" for f in rep["findings"]), "must be reported"
    assert "ledger_signer_unpinned" in verify.ADVISORY_CHECKS, "must not be scored"


@sig_required
def test_verify_does_not_say_PASS_about_a_ledger_that_is_broken_now(vault, capsys):
    """The attestation answers ONE question: does the published prefix still match.

    It says nothing about records AFTER the checkpoint, so a chain broken past it returned
    `PASS  valid as of N records` while verify_chain reported a break. That is the worst
    possible place for a false pass, because `--verify` is what you run when you already
    suspect the machine. Same defect as the EvidencePack verifier that printed "signed"
    directly under `signature: ABSENT`.
    """
    from homestead_memory import cli

    sig = ledger.checkpoint(vault)
    line = (f"hsm-checkpoint v1 head={sig['head_hash']} records={sig['records']}"
            f" ts={sig['ts']} pubkey={sig['signer_pubkey']} sig={sig['signature']}")
    ledger.append("tool_call", target="Edit", summary="after", vault=vault)

    lines = _lines(vault)
    rec = json.loads(lines[-1]); rec["summary"] = "TAMPERED"   # edit in place, no rehash
    lines[-1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    _write(vault, lines)
    assert ledger.verify_chain(vault), "precondition: the ledger really is broken"

    args = cli.build_parser().parse_args(["checkpoint", str(vault), "--verify", line])
    rc = cli.cmd_checkpoint(args)
    out = capsys.readouterr()
    assert rc == 1, "a broken ledger must not exit 0 from --verify"
    assert "broken now" in out.out, out.out
    assert not out.out.lstrip().startswith("PASS"), \
        "the FIRST verdict a reader sees must not be PASS when the result is failure"


@sig_required
def test_watch_json_reports_a_rebuilt_chain_to_a_SCRIPT(vault, capsys):
    """--json exited 0 on a rebuilt chain while the human path exited 1.

    Automation is exactly what consumes --json, and a correctly rebuilt chain produces no
    chain break, so the JSON path carried no signal whatsoever.
    """
    from homestead_memory import cli

    ledger.checkpoint(vault)
    _rebuild_chain(vault)
    args = cli.build_parser().parse_args(["watch", str(vault), "--json"])
    assert cli.cmd_watch(args) == 1, "--json must not hide a rebuilt chain from a script"


def test_export_and_verify_together_is_an_error_not_a_silent_drop(vault, capsys):
    """One reads, one writes. Honouring whichever was checked first would let a user
    believe they published a line they never received."""
    from homestead_memory import cli

    args = cli.build_parser().parse_args(["checkpoint", str(vault), "--export", "--verify", "x"])
    assert cli.cmd_checkpoint(args) == 2
    assert "pick one" in capsys.readouterr().err


@sig_required
def test_watch_coverage_does_not_parse_verify_checkpoints_prose(vault, capsys, monkeypatch):
    """`hsm watch` must survive a reworded core message.

    The first version did `why.split(";", 1)[1]`, so dropping that semicolon would
    IndexError inside the daily command, on a display path. Simulate exactly that.
    """
    from homestead_memory import cli

    ledger.checkpoint(vault)
    ledger.append("tool_call", target="Edit", summary="after", vault=vault)
    monkeypatch.setattr(ledger, "verify_checkpoint",
                        lambda *a, **k: (True, "reworded with no punctuation at all"))

    assert cli.cmd_watch(cli.build_parser().parse_args(["watch", str(vault)])) == 0
    assert "not covered" in capsys.readouterr().err, "must still report the uncovered tail"


@sig_required
def test_watch_survives_an_unreadable_checkpoint(vault, capsys):
    """It died with a raw PermissionError traceback out of the daily command.

    Found by a five-agent pass, which reported it as "returns 0, hiding the failure". The
    direction was right and the mechanism was not: it did not return anything, it crashed.
    Verified before fixing, which is why this pins the crash AND the exit code.
    """
    import os
    from homestead_memory import cli

    ledger.checkpoint(vault)
    cp = Path(vault) / ledger.CHECKPOINT_REL
    cp.chmod(0o000)
    try:
        if os.access(cp, os.R_OK):
            pytest.skip("running as root; permissions cannot be tested")
        rc = cli.cmd_watch(cli.build_parser().parse_args(["watch", str(vault)]))
        assert rc == 1, "an unreadable checkpoint must not read as a pass"
        assert "could not be read" in capsys.readouterr().err
    finally:
        cp.chmod(0o600)


@sig_required
def test_verify_reports_records_the_checkpoint_does_not_cover(vault):
    """`hsm watch` said "40 record(s) not covered" and `hsm verify` said nothing.

    verify is the AUDIT command: it produces the score and the report an auditor reads. A
    ledger whose signature covers 3 of 43 records must not look identical there to one
    that is fully checkpointed.
    """
    from homestead_memory.core import verify

    ledger.checkpoint(vault)
    for i in range(7):
        ledger.append("tool_call", target="Edit", summary=f"post{i}", vault=vault)

    rep = verify.verify_vault(vault, deep=False)
    unc = [f for f in rep["findings"] if f["check"] == "ledger_uncovered"]
    assert unc, "verify must report the uncovered tail"
    assert "7 record(s)" in unc[0]["detail"], unc[0]["detail"]
    assert "ledger_uncovered" in verify.ADVISORY_CHECKS, \
        "must not be scored: every live ledger has an uncovered tail"


@sig_required
def test_a_rebuilt_chain_is_caught_by_plain_verify_not_only_deep(vault):
    """The primary v1.5 claim, tested directly.

    The existing base-pass test covered `ledger_chain` (an in-place edit). A rebuilt chain
    produces NO chain break by design and is caught only by `ledger_signature`, so without
    this the headline claim was untested on the default command.
    """
    from homestead_memory.core import verify

    ledger.checkpoint(vault)
    _rebuild_chain(vault)
    rep = verify.verify_vault(vault, deep=False)
    assert any(f["check"] == "ledger_signature" for f in rep["findings"]), \
        "a rebuilt chain must be caught WITHOUT --deep"
    assert rep["score"] < 100 or any(f["level"] == "fail" for f in rep["findings"])


@sig_required
def test_exported_attestation_fields_never_contain_whitespace(vault):
    """parse_attestation splits on whitespace, so a space in ANY field silently breaks the
    round trip. Nothing enforces that the timestamp format stays space-free, so pin it."""
    sig = ledger.checkpoint(vault)
    for k in ("head_hash", "ts", "signer_pubkey", "signature"):
        assert not any(c.isspace() for c in str(sig[k])), \
            f"{k}={sig[k]!r} contains whitespace and would break the exported line"
