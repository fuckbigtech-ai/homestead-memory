#!/usr/bin/env python3
"""Detached Ed25519 signing for a vault's canonical markdown state.

The core package stays dependency-free. Ed25519 imports are deliberately lazy so
unsigned vaults and normal verification keep working without the optional
`homestead-memory[sign]` extra.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import provenance, store, vault as vaultlib

DEFAULT_KEY_PATH = Path("~/.config/homestead-memory/ed25519_key")
SIG_REL = Path(".hsm") / "vault.sig"
ALG = "ed25519"
SIG_VERSION = 1
_INSTALL_HINT = "install homestead-memory[sign]"


def _ed25519():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
    except ImportError as e:
        raise RuntimeError(_INSTALL_HINT) from e
    return ed25519, serialization, InvalidSignature


def _key_path(key_path: Path | str | None = None) -> Path:
    chosen = key_path or os.environ.get("HSM_SIGNING_KEY") or DEFAULT_KEY_PATH
    return Path(chosen).expanduser()


def vault_state_hash(vault: Path | str | None = None) -> str:
    """SHA-256 over sorted `<relpath>\\n<sha256(content)>\\n` records for all .md files.

    Content is newline-normalized before hashing (`CRLF` and bare `CR` become
    `LF`) so cross-platform checkouts do not invalidate a signature. Symlinked
    markdown files are skipped; the attestation covers vault-owned note files,
    not external paths reached through links.
    """
    root = vaultlib._resolve(vault)
    records: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not (rel_dir == Path(".") and d == ".hsm"))
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            p = Path(dirpath) / fn
            if p.is_symlink():
                continue
            rel = p.relative_to(root).as_posix()
            body = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            body_hash = hashlib.sha256(body).hexdigest()
            records.append((rel, body_hash))
    canonical = "".join(f"{rel}\n{body_hash}\n" for rel, body_hash in sorted(records))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_or_create_key(path: Path | str | None = None):
    """Load or create a raw Ed25519 private seed at `path` with 0600 permissions."""
    ed25519, serialization, _InvalidSignature = _ed25519()
    p = _key_path(path)
    if p.exists():
        if p.is_symlink():
            raise RuntimeError(f"refusing to load signing key through symlink: {p}")
        if hasattr(os, "chmod"):
            try:
                mode = p.stat().st_mode & 0o777
                if mode & 0o077:
                    os.chmod(p, 0o600)
            except OSError as e:
                raise RuntimeError(f"could not secure signing key permissions for {p}: {e}") from e
        return ed25519.Ed25519PrivateKey.from_private_bytes(p.read_bytes())

    p.parent.mkdir(parents=True, exist_ok=True)
    private_key = ed25519.Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw_private)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
        raise

    pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_path = Path(str(p) + ".pub")
    pub_path.write_bytes(pub)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return private_key


def sign_vault(vault: Path | str | None = None, key_path: Path | str | None = None) -> dict:
    """Sign the current canonical vault hash and write `<vault>/.hsm/vault.sig`."""
    _ed25519_mod, serialization, _InvalidSignature = _ed25519()
    root = vaultlib._resolve(vault)
    private_key = load_or_create_key(key_path)
    h = vault_state_hash(root)
    signature = private_key.sign(h.encode("utf-8"))
    pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    sig = {
        "vault_hash": h,
        "ts": provenance.now_ts(),
        "signer_pubkey": pub.hex(),
        "signature": signature.hex(),
        "alg": ALG,
        "sig_version": SIG_VERSION,
    }
    store.atomic_write(root / SIG_REL, json.dumps(sig, sort_keys=True) + "\n")
    return sig


def verify_signature(vault: Path | str | None = None, expect_pubkey: str | None = None) -> dict:
    """Verify `<vault>/.hsm/vault.sig`, classifying current/stale/invalid signer state."""
    root = vaultlib._resolve(vault)
    sig_path = root / SIG_REL
    if not sig_path.exists():
        return {"state": "unsigned"}

    try:
        ed25519, _serialization, InvalidSignature = _ed25519()
    except RuntimeError:
        return {"state": "unverifiable", "reason": "cryptography not installed"}

    try:
        sig = json.loads(sig_path.read_text())
        signer = str(sig["signer_pubkey"])
        signed_hash = str(sig["vault_hash"])
        signature = bytes.fromhex(str(sig["signature"]))
        pub = bytes.fromhex(signer)
        if sig.get("alg") != ALG or sig.get("sig_version") != SIG_VERSION:
            return {"state": "invalid"}
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub)
        public_key.verify(signature, signed_hash.encode("utf-8"))
    except InvalidSignature:
        return {"state": "invalid"}
    except Exception:
        return {"state": "invalid"}

    if expect_pubkey is not None and signer.casefold() != expect_pubkey.strip().casefold():
        return {"state": "wrong_signer", "signer": signer, "ts": sig.get("ts")}

    current = vault_state_hash(root)
    base = {"signer": signer, "ts": sig.get("ts")}
    if signed_hash == current:
        return {"state": "valid_current", **base}
    return {"state": "valid_stale", **base}
