"""Export the agent ledger as draft-sharif-agent-audit-trail-01 records.

WHY AN EXPORT AND NOT A MIGRATION
---------------------------------
The draft is an INDIVIDUAL Internet-Draft (Raza Sharif, CyberSecAI Ltd, -01 dated
2026-08-19, expires 2027-02-19) with no working-group adoption yet. Betting the native
format on it would break every chain on disk for a spec that may change or lapse.
Emitting it ALONGSIDE costs nothing, breaks nothing, and makes us an early named
implementer of a format that maps explicitly to EU AI Act Article 12 - which is a
defensible position, where publishing the ninth rival format is not.

WHERE WE DIVERGE, DELIBERATELY AND ON THE RECORD
------------------------------------------------
The draft specifies ECDSA P-256. Our NATIVE EvidencePack stays Ed25519, because its
verifier is hand-carried stdlib-only so a recipient can check a pack with nothing
installed, and a pure-Python P-256 verifier is far heavier than the Ed25519 one. So:
P-256 here, where the reader is expected to have conformant tooling; Ed25519 there,
where the whole point is that they need nothing. Both are stated rather than silently
differing.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from ..core import jcs, ledger, vault as vaultlib

# Section 3.1. Present in every record; `signature` is added after hashing.
MANDATORY_FIELDS = (
    "record_id", "timestamp", "agent_id", "agent_version", "session_id",
    "action_type", "action_detail", "outcome", "trust_level",
    "parent_record_id", "prev_hash", "record_phase",
)

DRAFT = "draft-sharif-agent-audit-trail-01"

# Section 3.1 phase vocabulary, and our two-value subset of it.
_PHASE = {ledger.PHASE_PRE: "pre_execution", ledger.PHASE_POST: "post_execution"}


def record_hash(record: dict) -> str:
    """Section 6.1: prev_hash(N) = hex(SHA-256(JCS(record(N-1)))).

    Section 6.2 additionally requires the `signature` field to be removed before
    hashing, so a signed record and its unsigned preimage agree.
    """
    payload = {k: v for k, v in record.items() if k != "signature"}
    return hashlib.sha256(jcs.canonicalize(payload)).hexdigest()


def _outcome(rec: dict) -> str:
    """Map to the draft's closed vocabulary: success/failure/timeout/denied/escalated.

    We record what the harness reported and cannot see a policy engine's verdict, so a
    pre-execution record is not claimed as `denied` or `escalated`. Section 4.2 makes
    those MUST be pre_execution, and asserting one we did not observe would be exactly
    the overclaim this project exists to avoid.
    """
    meta = rec.get("meta") or {}
    response = meta.get("response") or {}
    if isinstance(response, dict):
        if response.get("error") or response.get("is_error"):
            return "failure"
    return "success"


def to_aat(rec: dict, *, prev: dict | None, agent_version: str,
           trust_level: str = "L1") -> dict:
    """Map one native ledger record onto the draft's mandatory fields.

    `trust_level` defaults to L1 rather than something flattering: we observe a harness
    reporting its own actions, which is a low assurance level, and the draft's L0-L4
    scale is meaningless if every implementer writes L4.
    """
    phase = _PHASE.get(rec.get("phase") or "", "post_execution")
    return {
        "record_id": str(uuid.uuid4()),
        "timestamp": rec.get("ts"),
        "agent_id": f"urn:hsm:agent:{rec.get('agent') or 'unknown'}",
        "agent_version": agent_version,
        "session_id": rec.get("session") or str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"hsm-session-{rec.get('session')}")),
        "action_type": "tool_call" if rec.get("action") == "tool_call" else "decision",
        "action_detail": {
            "tool": rec.get("target"),
            "target": rec.get("summary"),
            **({"meta": rec["meta"]} if rec.get("meta") else {}),
        },
        "outcome": _outcome(rec),
        "trust_level": trust_level,
        "parent_record_id": prev["record_id"] if prev else None,
        "prev_hash": record_hash(prev) if prev else None,
        "record_phase": phase,
    }


def sign_p1363(payload_hash: bytes, private_key) -> str:
    """ECDSA P-256 signature, Base64url, IEEE P1363 fixed r||s (64 bytes).

    Section 6.2 is specific about the encoding and `cryptography` returns DER, so the
    signature is decoded and re-emitted as fixed-width r||s. Shipping DER here would
    verify fine against our own code and fail against every conformant verifier, which
    is the most common ECDSA interop bug and it fails silently.
    """
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils

    der = private_key.sign(payload_hash, ec.ECDSA(asym_utils.Prehashed(hashes.SHA256())))
    r, s = asym_utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def aat_export(vault: Path | str | None = None, out_dir: Path | str | None = None,
               *, key_path: Path | str | None = None) -> dict:
    """Write the ledger as a chained AAT record stream (JSON Lines)."""
    from .. import __version__

    root = vaultlib._resolve(vault)
    records = ledger.read_all(root)
    destination = (Path(out_dir).expanduser() if out_dir is not None
                   else Path.cwd() / f"aat-{root.name}.jsonl")
    destination.parent.mkdir(parents=True, exist_ok=True)

    out: list[dict] = []
    prev: dict | None = None
    for rec in records:
        aat = to_aat(rec, prev=prev, agent_version=__version__)
        out.append(aat)
        prev = aat

    destination.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    return {
        "format": DRAFT,
        "out": str(destination),
        "records": len(out),
        "signed": False,          # signing is opt-in; unsigned is stated, never implied
    }
