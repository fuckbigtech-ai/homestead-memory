#!/usr/bin/env python3
"""verify_evidence.py — verify a Homestead EvidencePack. No installs required.

Run:  python3 verify_evidence.py [pack_directory] [--pubkey <hex>] [--no-signature]

This file is copied verbatim into every EvidencePack. It imports nothing outside the
Python standard library, on purpose: if checking our evidence required installing our
tool, you would be trusting the vendor's code to vouch for the vendor's own record.
Read this file. It is short, and it is the whole verification.

WHAT A PASS PROVES
  - No record was edited, deleted, reordered, or truncated. Each record carries the
    SHA-256 of the one before it, so any change breaks every hash after it.
  - The head of the chain was signed by the holder of the private key matching
    pubkey.hex.

WHAT A PASS DOES NOT PROVE
  - That the agent really performed these actions. Records are what the harness
    reported at the time. This is a faithful log, not an independent witness.
  - That nothing is MISSING. Events that never reached the ledger (hook not installed,
    a recording failure) leave no trace here by definition. Recording failures that WERE
    detected appear in the integrity report as `ledger_drop`; check it.
  - Anything about records outside this pack's window. A windowed pack begins at an
    anchor, and everything before that anchor is simply not covered.

ON THE ED25519 IMPLEMENTATION BELOW
  It is the reference implementation from RFC 8032 (Edwards-Curve Digital Signature
  Algorithm), section 6, which is published as the specification's own worked example.
  It is used here for VERIFICATION ONLY: it never touches a private key, and every
  input it handles (public key, signature, message) is already public. The
  side-channel and timing concerns that make hand-rolled cryptography dangerous apply
  to secret-key operations, which this does not perform.

  It is deliberately not fast. One signature check takes tens of milliseconds.

  If you would rather not trust this code, the check it performs is standard: verify an
  Ed25519 signature over the ASCII string "<head_hash>:<record_count>" using the key in
  pubkey.hex. Any Ed25519 implementation will do, and `--no-signature` skips it here.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Ed25519 verification. RFC 8032 section 6 reference implementation.
# --------------------------------------------------------------------------

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = 4 * _inv(5)
_BX = _xrecover(_BY)
_B = (_BX % _P, _BY % _P)


def _edwards_add(pt_p, pt_q):
    x1, y1 = pt_p
    x2, y2 = pt_q
    k = _D * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + k)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - k)
    return (x3 % _P, y3 % _P)


def _scalarmult(pt, e: int):
    if e == 0:
        return (0, 1)
    q = _scalarmult(pt, e // 2)
    q = _edwards_add(q, q)
    if e & 1:
        q = _edwards_add(q, pt)
    return q


def _decodeint(s: bytes) -> int:
    return int.from_bytes(s[:32], "little")


def _isoncurve(pt) -> bool:
    x, y = pt
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _P == 0


def _decodepoint(s: bytes):
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (s[31] >> 7) & 1:
        x = _P - x
    pt = (x, y)
    if not _isoncurve(pt):
        # Rejecting off-curve points is not optional. Accepting them is the classic
        # way a verifier ends up validating signatures that no key ever produced.
        raise ValueError("decoding point that is not on curve")
    return pt


def ed25519_verify(signature: bytes, message: bytes, public_key: bytes) -> bool:
    """True iff `signature` is a valid Ed25519 signature of `message` under `public_key`."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    try:
        pt_a = _decodepoint(public_key)
        pt_r = _decodepoint(signature[:32])
        s = _decodeint(signature[32:])
        if s >= _L:
            return False        # non-canonical scalar; reject rather than normalise
        h = int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(), "little")
        return _scalarmult(_B, s) == _edwards_add(pt_r, _scalarmult(pt_a, h % _L))
    except (ValueError, IndexError):
        return False


# --------------------------------------------------------------------------
# Chain verification
# --------------------------------------------------------------------------

GENESIS_HASH = "0" * 64


def record_hash(rec: dict) -> str:
    payload = {k: v for k, v in rec.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_chain(records: list[dict], anchor: str) -> list[str]:
    problems: list[str] = []
    expected_prev = anchor
    expected_seq = records[0].get("seq") if records else 0
    for i, rec in enumerate(records):
        if rec.get("hash") != record_hash(rec):
            problems.append(f"record {i} (seq {rec.get('seq')}): content does not match its hash")
        if rec.get("prev_hash") != expected_prev:
            problems.append(
                f"record {i} (seq {rec.get('seq')}): prev_hash does not match the preceding "
                f"record (removed, reordered, or inserted)")
        if rec.get("seq") != expected_seq:
            problems.append(f"record {i}: expected seq {expected_seq}, found {rec.get('seq')}")
        expected_prev = rec.get("hash") or expected_prev
        expected_seq = (rec.get("seq") if isinstance(rec.get("seq"), int) else expected_seq) + 1
    return problems


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_sig = "--no-signature" not in argv
    # --pubkey <hex> pins the expected signer. Without it a signature can only show
    # internal consistency, because the key travels inside the pack it authenticates.
    expect_key = None
    if "--pubkey" in argv:
        i = argv.index("--pubkey")
        if i + 1 < len(argv):
            expect_key = argv[i + 1].strip().lower()
            del argv[i:i + 2]
    argv = [a for a in argv if not a.startswith("--")]
    pack = Path(argv[0]) if argv else Path(__file__).resolve().parent

    try:
        manifest = json.loads((pack / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"FAIL: cannot read MANIFEST.json: {e}")
        return 2

    records = []
    torn = 0
    try:
        for line in (pack / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                torn += 1
    except OSError as e:
        print(f"FAIL: cannot read ledger.jsonl: {e}")
        return 2

    anchor = manifest.get("anchor_hash", GENESIS_HASH)
    print(f"pack:       {pack}")
    print(f"records:    {len(records)}")
    if records:
        print(f"window:     seq {records[0].get('seq')}..{records[-1].get('seq')}  "
              f"({manifest.get('window_from_ts')} .. {manifest.get('window_to_ts')})")
    if anchor == GENESIS_HASH:
        print("anchor:     genesis (this pack covers the ledger from its beginning)")
    else:
        print(f"anchor:     {anchor[:16]}…  "
              f"(records BEFORE this anchor are not included in this pack)")

    problems = verify_chain(records, anchor)
    if torn:
        problems.append(f"{torn} unparseable line(s) in ledger.jsonl")

    if manifest.get("record_count") != len(records):
        problems.append(
            f"manifest claims {manifest.get('record_count')} records, file has {len(records)}")

    print()
    if problems:
        print(f"chain:      FAILED  ({len(problems)} problem(s))")
        for p in problems[:20]:
            print(f"              - {p}")
        if len(problems) > 20:
            print(f"              ...and {len(problems) - 20} more")
    else:
        print("chain:      VERIFIED  no record was edited, deleted, or reordered")

    sig_ok = None
    if check_sig and not manifest.get("signed", True):
        # Deliberately unsigned is a weaker pack, not a broken one. Reporting FAILED
        # here would conflate "absent" with "invalid" and train readers to ignore the
        # line that matters when a signature really does fail to verify.
        print("signature:  ABSENT   this pack is unsigned; the chain proves no record was")
        print("                     edited in place, but a wholly rebuilt chain would not")
        print("                     be detected")
    elif check_sig:
        try:
            sig = json.loads((pack / "ledger.sig").read_text(encoding="utf-8"))
            pub = bytes.fromhex((pack / "pubkey.hex").read_text(encoding="utf-8").strip())
            payload = f"{sig['head_hash']}:{sig['records']}".encode("utf-8")
            sig_ok = ed25519_verify(bytes.fromhex(sig["signature"]), payload, pub)
            if sig_ok and records and sig["head_hash"] != records[-1].get("hash"):
                sig_ok = False
                print("signature:  FAILED  signature is valid but covers a different head "
                      "than this pack's last record")
            elif sig_ok and expect_key:
                if pub.hex() != expect_key.lower():
                    sig_ok = False
                    print(f"signature:  FAILED  signed by {pub.hex()[:16]}… but you expected "
                          f"{expect_key[:16]}…")
                else:
                    print(f"signature:  VERIFIED  head signed by the key you specified "
                          f"({pub.hex()[:16]}…)")
            elif sig_ok:
                # The signature is real, but the key came from inside the pack. Anyone
                # who can rewrite the ledger can also generate a key, sign the forgery,
                # and replace pubkey.hex -- and it will verify. A signature only means
                # something when you know WHICH key to expect. Saying VERIFIED here with
                # no qualifier would be a misleading result, so it is not said.
                print(f"signature:  SELF-ASSERTED  valid under {pub.hex()[:16]}…, but that")
                print("                     key came from this pack. Compare it against the")
                print("                     signer's key obtained SEPARATELY, or re-run with")
                print(f"                     --pubkey <hex>. Until then this establishes")
                print("                     internal consistency, not authenticity.")
            else:
                print("signature:  FAILED  signature does not verify")
        except (OSError, ValueError, KeyError) as e:
            sig_ok = False
            print(f"signature:  FAILED  cannot check ({e})")
    else:
        print("signature:  SKIPPED  (--no-signature)")

    drops = (manifest.get("integrity") or {}).get("ledger_drops", 0)
    if drops:
        print(f"\n!! the source ledger recorded {drops} failure(s) to record an event.")
        print("   this pack is internally consistent but the ORIGINAL log has known gaps.")

    print()
    ok = not problems and (sig_ok is not False)
    print("RESULT:     PASS" if ok else "RESULT:     FAIL")

    # FOUR passing states, four verdicts. Collapsing any two of them is how this file
    # once told a reader that an UNSIGNED pack was "intact and signed" (found
    # 2026-08-26): sig_ok is None when there is no signature, None is falsy, so the
    # unsigned case fell through to the wording written for a signed one. The
    # `signature:` line above was already correct, which made it worse rather than
    # better: the reader saw ABSENT and then a verdict contradicting it.
    #
    # This is the last sentence an auditor reads about an artifact whose only real
    # claim is that it refuses to overclaim, so each state says exactly what it found.
    limits = [
        "            This does not establish that the agent performed the actions, nor",
        "            that events missing from the ledger never happened. See README.md.",
    ]
    if ok and not check_sig:
        print("            Records in this window are intact and internally consistent.")
        print("            The signature was NOT CHECKED (--no-signature), so nothing")
        print("            here speaks to the origin of this pack.")
        print(*limits, sep="\n")
    elif ok and sig_ok is None:
        print("            Records in this window are intact and internally consistent,")
        print("            and this pack is UNSIGNED. The chain shows that no record was")
        print("            edited, deleted, or reordered in place, but a wholly rebuilt")
        print("            chain would not be detected, and nothing here establishes who")
        print("            produced it.")
        print(*limits, sep="\n")
    elif ok and not expect_key:
        print("            Records in this window are intact and internally consistent.")
        print("            The signature is SELF-ASSERTED: confirm pubkey.hex against a")
        print("            key you obtained separately before treating this as proof of")
        print("            origin.")
        print(*limits, sep="\n")
    elif ok:
        print("            Records in this window are intact, and the head was signed by")
        print("            the key you pinned.")
        print(*limits, sep="\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
