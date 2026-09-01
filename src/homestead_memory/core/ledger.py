#!/usr/bin/env python3
"""ledger.py — hash-chained, append-only record of what an agent actually did.

WHY THIS EXISTS
---------------
`signing.py` proves a vault's state at a moment: "these files hashed to X, signed
by Y." That is a snapshot. It cannot answer "what did the agent do at 14:02", and
it cannot show that nothing was quietly removed from the record in between.

A chain can. Every record carries the hash of the record before it, so deleting,
reordering, or editing any record breaks every hash after it. You do not need to
trust the storage; you recompute and see exactly where the break is.

Two audiences, deliberately one artifact:

  debugging   "what did my agent do?" — the daily reason anyone runs this. Silent
              agent failure is the common case: a job reports success while doing
              nothing, and there is no record to contradict it.
  evidence    the same file is an audit record. EU AI Act Article 12 requires
              automatic, tamper-evident event recording for high-risk systems;
              manual records explicitly do not count.

If these ever become two formats, the design has failed. There is one file.

WHY NOT store.atomic_write
--------------------------
`atomic_write` rewrites a whole file through a temp + replace. That is correct for
notes and catastrophic for an append-only log: it rewrites history on every append
(O(n) per record), and a crash mid-replace can lose every prior record rather than
just the newest. Appends here are O_APPEND + fsync of a single line, serialized by
`store.vault_lock`, so a crash can at worst leave one torn final line, which
`verify_chain` reports rather than silently accepting.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import provenance, store, vault as vaultlib

LEDGER_REL = Path(".hsm") / "ledger.jsonl"
CHECKPOINT_REL = Path(".hsm") / "ledger.sig"
LEDGER_VERSION = 1

# The chain has to start somewhere. 64 zeros is not a real hash, so a record
# claiming this as its predecessor is unambiguously the genesis record, and a
# second one appearing mid-file is itself a detectable defect.
GENESIS_HASH = "0" * 64

# Fields excluded from the hash preimage. `hash` obviously cannot cover itself.
_UNHASHED = frozenset({"hash"})


def record_hash(rec: dict) -> str:
    """Hash of a record's canonical form, excluding its own `hash` field.

    sort_keys + tight separators so the digest depends on the record's CONTENT and
    not on dict ordering or the whitespace whims of whatever wrote it. A verifier
    on another machine, in another language, must be able to recompute this.
    """
    payload = {k: v for k, v in rec.items() if k not in _UNHASHED}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ledger_path(vault: Path | str | None = None) -> Path:
    return vaultlib._resolve(vault) / LEDGER_REL


def _last_record(path: Path) -> dict | None:
    """Read the final complete record without loading the file.

    Seeks backward in chunks. A ledger is append-only and unbounded, so reading it
    entirely just to learn the head hash would make every append O(n) and turn a
    long-running agent's log into a performance cliff.

    A torn final line (crash mid-append) is skipped here so the NEXT append still
    chains from the last intact record; `verify_chain` is what reports the tear.
    """
    if not path.exists():
        return None
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        if end == 0:
            return None
        buf = b""
        pos = end
        while pos > 0:
            step = min(8192, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            lines = [ln for ln in buf.split(b"\n") if ln.strip()]
            for ln in reversed(lines):
                try:
                    return json.loads(ln)
                except (ValueError, UnicodeDecodeError):
                    continue      # torn or corrupt line: keep walking backward
            if pos == 0:
                break
    return None


PHASE_PRE = "pre_execution"
PHASE_POST = "post_execution"


def append(
    action: str,
    *,
    target: str | None = None,
    summary: str | None = None,
    meta: dict | None = None,
    vault: Path | str | None = None,
    agent: str | None = None,
    session: str | None = None,
    phase: str | None = None,
) -> dict:
    """Append one record and return it.

    Serialized by `vault_lock` so two agents writing concurrently cannot both read
    the same head and fork the chain. The lock is the same advisory lock the rest
    of the write path uses, so a ledger append and a note write cannot interleave
    into an inconsistent pair either.
    """
    root = vaultlib._resolve(vault)
    path = root / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)

    with store.vault_lock(root):
        prev = _last_record(path)
        rec = {
            "v": LEDGER_VERSION,
            "seq": (prev["seq"] + 1) if prev else 0,
            "ts": provenance.now_ts(),
            "agent": provenance.resolve_agent(agent),
            "session": provenance.resolve_session(session),
            "action": action,
            "target": target,
            "summary": summary,
            "meta": meta or {},
            "prev_hash": prev["hash"] if prev else GENESIS_HASH,
        }
        # Written only when known, so records predating phase capture keep hashing
        # exactly as they did and old chains still verify. draft-sharif-agent-audit-trail
        # calls this record_phase, and it is the difference between a log that proves
        # ENFORCEMENT and one that proves only observation: "a denial that is only logged
        # after execution provides no evidence that the denial was enforced".
        if phase:
            rec["phase"] = phase
        rec["hash"] = record_hash(rec)
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"

        # O_APPEND makes the write atomic with respect to other appenders at the OS
        # level; the lock above is what keeps the CHAIN consistent (read-head then
        # write must be one critical section). fsync so a power loss cannot leave a
        # record that verification accepted but the disk never took.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    return rec


def read_all(vault: Path | str | None = None) -> list[dict]:
    """Every intact record, in file order. Torn lines are omitted; use verify_chain
    to learn that they existed."""
    path = _ledger_path(vault)
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def tail(n: int = 20, vault: Path | str | None = None) -> list[dict]:
    return read_all(vault)[-n:]


def head_hash(vault: Path | str | None = None) -> str:
    rec = _last_record(_ledger_path(vault))
    return rec["hash"] if rec else GENESIS_HASH


@dataclass
class ChainBreak:
    """Where the chain stops being provable, and why.

    `index` is the position in the file, `seq` the record's own claim. They differ
    exactly when records were removed, which is the case worth naming precisely.
    """
    index: int
    seq: int | None
    kind: str        # "torn" | "prev_mismatch" | "hash_mismatch" | "seq_gap" | "bad_genesis"
    detail: str


def verify_chain(vault: Path | str | None = None) -> list[ChainBreak]:
    """Recompute the chain. Empty list means every record is provably intact.

    Deliberately reports ALL breaks rather than stopping at the first: one edited
    record invalidates every hash after it, and an operator needs to see that the
    damage starts at index N rather than believing the whole log is worthless.
    """
    path = _ledger_path(vault)
    if not path.exists():
        return []

    breaks: list[ChainBreak] = []
    expected_prev = GENESIS_HASH
    expected_seq = 0
    idx = -1

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for idx, line in enumerate(f):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                breaks.append(ChainBreak(idx, None, "torn",
                                         "record is not valid JSON (truncated write or edit)"))
                continue

            seq = rec.get("seq")
            if rec.get("hash") != record_hash(rec):
                breaks.append(ChainBreak(idx, seq, "hash_mismatch",
                                         "record content does not match its own hash (edited in place)"))
            if rec.get("prev_hash") != expected_prev:
                kind = "bad_genesis" if idx == 0 else "prev_mismatch"
                breaks.append(ChainBreak(
                    idx, seq, kind,
                    f"prev_hash {str(rec.get('prev_hash'))[:12]}… does not match preceding "
                    f"record {expected_prev[:12]}… (record removed, reordered, or inserted)"))
            if seq != expected_seq:
                breaks.append(ChainBreak(idx, seq, "seq_gap",
                                         f"expected seq {expected_seq}, found {seq}"))

            expected_prev = rec.get("hash") or expected_prev
            expected_seq = (seq if isinstance(seq, int) else expected_seq) + 1

    return breaks


DROPS_REL = Path(".hsm") / "ledger.drops.jsonl"


def record_drop(reason: str, vault: Path | str | None = None) -> None:
    """Record that an event could NOT be recorded.

    An audit log with invisible gaps is the exact defect this exists to prevent, so a
    failed append has to leave a mark. Deliberately does NOT take `vault_lock`: the
    lock timing out is one of the likeliest reasons we are here, and a drop handler
    that can be blocked by the same contention is not a drop handler.

    Best-effort by design. If even this fails there is nothing safe left to do inside
    a hook that must not disturb the session.
    """
    try:
        root = vaultlib._resolve(vault)
        p = root / DROPS_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"ts": provenance.now_ts(), "reason": str(reason)[:300]},
            sort_keys=True, separators=(",", ":"),
        ) + "\n"
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except BaseException:      # noqa: BLE001 - see docstring
        pass


def read_drops(vault: Path | str | None = None) -> list[dict]:
    p = vaultlib._resolve(vault) / DROPS_REL
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"ts": None, "reason": "unparseable drop record"})
    return out


def checkpoint(vault: Path | str | None = None, key_path: Path | str | None = None) -> dict:
    """Sign the current head hash.

    The chain proves internal consistency: nobody removed a record without leaving
    a break. It does NOT prove the whole file was not rebuilt from scratch by
    someone who recomputed every hash. A signature over the head, made with a key
    the rewriter does not hold, is what closes that. Reuses signing.py's key
    handling rather than introducing a second key path or a second algorithm.
    """
    from . import signing

    _ed25519_mod, serialization, _InvalidSignature = signing._ed25519()
    root = vaultlib._resolve(vault)
    private_key = signing.load_or_create_key(key_path)
    h = head_hash(root)
    records = len(read_all(root))
    payload = f"{h}:{records}"
    signature = private_key.sign(payload.encode("utf-8"))
    pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    sig = {
        "head_hash": h,
        "records": records,
        "ts": provenance.now_ts(),
        "signer_pubkey": pub.hex(),
        "signature": signature.hex(),
        "alg": signing.ALG,
        "sig_version": signing.SIG_VERSION,
        "ledger_version": LEDGER_VERSION,
    }
    store.atomic_write(root / CHECKPOINT_REL, json.dumps(sig, sort_keys=True) + "\n")
    return sig


def verify_checkpoint(vault: Path | str | None = None,
                      expect_pubkey: str | None = None) -> tuple[bool, str]:
    """(ok, reason). Absence of a checkpoint is reported, never treated as success."""
    from . import signing

    root = vaultlib._resolve(vault)
    p = root / CHECKPOINT_REL
    if not p.exists():
        return False, "no ledger checkpoint (run `hsm checkpoint`)"
    try:
        sig = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False, "checkpoint file is not valid JSON"
    return _check_attestation(root, sig, expect_pubkey)


ATTESTATION_PREFIX = "hsm-checkpoint v1"
_ATTESTATION_FIELDS = ("head", "records", "ts", "pubkey", "sig")


def parse_attestation(line: str) -> dict:
    """Parse the line `hsm checkpoint --export` prints, back into a checkpoint dict.

    Raises ValueError with a usable message rather than returning a half-built dict: this
    input arrives from a human pasting from a gist or a git commit, so malformed is the
    NORMAL case and it must not be reported as a signature failure.
    """
    line = line.strip()
    if not line.startswith(ATTESTATION_PREFIX):
        raise ValueError(f"not an attestation line (expected it to start {ATTESTATION_PREFIX!r})")
    kv = {}
    for tok in line[len(ATTESTATION_PREFIX):].split():
        k, _, v = tok.partition("=")
        if not _:
            raise ValueError(f"malformed field {tok!r}: expected key=value")
        kv[k] = v
    missing = [f for f in _ATTESTATION_FIELDS if f not in kv]
    if missing:
        raise ValueError(f"attestation is missing {', '.join(missing)}")
    try:
        records = int(kv["records"])
    except ValueError:
        raise ValueError(f"records={kv['records']!r} is not a number") from None
    return {"head_hash": kv["head"], "records": records, "ts": kv["ts"],
            "signer_pubkey": kv["pubkey"], "signature": kv["sig"]}


def verify_attestation(line: str, vault: Path | str | None = None,
                       expect_pubkey: str | None = None) -> tuple[bool, str]:
    """(ok, reason) for a PUBLISHED attestation against the local ledger.

    This is the half that makes `--export` mean anything. Exporting a line to a gist and
    having no way to check a ledger against it is a ritual, not a control: the whole point
    of publishing outside the machine is that you can come back and test the file against
    what you published BEFORE the machine was touched.
    """
    return _check_attestation(vaultlib._resolve(vault), parse_attestation(line), expect_pubkey)


def _check_attestation(root: Path, sig: dict,
                       expect_pubkey: str | None = None) -> tuple[bool, str]:
    """Shared by the on-disk checkpoint and a published attestation.

    One implementation on purpose. Two copies of a signature-then-prefix check is how the
    published path quietly grows weaker than the local one, and the published path is the
    one used when the local machine is already suspect.
    """
    from . import signing

    if expect_pubkey and sig.get("signer_pubkey") != expect_pubkey:
        # Local rather than borrowed: the equivalent helper lives in verify.py, and
        # importing it here would make ledger depend on the module that is about to
        # depend on ledger. It is one line; the cycle is not worth saving it.
        got = sig.get("signer_pubkey") or "unknown signer"
        return False, f"signed by unexpected key {got[:16]}…"

    ed25519, _serialization, InvalidSignature = signing._ed25519()
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(sig["signer_pubkey"]))
        payload = f"{sig['head_hash']}:{sig['records']}"
        pub.verify(bytes.fromhex(sig["signature"]), payload.encode("utf-8"))
    except (InvalidSignature, KeyError, ValueError):
        return False, "checkpoint signature does not verify"

    current = head_hash(root)
    if current != sig["head_hash"]:
        # Not tampering by itself: normal appends move the head past a checkpoint.
        # Say which it is rather than crying wolf on ordinary use.
        #
        # But growth is NOT sufficient evidence of growth. Comparing record COUNTS alone
        # let a forgery that replaced every record and then grew past the checkpoint
        # verify clean: 5 checkpointed records replaced by 9 fabricated ones returned
        # "valid as of 5 records; 4 appended since". The signed head must still be a
        # PREFIX of the chain, so recompute the hash at the checkpointed index and
        # require it to match before accepting the ledger merely grew.
        recs = read_all(root)
        n_now = len(recs)
        if n_now > sig["records"] and sig["records"] >= 1:
            at_checkpoint = recs[sig["records"] - 1].get("hash")
            if at_checkpoint == sig["head_hash"]:
                return True, (f"valid as of {sig['records']} records; "
                              f"{n_now - sig['records']} appended since "
                              f"(re-checkpoint to cover them)")
            return False, ("the checkpointed head is not a prefix of this chain: record "
                           f"{sig['records'] - 1} hashes to {str(at_checkpoint)[:12]}…, "
                           f"checkpoint signed {sig['head_hash'][:12]}… "
                           "(the ledger was rebuilt, not appended to)")
        return False, "head hash does not match the checkpoint and the ledger did not grow"
    return True, f"verified: {sig['records']} records, head {current[:12]}…"
