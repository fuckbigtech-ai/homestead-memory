#!/usr/bin/env python3
"""evidence.py — build an EvidencePack a third party can verify without us.

The point of the pack is that it does not require trusting the people who produced it.
If checking our evidence meant installing our tool, the auditor would be trusting the
vendor's code to vouch for the vendor's own record. So a pack ships a stdlib-only
verifier they can read in full, and a README that states what the pack cannot prove.

Ledger-only by design: Article 12 asks for a record of EVENTS, and keeping the memory
contents out means an auditor never receives the substance of someone's work in order
to check that the log is intact.

WINDOWING AND THE ANCHOR
A retention window (6 months, 24 for biometric and law enforcement) is the normal case,
so most packs are a slice rather than the whole chain. A slice cannot chain to genesis:
its first record's `prev_hash` names a record that is not in the pack. The manifest
therefore carries that value as `anchor_hash`, and the verifier prints that records
before the anchor are not covered. Without it the pack silently appears to prove more
than it does.

WINDOWED PACKS ARE SIGNED FRESH
The vault checkpoint signs the head of the WHOLE ledger. Reusing it in a windowed pack
would produce a signature that verifies cryptographically but covers a head the pack
does not contain, which is worse than no signature: it looks like proof. Each pack is
therefore signed over its own window (`<window_head>:<record_count>`), with the same
key, at export time.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import ledger, provenance, vault as vaultlib, verify

PACK_FORMAT = "homestead-evidence"
PACK_VERSION = 1


def _sign_window(head_hash: str, count: int, key_path=None) -> tuple[dict | None, str | None]:
    """Sign this pack's own window. Returns (sig_dict, pubkey_hex) or (None, None).

    Unsigned is a degraded pack, not a failed export: the chain still proves nobody
    edited the records in place, and refusing to export because an optional dependency
    is missing would just mean no evidence at all. The manifest says which it is.
    """
    try:
        from . import signing
        _ed, serialization, _inv = signing._ed25519()
        key = signing.load_or_create_key(key_path)
        payload = f"{head_hash}:{count}".encode("utf-8")
        pub = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        return {
            "head_hash": head_hash,
            "records": count,
            "ts": provenance.now_ts(),
            "signer_pubkey": pub.hex(),
            "signature": key.sign(payload).hex(),
            "alg": "ed25519",
            "sig_version": 1,
        }, pub.hex()
    except (RuntimeError, OSError):
        return None, None


def build_pack(
    vault: Path | str | None = None,
    out_dir: Path | str | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
    from_seq: int | None = None,
    to_seq: int | None = None,
    key_path: Path | str | None = None,
) -> dict:
    """Write an EvidencePack directory. Returns a summary dict."""
    root = vaultlib._resolve(vault)
    all_records = ledger.read_all(root)
    if not all_records:
        raise ValueError(
            "no ledger records to export. install the capture hook first "
            "(`hsm hook --install`), or point at a vault that has one."
        )

    records = all_records
    # Timestamps carry SECOND granularity, so a busy agent puts many records in the
    # same second and a ts window cannot split them. That is fine for a retention
    # window (6 months, 24 for biometric and law enforcement) and useless for an exact
    # slice, so sequence bounds exist too and take precedence when given.
    if since:
        records = [r for r in records if str(r.get("ts", "")) >= since]
    if until:
        records = [r for r in records if str(r.get("ts", "")) <= until]
    if from_seq is not None:
        records = [r for r in records if isinstance(r.get("seq"), int) and r["seq"] >= from_seq]
    if to_seq is not None:
        records = [r for r in records if isinstance(r.get("seq"), int) and r["seq"] <= to_seq]
    if not records:
        raise ValueError("no ledger records in the requested window")

    # The anchor is what the first included record chains BACK to. For a full export
    # that is genesis; for a slice it names a record deliberately left out.
    anchor = records[0].get("prev_hash", ledger.GENESIS_HASH)
    head = records[-1].get("hash", "")
    windowed = len(records) != len(all_records)

    out = Path(out_dir).expanduser() if out_dir else Path.cwd() / f"evidence-{provenance.now_ts()[:10]}"
    out.mkdir(parents=True, exist_ok=True)

    # Integrity report at export time. An auditor should see the score AND any recorded
    # gaps, not a curated subset.
    try:
        rep = verify.verify_vault(root, deep=True)
        drops = len(ledger.read_drops(root))
        integrity = {
            "score": rep.get("score"),
            "stamp": rep.get("stamp"),
            "rotbench_version": rep.get("rotbench_version"),
            "notes": rep.get("notes"),
            "ledger_drops": drops,
            "ledger_findings": [f for f in rep.get("findings", [])
                                if f.get("check", "").startswith("ledger")],
        }
    except Exception as e:                      # noqa: BLE001 - never block an export
        integrity = {"error": f"integrity report unavailable: {e}"}
        drops = len(ledger.read_drops(root))
        integrity["ledger_drops"] = drops

    sig, pubkey = _sign_window(head, len(records), key_path)

    manifest = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "exported_at": provenance.now_ts(),
        "record_count": len(records),
        "window_from_seq": records[0].get("seq"),
        "window_to_seq": records[-1].get("seq"),
        "window_from_ts": records[0].get("ts"),
        "window_to_ts": records[-1].get("ts"),
        "windowed": windowed,
        "anchor_hash": anchor,
        "head_hash": head,
        "signed": sig is not None,
        "ledger_version": ledger.LEDGER_VERSION,
        "integrity": integrity,
    }

    (out / "ledger.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
                for r in records), encoding="utf-8")
    (out / "MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                                       encoding="utf-8")
    (out / "integrity.json").write_text(json.dumps(integrity, sort_keys=True, indent=2) + "\n",
                                        encoding="utf-8")
    if sig:
        (out / "ledger.sig").write_text(json.dumps(sig, sort_keys=True, indent=2) + "\n",
                                        encoding="utf-8")
        (out / "pubkey.hex").write_text(pubkey + "\n", encoding="utf-8")

    # Copy the verifier verbatim rather than generating it, so what ships is the same
    # file the test suite exercises.
    from .. import evidence_verifier
    shutil.copyfile(Path(evidence_verifier.__file__), out / "verify_evidence.py")

    (out / "README.md").write_text(_readme(manifest), encoding="utf-8")
    return {"pack": str(out), "records": len(records), "signed": sig is not None,
            "windowed": windowed, "anchor": anchor, "head": head}


def _readme(m: dict) -> str:
    drops = (m.get("integrity") or {}).get("ledger_drops", 0)
    window = (f"records {m['window_from_seq']} to {m['window_to_seq']}, "
              f"{m['window_from_ts']} to {m['window_to_ts']}")
    lines = [
        "# EvidencePack",
        "",
        f"A tamper-evident record of agent actions. {m['record_count']} records, {window}.",
        "",
        "## Verify it",
        "",
        "```bash",
        "python3 verify_evidence.py",
        "```",
        "",
        "No installation. The verifier uses only the Python standard library, including",
        "its Ed25519 check, so you do not have to install anything from the party that",
        "produced this pack in order to check it. Read `verify_evidence.py` first: it is",
        "short, and it is the entire verification.",
        "",
        "## What a PASS proves",
        "",
        "- No record in this window was edited, deleted, reordered, or truncated. Each",
        "  record carries the SHA-256 of the record before it, so any alteration breaks",
        "  every hash that follows.",
        ("- The window's head was signed by the holder of the key in `pubkey.hex`."
         if m["signed"] else
         "- NOTHING about authenticity. This pack is UNSIGNED, so a wholly rebuilt chain"),
    ]
    if not m["signed"]:
        lines.append("  would go undetected. Treat it as an integrity check only.")
    lines += [
        "",
        "## What a PASS does NOT prove",
        "",
        "- **Origin, unless you pin the key.** This matters more than anything else here.",
        "  `pubkey.hex` travels inside the pack it authenticates, so whoever can rewrite",
        "  the ledger can also generate a fresh key, re-sign the rewritten chain, and",
        "  replace `pubkey.hex`. That forgery verifies. A signature only means something",
        "  when you already know which key to expect. Obtain the signer's public key",
        "  through a separate channel and pass it:",
        "",
        "  ```bash",
        "  python3 verify_evidence.py --pubkey <the key you were given separately>",
        "  ```",
        "",
        "  Without `--pubkey` the verifier reports the signature as SELF-ASSERTED rather",
        "  than verified, because that is what it is.",
        "- **That the agent performed these actions.** Records are what the harness",
        "  reported at the time. This is a faithful log, not an independent witness.",
        "- **That nothing is missing.** Events that never reached the ledger leave no",
        "  trace here, by definition. That includes anything done before recording was",
        "  switched on.",
    ]
    if m.get("windowed"):
        lines += [
            f"- **Anything before the anchor.** This pack is a window. It begins at anchor",
            f"  `{m['anchor_hash'][:16]}...`, and records before that point are not included",
            "  and are not covered by this verification.",
        ]
    if drops:
        lines += [
            "",
            "## Known gaps",
            "",
            f"The source ledger recorded **{drops} failure(s) to record an event**. This pack",
            "is internally consistent, but the original log knows it is incomplete. This is",
            "disclosed rather than omitted; see `integrity.json`.",
        ]
    lines += [
        "",
        "## Files",
        "",
        "| File | What it is |",
        "|---|---|",
        "| `ledger.jsonl` | the action records, one JSON object per line |",
        "| `MANIFEST.json` | window, anchor, counts, versions |",
        "| `integrity.json` | the integrity report at export time |",
        "| `verify_evidence.py` | the standalone verifier |",
    ]
    if m["signed"]:
        lines += ["| `ledger.sig` | Ed25519 signature over `<head_hash>:<record_count>` |",
                  "| `pubkey.hex` | the public key that signature must verify against |"]
    lines += [
        "",
        "## Checking it with your own tools",
        "",
        "The signature is a standard Ed25519 signature over the ASCII string",
        "`<head_hash>:<record_count>` taken from `ledger.sig`. Any Ed25519 implementation",
        "will verify it. The chain is SHA-256 over each record's canonical JSON with the",
        "`hash` field removed, serialized with sorted keys and no whitespace.",
        "",
    ]
    return "\n".join(lines)
