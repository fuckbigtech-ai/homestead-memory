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

## Release integrity

Releases are published to PyPI through GitHub Actions Trusted Publishing (OIDC, no long-lived tokens). Each wheel passes an automated inspection (`scripts/verify_artifact.py`) that checks the version and scans for leaked secrets before publication.
