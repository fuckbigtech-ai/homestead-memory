"""EvidencePack: does it survive someone trying to cheat it?

The pack's whole claim is that a third party can check it without trusting us. So these
tests perform the forgeries against real packs on disk and require the bundled verifier
to catch them, running it the way an auditor would: as a subprocess, with a clean
environment, no PYTHONPATH, and nothing from this project importable.

The Ed25519 implementation in the verifier is hand-carried (RFC 8032 reference code) at
Humza's decision, so it is differentially tested against `cryptography`. Agreement on
VALID signatures alone would be worthless: a verifier that returns True unconditionally
passes every positive test. The negative cases are the real suite.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from homestead_memory import evidence_verifier as ev
from homestead_memory.core import evidence, ledger

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519 as C
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAVE_CRYPTO, reason="requires the [sign] extra")


# --- the hand-carried Ed25519 ---------------------------------------------

def _raw_pub(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def _reference_verify(sig, msg, pub) -> bool:
    try:
        C.Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except Exception:       # noqa: BLE001 - any failure is a rejection
        return False


@needs_crypto
@pytest.mark.parametrize("i", range(12))
def test_agrees_with_cryptography_on_valid_signatures(i):
    key = C.Ed25519PrivateKey.generate()
    msg = secrets.token_bytes(i * 17)
    sig, pub = key.sign(msg), _raw_pub(key)
    assert ev.ed25519_verify(sig, msg, pub) == _reference_verify(sig, msg, pub) is True


@needs_crypto
def test_agrees_with_cryptography_on_forgeries():
    """The half that matters. A verifier that always returns True passes the tests
    above and fails every one of these."""
    key = C.Ed25519PrivateKey.generate()
    other = C.Ed25519PrivateKey.generate()
    msg = b"a3f8deadbeef:412"
    sig, pub = key.sign(msg), _raw_pub(key)

    cases = {
        "mutated message":  (sig, b"a3f8deadbeef:413", pub),
        "wrong public key": (sig, msg, _raw_pub(other)),
        "random signature": (secrets.token_bytes(64), msg, pub),
        "bit flipped in R": (bytes([sig[0] ^ 1]) + sig[1:], msg, pub),
        "bit flipped in S": (sig[:32] + bytes([sig[32] ^ 1]) + sig[33:], msg, pub),
        "all-zero sig":     (b"\x00" * 64, msg, pub),
        "truncated sig":    (sig[:63], msg, pub),
        "oversized sig":    (sig + b"\x00", msg, pub),
        "short pubkey":     (sig, msg, pub[:31]),
    }
    for label, (s, m, p) in cases.items():
        mine = ev.ed25519_verify(s, m, p)
        assert mine is False, f"{label}: forgery ACCEPTED"
        assert mine == _reference_verify(s, m, p), f"{label}: disagrees with cryptography"


def test_malformed_input_is_rejected_not_crashed():
    for sig, msg, pub in [(b"", b"m", b""), (b"x" * 64, b"m", b"y" * 32),
                          (b"\xff" * 64, b"m", b"\xff" * 32)]:
        assert ev.ed25519_verify(sig, msg, pub) is False


# --- pack building and clean-room verification -----------------------------

def _vault_with_records(tmp_path, n=5):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "n.md").write_text("---\nname: n\n---\n", encoding="utf-8")
    for i in range(n):
        ledger.append("tool_call", target="Bash", summary=f"step {i}",
                      meta={"input": {"head": f"cmd {i}"}}, vault=v)
    return v


def _verify(pack: Path, *args) -> subprocess.CompletedProcess:
    """Run the bundled verifier the way an auditor would: clean env, nothing importable
    from this project, no PYTHONPATH."""
    return subprocess.run(
        [sys.executable, str(pack / "verify_evidence.py"), str(pack), *args],
        capture_output=True, text=True, cwd="/", env={"PATH": os.environ.get("PATH", "")},
    )


@pytest.fixture()
def pack(tmp_path):
    v = _vault_with_records(tmp_path)
    res = evidence.build_pack(v, tmp_path / "pack")
    return Path(res["pack"])


def test_pack_contains_everything_needed_to_check_it(pack):
    for f in ("ledger.jsonl", "MANIFEST.json", "integrity.json",
              "verify_evidence.py", "README.md"):
        assert (pack / f).exists(), f"missing {f}"


def test_an_honest_pack_verifies_in_a_clean_environment(pack):
    r = _verify(pack)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "chain:      VERIFIED" in r.stdout
    assert "RESULT:     PASS" in r.stdout


def test_editing_a_record_is_caught(pack):
    p = pack / "ledger.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[2])
    rec["summary"] = "rm -rf / (never happened)"
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = _verify(pack)
    assert r.returncode != 0
    assert "chain:      FAILED" in r.stdout


def test_deleting_a_record_is_caught(pack):
    p = pack / "ledger.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    del lines[2]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = _verify(pack)
    assert r.returncode != 0
    assert "manifest claims" in r.stdout, "the count mismatch should be named explicitly"


@needs_crypto
def test_a_wholly_rebuilt_chain_passes_the_chain_check_and_is_caught_by_the_signature(pack):
    """Rewriting every record produces a self-consistent chain. This is precisely why
    the signature exists, and the verifier must say WHICH check caught it."""
    import hashlib

    recs = [json.loads(l) for l in (pack / "ledger.jsonl").read_text().splitlines() if l.strip()]
    prev = ev.GENESIS_HASH
    for rec in recs:
        rec["summary"] = "innocent activity"
        rec["prev_hash"] = prev
        rec["hash"] = hashlib.sha256(json.dumps(
            {k: v for k, v in rec.items() if k != "hash"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        prev = rec["hash"]
    (pack / "ledger.jsonl").write_text("".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for r in recs), encoding="utf-8")

    r = _verify(pack)
    assert "chain:      VERIFIED" in r.stdout, "precondition: the forgery is self-consistent"
    assert "signature:  FAILED" in r.stdout
    assert r.returncode != 0


@needs_crypto
def test_key_substitution_is_self_asserted_without_a_pin_and_fails_with_one(pack, tmp_path):
    """The attack that matters most, and the one a pack cannot defend against alone:
    forge everything, then sign it with your own key and swap pubkey.hex.

    Unpinned, the verifier must NOT say VERIFIED, because it cannot know the key is the
    right one. Pinned against the real key, it must fail outright."""
    import hashlib

    real_key = (pack / "pubkey.hex").read_text().strip()

    recs = [json.loads(l) for l in (pack / "ledger.jsonl").read_text().splitlines() if l.strip()]
    prev = ev.GENESIS_HASH
    for rec in recs:
        rec["summary"] = "innocent activity"
        rec["prev_hash"] = prev
        rec["hash"] = hashlib.sha256(json.dumps(
            {k: v for k, v in rec.items() if k != "hash"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        prev = rec["hash"]
    (pack / "ledger.jsonl").write_text("".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for r in recs), encoding="utf-8")

    attacker = C.Ed25519PrivateKey.generate()
    pub = _raw_pub(attacker)
    sig = json.loads((pack / "ledger.sig").read_text())
    sig.update(head_hash=recs[-1]["hash"], records=len(recs), signer_pubkey=pub.hex(),
               signature=attacker.sign(f"{recs[-1]['hash']}:{len(recs)}".encode()).hex())
    (pack / "ledger.sig").write_text(json.dumps(sig, indent=2), encoding="utf-8")
    (pack / "pubkey.hex").write_text(pub.hex() + "\n", encoding="utf-8")

    unpinned = _verify(pack)
    assert "SELF-ASSERTED" in unpinned.stdout, \
        "an unpinned signature must not be reported as VERIFIED"

    pinned = _verify(pack, "--pubkey", real_key)
    assert "signature:  FAILED" in pinned.stdout
    assert pinned.returncode != 0


@needs_crypto
def test_pinning_the_correct_key_reports_verified(pack):
    key = (pack / "pubkey.hex").read_text().strip()
    r = _verify(pack, "--pubkey", key)
    assert "signature:  VERIFIED" in r.stdout and r.returncode == 0


# --- windowing -------------------------------------------------------------

def test_a_windowed_pack_declares_its_anchor(tmp_path):
    """A slice cannot chain to genesis. If the pack did not say so it would appear to
    prove more than it does."""
    v = _vault_with_records(tmp_path, n=6)
    # By seq, not ts: timestamps are second-granular, so records written in the same
    # second cannot be split by a ts window. Discovered by this test failing.
    res = evidence.build_pack(v, tmp_path / "w", from_seq=2)

    manifest = json.loads((Path(res["pack"]) / "MANIFEST.json").read_text())
    assert manifest["windowed"] is True
    assert manifest["anchor_hash"] != ev.GENESIS_HASH

    r = _verify(Path(res["pack"]))
    assert "not included in this pack" in r.stdout
    assert r.returncode == 0, "a windowed pack must still verify against its anchor"


def test_exporting_an_empty_ledger_is_a_clear_error_not_an_empty_pack(tmp_path):
    v = tmp_path / "empty"
    v.mkdir()
    (v / "n.md").write_text("---\nname: n\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no ledger records"):
        evidence.build_pack(v, tmp_path / "out")


def test_known_gaps_in_the_source_ledger_are_disclosed(tmp_path):
    v = _vault_with_records(tmp_path)
    ledger.record_drop("vault lock timed out", vault=v)
    res = evidence.build_pack(v, tmp_path / "p")
    pack = Path(res["pack"])

    assert json.loads((pack / "integrity.json").read_text())["ledger_drops"] == 1
    assert "Known gaps" in (pack / "README.md").read_text()
    r = _verify(pack)
    assert "known gaps" in r.stdout.lower()


def test_readme_states_the_key_pinning_limitation(pack):
    """If this ever disappears from the README, the pack starts overclaiming."""
    readme = (pack / "README.md").read_text()
    assert "--pubkey" in readme
    assert "SELF-ASSERTED" in readme


def test_the_bundled_verifier_is_byte_identical_to_the_tested_one(pack):
    """What ships must be what the suite exercises."""
    assert (pack / "verify_evidence.py").read_bytes() == Path(ev.__file__).read_bytes()
