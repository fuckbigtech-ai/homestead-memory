# Release security boundary

The five-agent review is a local/VPS-only operation. Run
`scripts/local_release_gate.sh` before creating a tag. It reviews only the git
diff, uses a bounded backend, fails closed on missing/failed lenses, and leaves
the raw report untracked. Do not upload reviewer transcripts or prompts to CI.

GitHub-hosted Actions are limited to deterministic tests, package inspection,
wheel creation, and artifact publication. They receive no vault, SSH key, model
credential, or fivepass backend credential.

The VPS deployment identity must be a restricted user with pinned host keys.
PyPI should use Trusted Publishing; if a token is used temporarily, rotate it
after publication and never commit or print it.
