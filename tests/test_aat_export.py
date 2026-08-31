"""Conformance to draft-sharif-agent-audit-trail-01.

Emitted ALONGSIDE the native format, never replacing it, because this is an individual
Internet-Draft with no working-group adoption: betting the native chain on it would break
every ledger on disk for a spec that may still change. So the load-bearing test here is
not just "the export looks right", it is "the native path is untouched".

The signature tests exist because P1363 vs DER is the classic ECDSA interop bug and it
fails SILENTLY: a DER signature verifies fine against our own code and is rejected by
every conformant verifier.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from homestead_memory.adapters import aat
from homestead_memory.core import capture, jcs, ledger


@pytest.fixture()
def exported(tmp_path):
    for payload in (
        {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "npm test"}, "session_id": "s1"},
        {"hook_event_name": "PostToolUse", "tool_name": "Bash",
         "tool_input": {"command": "npm test"}, "tool_response": {"ok": True},
         "session_id": "s1"},
    ):
        ledger.append(vault=tmp_path, **capture.from_hook_payload(payload))
    out = tmp_path / "aat.jsonl"
    aat.aat_export(tmp_path, out_dir=out)
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def test_every_mandatory_field_is_present(exported):
    """Section 3.1 lists twelve. A missing one is a non-conformant record."""
    for record in exported:
        missing = [f for f in aat.MANDATORY_FIELDS if f not in record]
        assert not missing, f"missing mandatory field(s): {missing}"


def test_chain_uses_sha256_over_jcs(exported):
    """Section 6.1: prev_hash(N) = hex(SHA-256(JCS(record(N-1)))).

    Recomputed here from the spec's formula rather than by calling aat.record_hash, so
    the test cannot pass merely because the exporter agrees with itself.
    """
    assert exported[0]["prev_hash"] is None, "genesis prev_hash must be null"
    assert exported[0]["parent_record_id"] is None
    for previous, current in zip(exported, exported[1:]):
        payload = {k: v for k, v in previous.items() if k != "signature"}
        assert current["prev_hash"] == hashlib.sha256(jcs.canonicalize(payload)).hexdigest()
        assert current["parent_record_id"] == previous["record_id"]


def test_record_phase_survives_the_mapping(exported):
    """The decision phase is the whole reason PreToolUse capture was added."""
    assert [r["record_phase"] for r in exported] == ["pre_execution", "post_execution"]


def test_outcome_stays_inside_the_draft_vocabulary(exported):
    allowed = {"success", "failure", "timeout", "denied", "escalated"}
    assert all(r["outcome"] in allowed for r in exported)


def test_we_do_not_claim_denied_or_escalated_we_never_observed(exported):
    """Section 4.2 makes denied/escalated a MUST-be-pre_execution claim about ENFORCEMENT.

    We observe a harness reporting its own tool calls and cannot see a policy engine's
    verdict, so asserting either would be exactly the overclaim this project exists to
    avoid. If a future change starts emitting them, it must come with real evidence.
    """
    assert all(r["outcome"] not in ("denied", "escalated") for r in exported)


def test_trust_level_is_honest_about_what_we_can_see(exported):
    """L0-L4 is meaningless if every implementer writes L4."""
    assert all(r["trust_level"] == "L1" for r in exported)


def test_signature_is_p1363_not_der():
    """Section 6.2: Base64url, IEEE P1363 fixed r||s, 64 bytes. DER fails silently."""
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    key = ec.generate_private_key(ec.SECP256R1())
    raw = base64.urlsafe_b64decode(
        (sig := aat.sign_p1363(hashlib.sha256(b"x").digest(), key)) + "=" * (-len(sig) % 4))

    assert len(raw) == 64, f"P1363 r||s is exactly 64 bytes, got {len(raw)}"
    assert raw[:1] != b"\x30", "0x30 means DER was emitted; conformant verifiers reject it"


def test_signature_verifies_after_round_tripping_back_to_der():
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import utils as au

    key = ec.generate_private_key(ec.SECP256R1())
    digest = hashlib.sha256(b"payload").digest()
    sig = aat.sign_p1363(digest, key)
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    der = au.encode_dss_signature(int.from_bytes(raw[:32], "big"),
                                  int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, digest, ec.ECDSA(au.Prehashed(hashes.SHA256())))


def test_exporting_does_not_touch_the_native_ledger(tmp_path):
    """The guarantee the 'alongside, not replacing' design rests on."""
    for i in range(3):
        ledger.append("tool_call", target=f"t{i}", summary=f"s{i}", vault=tmp_path)
    before = (tmp_path / ledger.LEDGER_REL).read_bytes()

    aat.aat_export(tmp_path, out_dir=tmp_path / "out.jsonl")

    assert (tmp_path / ledger.LEDGER_REL).read_bytes() == before, \
        "the native ledger must be byte-identical after an AAT export"
    assert ledger.verify_chain(tmp_path) == []
