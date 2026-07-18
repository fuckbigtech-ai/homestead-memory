#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_HOST:?set DEPLOY_HOST}"
: "${DEPLOY_USER:?set DEPLOY_USER}"
: "${DEPLOY_KNOWN_HOSTS:?set DEPLOY_KNOWN_HOSTS}"
: "${RELEASE_VERSION:?set RELEASE_VERSION}"
: "${WHEEL_URL:?set WHEEL_URL}"

known_hosts="$(mktemp)"
trap 'rm -f "$known_hosts"' EXIT
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" >"$known_hosts"
ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts")
ssh "${ssh_opts[@]}" "$DEPLOY_USER@$DEPLOY_HOST" \
  "RELEASE_VERSION='$RELEASE_VERSION' WHEEL_URL='$WHEEL_URL' bash -s" <<'REMOTE'
set -euo pipefail
base="$HOME/.local/share/homestead-memory/releases"
release="$base/$RELEASE_VERSION"
mkdir -p "$release"
curl --fail --location --proto '=https' --tlsv1.2 "$WHEEL_URL" -o "$release/homestead.whl"
python3 -m venv "$release/venv"
"$release/venv/bin/pip" install --no-deps "$release/homestead.whl"
test "$("$release/venv/bin/hsm" --version | head -1)" = *"$RELEASE_VERSION"*
REMOTE
