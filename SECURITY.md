# Security Policy

`homestead-memory` is local-first software that reads and writes a Markdown vault on your own machine. We take the integrity of the package and its supply chain seriously.

## Supported versions

We support the latest released minor version. Please upgrade before you report an issue.

| Version | Supported |
| ------- | --------- |
| 0.2.x   | ✅        |
| < 0.2   | ❌        |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub:

1. Go to the **Security** tab of this repository.
2. Select **Report a vulnerability** (GitHub Private Vulnerability Reporting).

If you cannot use that channel, email **security@fuckbigtech.ai**.

Include the version, your platform, reproduction steps, and the impact you observed. We aim to acknowledge a report within **3 business days** and to agree on a disclosure timeline with you.

## Scope

In scope:

- The `homestead-memory` package (the `hsm` CLI, the SDK, the MCP server, and the local HTTP API).
- The build and release pipeline (wheel integrity, publication).

Out of scope:

- Vulnerabilities in third-party dependencies (report those upstream).
- The five-agent release review, which is a local/VPS-only process and never runs in CI. See [`SECURITY_RELEASE.md`](SECURITY_RELEASE.md) for that boundary.

## Cryptography, and the one piece that is hand-carried

**Signing uses [`cryptography`](https://cryptography.io/). Only the EvidencePack verifier
is hand-carried.** Most readers assume the opposite when they hear "bundled Ed25519", so
it is worth stating first: no key generation and no signing in this project is done with
our own code.

`src/homestead_memory/evidence_verifier.py` contains a pure-Python Ed25519
implementation, and a copy of it ships inside every EvidencePack. That is deliberate. The
point of a pack is that a third party can check it **without installing anything from
us**, including our package. A verifier that required `pip install cryptography` would
put the recipient back in the position of trusting a dependency chain to check a record
they were given precisely because they do not trust the sender.

Rolling crypto is a legitimate thing to object to, so here is exactly what reduces the
risk, and what remains:

- **Verify only. No secret-key operations.** The side-channel and timing concerns that
  make hand-rolled cryptography dangerous apply to operations on secret keys. This code
  touches only public data: a public key, a signature, and a message.
- **It is RFC 8032's reference implementation**, cited in the file. The claim is "this is
  the specification's own code", not "we designed a scheme".
- **Differentially tested against `cryptography`** in `tests/test_evidence.py`, on valid
  signatures *and* on forgeries. Agreement on valid signatures alone would be worthless:
  a verifier that returns `True` unconditionally passes every positive test. The negative
  cases are the real suite.
- **Negative cases asserted:** forged signature, wrong public key, mutated message,
  truncated signature, malformed hex, and key substitution (a rewritten chain re-signed
  with a fresh key, with `pubkey.hex` replaced). The last one is reported as
  `SELF-ASSERTED`, never `VERIFIED`, unless the checker pins the expected key with
  `--pubkey`.

**Residual risk, stated rather than argued away.** This implementation is not constant
time, has not been independently audited, and is not suitable for any purpose beyond
verifying an EvidencePack. If you are checking packs at scale or in a hostile setting,
verify with `cryptography` instead: the signature is a standard Ed25519 signature over a
documented byte string, so any conformant implementation will do.

Findings against the verifier are **in scope** and welcome. See the reporting section
above.

## Release integrity

Releases are published to PyPI through GitHub Actions Trusted Publishing (OIDC, no long-lived tokens). Each wheel passes an automated inspection (`scripts/verify_artifact.py`) that checks the version and scans for leaked secrets before publication.
