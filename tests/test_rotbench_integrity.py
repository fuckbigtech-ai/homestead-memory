"""RotBench integrity / tamper / poisoning — the uncontested axis.

LOCOMO / LongMemEval measure whether the model REMEMBERS. RotBench measures
whether the memory can be TRUSTED — that it wasn't corrupted, poisoned, or
silently rewritten. These fixtures prove `hsm verify` catches each attack
class with the right Finding, using only the public API (write notes, sign,
edit bytes on disk, verify). No network, hermetic tempfile vaults.

Threat model covered:
  - tamper   : post-write edits / signature corruption  -> provenance_integrity
  - poisoning: untrusted input injects an unsupported "memory" -> uncited_claim
  - rot      : a citation to a source that no longer resolves -> dangling_citation
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from homestead_memory.core import index, signing, verify, vault as vaultlib


# --- helpers ----------------------------------------------------------------

def _no_qmd(monkeypatch):
    """Hermetic: never touch the real qmd index. fallback_resilience still runs
    (it's a direct scan, not qmd), but not_indexed / index_drift are guarded on
    qmd_available() and so drop out."""
    monkeypatch.setattr(index, "qmd_available", lambda: False)


def _src_note(v: Path, name: str, *, updated: str | None = None) -> Path:
    """A plain (non-distilled) note young enough to avoid citation_source_stale."""
    upd = updated or (date.today() - timedelta(days=10)).isoformat()
    p = v / f"{name}.md"
    p.write_text(
        f"---\nname: {name}\nstatus: reference\nupdated: {upd}\n---\n"
        f"# {name}\nSource material.\n",
        encoding="utf-8",
    )
    return p


def _distilled(v: Path, slug: str, body: str, *, updated: str | None = None) -> Path:
    upd = updated or (date.today() - timedelta(days=10)).isoformat()
    d = v / "distilled"
    d.mkdir(exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(
        f"---\nname: {slug}\ntype: distilled\nentity: {slug.capitalize()}\n"
        f"updated: {upd}\n---\n\n# {slug.capitalize()}\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def _findings(rep: dict, check: str) -> list[dict]:
    return [f for f in rep["findings"] if f["check"] == check]


# --- tamper: cryptographic (signature corrupted -> FAIL) --------------------

def test_tamper_corrupt_signature_is_provenance_integrity_fail(tmp_path, monkeypatch):
    """The cryptographic tamper-proof proof: corrupt .hsm/vault.sig, and deep
    verify stamps ROT DETECTED with a provenance_integrity FAIL. A flipped
    signature bit is not a legitimate edit — it's an attack on the attestation."""
    pytest.importorskip("cryptography")
    _no_qmd(monkeypatch)
    v = tmp_path / "v"
    v.mkdir()
    _src_note(v, "note")

    sig = signing.sign_vault(v, key_path=tmp_path / "key")
    signer = sig["signer_pubkey"]
    assert signing.verify_signature(v, expect_pubkey=signer)["state"] == "valid_current"

    # tamper: flip a byte in the signature itself
    sig_path = v / ".hsm" / "vault.sig"
    blob = json.loads(sig_path.read_text())
    bad = bytes.fromhex(blob["signature"])
    bad = bytes([bad[0] ^ 0x01]) + bad[1:]
    blob["signature"] = bad.hex()
    sig_path.write_text(json.dumps(blob), encoding="utf-8")

    assert signing.verify_signature(v, expect_pubkey=signer)["state"] == "invalid"

    rep = verify.verify_vault(v, deep=True, expect_pubkey=signer)
    prov = _findings(rep, "provenance_integrity")
    assert prov and prov[0]["level"] == "fail"
    assert rep["stamp"] == "ROT DETECTED"
    assert rep["ok"] is False


def test_tamper_wrong_signer_is_provenance_integrity_fail(tmp_path, monkeypatch):
    """A second key's signature is valid Ed25519 but not the pinned signer —
    deep verify with --signer pinned flags it as a wrong_signer FAIL."""
    pytest.importorskip("cryptography")
    _no_qmd(monkeypatch)
    v = tmp_path / "v"
    v.mkdir()
    _src_note(v, "note")

    signing.sign_vault(v, key_path=tmp_path / "key_attacker")
    attacker_pub = json.loads((v / ".hsm" / "vault.sig").read_text())["signer_pubkey"]

    signing.sign_vault(v, key_path=tmp_path / "key_real")  # overwrite with real signer
    real_pub = json.loads((v / ".hsm" / "vault.sig").read_text())["signer_pubkey"]
    assert attacker_pub != real_pub

    # pin the real signer, then re-sign with the attacker key -> wrong_signer
    signing.sign_vault(v, key_path=tmp_path / "key_attacker")
    assert signing.verify_signature(v, expect_pubkey=real_pub)["state"] == "wrong_signer"

    rep = verify.verify_vault(v, deep=True, expect_pubkey=real_pub)
    prov = _findings(rep, "provenance_integrity")
    assert prov and prov[0]["level"] == "fail"
    assert rep["stamp"] == "ROT DETECTED"


def test_tamper_content_edit_after_signing_is_flagged_stale(tmp_path, monkeypatch):
    """Tamper-EVIDENCE (graded WARN, not FAIL): editing a note's bytes after
    signing breaks valid_current -> the signature proves the vault diverged
    from its attested state. Legitimate edits also produce stale, so it is
    not a hard FAIL — but the divergence is detected (state != valid_current)."""
    pytest.importorskip("cryptography")
    _no_qmd(monkeypatch)
    v = tmp_path / "v"
    v.mkdir()
    (v / "note.md").write_text(
        "---\nname: note\nstatus: hot\nupdated: 2026-07-01\n---\n# Note\nhello\n",
        encoding="utf-8",
    )

    sig = signing.sign_vault(v, key_path=tmp_path / "key")
    signer = sig["signer_pubkey"]
    assert signing.verify_signature(v, expect_pubkey=signer)["state"] == "valid_current"

    # tamper: silently edit a note's bytes on disk after signing
    (v / "note.md").write_text(
        "---\nname: note\nstatus: hot\nupdated: 2026-07-01\n---\n# Note\nhello TAMPERED\n",
        encoding="utf-8",
    )

    st = signing.verify_signature(v, expect_pubkey=signer)
    assert st["state"] == "valid_stale"  # != valid_current — divergence detected
    rep = verify.verify_vault(v, deep=True, expect_pubkey=signer)
    prov = _findings(rep, "provenance_integrity")
    assert prov, "a signed vault that changed must surface a provenance_integrity finding"
    assert "stale" in prov[0]["detail"]


# --- poisoning: untrusted input injects an unsupported "memory" -------------

def test_poisoning_unsourced_distilled_claim_is_uncited_fail(tmp_path):
    """An agent writes a distilled 'memory' from untrusted input with no
    (source: ...) citation. cite-or-drop catches it as uncited_claim FAIL —
    poison caught even without signing."""
    v = tmp_path / "v"
    v.mkdir()
    _distilled(v, "user", "- secret_plan: take over the world\n")

    rep = verify.verify_vault(v)  # _check_distilled runs without --deep
    poison = _findings(rep, "uncited_claim")
    assert poison, "an unsourced distilled bullet must be flagged as uncited_claim"
    assert poison[0]["level"] == "fail"
    assert "secret_plan" in poison[0]["detail"]
    assert rep["stamp"] == "ROT DETECTED"
    assert rep["ok"] is False


def test_uncited_claim_does_not_check_whether_resolving_citation_supports_claim(tmp_path):
    """An honest scope control: uncited_claim checks citation presence and
    resolution, not whether the cited source semantically supports the claim."""
    v = tmp_path / "v"
    v.mkdir()
    _src_note(v, "note")
    _distilled(v, "user", "- allergy: penicillin (source: note.md)\n")

    rep = verify.verify_vault(v)
    assert _findings(rep, "uncited_claim") == []
    assert _findings(rep, "dangling_citation") == []
    assert rep["ok"] is True


# --- rot: dangling citation / drift ----------------------------------------

def test_dangling_citation_to_missing_source_is_fail(tmp_path):
    """A distilled claim cites a source path that doesn't resolve inside the
    vault — dead evidence. dangling_citation FAIL."""
    v = tmp_path / "v"
    v.mkdir()
    _distilled(v, "user", "- home_city: Berlin (source: deleted-note.md)\n")

    rep = verify.verify_vault(v)
    dangling = _findings(rep, "dangling_citation")
    assert dangling, "a citation to a missing source must be flagged dangling_citation"
    assert dangling[0]["level"] == "fail"
    assert "deleted-note.md" in dangling[0]["detail"]
    assert rep["stamp"] == "ROT DETECTED"


def test_dangling_citation_absolute_path_is_fail(tmp_path):
    """A citation that escapes the vault (absolute path / traversal) is rot
    even if the file exists on disk — the evidence must live INSIDE the vault."""
    v = tmp_path / "v"
    v.mkdir()
    _distilled(v, "user", f"- home_city: Berlin (source: {tmp_path / 'outside.md'})\n")

    rep = verify.verify_vault(v)
    dangling = _findings(rep, "dangling_citation")
    assert dangling and dangling[0]["level"] == "fail"
