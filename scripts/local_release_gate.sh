#!/usr/bin/env bash
set -euo pipefail

# Run before creating a release tag. This is intentionally local/VPS-only:
# source diffs and reviewer prompts never enter GitHub-hosted runners.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
: "${FIVEPASS_BACKEND:=ollama}"
: "${FIVEPASS_TIMEOUT_SECONDS:=600}"
export FIVEPASS_BACKEND FIVEPASS_TIMEOUT_SECONDS

rm -f fivepass-report.json
python3 scripts/release_5pass.py
python3 -m pytest -q
rm -rf dist
python3 -m pip wheel . --no-deps -w dist >/dev/null
python3 scripts/verify_artifact.py
printf '%s\n' "local release gate passed; do not upload fivepass-report.json"
