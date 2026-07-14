import json
import os
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from homestead_memory.cli import main
from homestead_memory.core import signing


NOTE = """\
---
name: note
status: hot
updated: 2026-07-01
---
# Note
hello
"""


def _vault(root: Path) -> Path:
    root.mkdir()
    (root / "note.md").write_text(NOTE, encoding="utf-8")
    return root


def _quiet_deep(monkeypatch):
    from homestead_memory.core import index

    monkeypatch.setattr(index, "qmd_available", lambda: False)


def _verify_json(root: Path, capsys, *extra: str) -> tuple[int, dict]:
    rc = main(["verify", str(root), "--deep", "--json", *extra])
    rep = json.loads(capsys.readouterr().out)
    return rc, rep


def test_sign_vault_writes_signature_and_verifies_current(tmp_path):
    root = _vault(tmp_path / "v")
    key = tmp_path / "key"

    sig = signing.sign_vault(root, key_path=key)

    sig_path = root / ".hsm" / "vault.sig"
    assert sig_path.exists()
    on_disk = json.loads(sig_path.read_text())
    assert set(on_disk) == {"vault_hash", "ts", "signer_pubkey", "signature",
                            "alg", "sig_version"}
    assert on_disk["vault_hash"] == sig["vault_hash"]
    assert on_disk["alg"] == "ed25519"
    assert on_disk["sig_version"] == 1
    assert signing.verify_signature(root)["state"] == "valid_current"


def test_edit_after_signing_is_stale_warn_not_fail(tmp_path, capsys, monkeypatch):
    _quiet_deep(monkeypatch)
    root = _vault(tmp_path / "v")
    sig = signing.sign_vault(root, key_path=tmp_path / "key")
    (root / "note.md").write_text(NOTE.replace("hello", "hello again"), encoding="utf-8")

    assert signing.verify_signature(root)["state"] == "valid_stale"
    rc, rep = _verify_json(root, capsys)

    assert rc == 0
    findings = [f for f in rep["findings"] if f["check"] == "provenance_integrity"]
    assert findings == [{
        "level": "warn",
        "check": "provenance_integrity",
        "note": ".hsm/vault.sig",
        "detail": "signature is stale: vault changed since signing; "
                  f"signed by {sig['signer_pubkey'][:16]}…; run `hsm sign` to re-attest",
    }]
    assert rep["score"] == 85
    assert rep["ok"] is True


def test_corrupt_signature_is_invalid_and_deep_verify_fails(tmp_path, capsys, monkeypatch):
    _quiet_deep(monkeypatch)
    root = _vault(tmp_path / "v")
    signing.sign_vault(root, key_path=tmp_path / "key")
    sig_path = root / ".hsm" / "vault.sig"
    sig = json.loads(sig_path.read_text())
    sig["signature"] = "not-hex"
    sig_path.write_text(json.dumps(sig), encoding="utf-8")

    assert signing.verify_signature(root)["state"] == "invalid"
    rc, rep = _verify_json(root, capsys)

    assert rc == 1
    findings = [f for f in rep["findings"] if f["check"] == "provenance_integrity"]
    assert findings
    assert findings[0]["level"] == "fail"
    assert rep["stamp"] == "ROT DETECTED"


def test_unsigned_deep_verify_has_no_provenance_integrity_finding(tmp_path, capsys, monkeypatch):
    _quiet_deep(monkeypatch)
    root = _vault(tmp_path / "v")

    assert signing.verify_signature(root)["state"] == "unsigned"
    rc, rep = _verify_json(root, capsys)

    assert rc == 0
    assert all(f["check"] != "provenance_integrity" for f in rep["findings"])


def test_pinned_signer_deleted_signature_fails_deep_verify(tmp_path, capsys, monkeypatch):
    _quiet_deep(monkeypatch)
    root = _vault(tmp_path / "v")
    sig = signing.sign_vault(root, key_path=tmp_path / "key")
    (root / ".hsm" / "vault.sig").unlink()

    assert signing.verify_signature(root, expect_pubkey=sig["signer_pubkey"])["state"] == "unsigned"
    rc, rep = _verify_json(root, capsys, "--signer", sig["signer_pubkey"])

    assert rc == 1
    findings = [f for f in rep["findings"] if f["check"] == "provenance_integrity"]
    assert findings == [{
        "level": "fail",
        "check": "provenance_integrity",
        "note": ".hsm/vault.sig",
        "detail": "signer pinned but vault is unsigned / unverifiable",
    }]


def test_signed_verify_warns_without_pin_and_clean_with_matching_pin(tmp_path, capsys, monkeypatch):
    _quiet_deep(monkeypatch)
    root = _vault(tmp_path / "v")
    sig = signing.sign_vault(root, key_path=tmp_path / "key")

    rc, rep = _verify_json(root, capsys)

    assert rc == 0
    findings = [f for f in rep["findings"] if f["check"] == "provenance_integrity"]
    assert findings == [{
        "level": "warn",
        "check": "provenance_integrity",
        "note": ".hsm/vault.sig",
        "detail": f"signed by {sig['signer_pubkey'][:16]}…; "
                  "pass --signer to pin the trusted signer",
    }]

    rc, rep = _verify_json(root, capsys, "--signer", sig["signer_pubkey"])

    assert rc == 0
    assert all(f["check"] != "provenance_integrity" for f in rep["findings"])


def test_vault_state_hash_is_deterministic_and_changes_on_note_edit(tmp_path):
    root = _vault(tmp_path / "v")
    (root / "sub").mkdir()
    (root / "sub" / "other.md").write_text(NOTE.replace("note", "other"), encoding="utf-8")
    (root / ".hsm").mkdir()
    (root / ".hsm" / "ignored.md").write_text("ignored", encoding="utf-8")

    h1 = signing.vault_state_hash(root)
    h2 = signing.vault_state_hash(root)
    (root / "note.md").write_text(NOTE.replace("hello", "changed"), encoding="utf-8")
    h3 = signing.vault_state_hash(root)

    assert h1 == h2
    assert h3 != h1


def test_vault_state_hash_normalizes_newlines(tmp_path):
    root = tmp_path / "v"
    root.mkdir()
    (root / "note.md").write_bytes(NOTE.replace("\n", "\r\n").encode("utf-8"))
    h_crlf = signing.vault_state_hash(root)

    (root / "note.md").write_text(NOTE, encoding="utf-8")
    h_lf = signing.vault_state_hash(root)

    assert h_crlf == h_lf


def test_vault_state_hash_skips_symlinked_markdown(tmp_path):
    root = _vault(tmp_path / "v")
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\nsecret\n", encoding="utf-8")

    before = signing.vault_state_hash(root)
    try:
        (root / "linked.md").symlink_to(outside)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink unavailable: {e}")
    after_link = signing.vault_state_hash(root)
    outside.write_text("# outside\nchanged secret\n", encoding="utf-8")
    after_target_edit = signing.vault_state_hash(root)

    assert after_link == before
    assert after_target_edit == before


def test_wrong_signer_pin_is_wrong_signer_and_deep_verify_fails(tmp_path, capsys, monkeypatch):
    _quiet_deep(monkeypatch)
    root = _vault(tmp_path / "v")
    signing.sign_vault(root, key_path=tmp_path / "key1")
    signing.load_or_create_key(tmp_path / "key2")
    wrong_pubkey = (tmp_path / "key2.pub").read_bytes().hex()

    assert signing.verify_signature(root, expect_pubkey=wrong_pubkey)["state"] == "wrong_signer"
    rc, rep = _verify_json(root, capsys, "--signer", wrong_pubkey)

    assert rc == 1
    findings = [f for f in rep["findings"] if f["check"] == "provenance_integrity"]
    assert findings
    assert findings[0]["level"] == "fail"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions (0600) not enforced on Windows")
def test_existing_key_is_chmodded_to_0600_on_load(tmp_path):
    key = tmp_path / "key"
    signing.load_or_create_key(key)
    os.chmod(key, 0o644)

    signing.load_or_create_key(key)

    assert key.stat().st_mode & 0o777 == 0o600


def test_symlinked_key_is_refused_on_load(tmp_path):
    key = tmp_path / "key"
    signing.load_or_create_key(key)
    link = tmp_path / "key-link"
    try:
        link.symlink_to(key)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink unavailable: {e}")

    with pytest.raises(RuntimeError, match="refusing to load signing key through symlink"):
        signing.load_or_create_key(link)
