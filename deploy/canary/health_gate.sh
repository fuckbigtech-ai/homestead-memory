#!/usr/bin/env bash
set -euo pipefail

: "${HSM_VAULT:?set HSM_VAULT}"
: "${HSM_CANARY_STATE_DIR:?set HSM_CANARY_STATE_DIR}"
: "${HSM_CANARY_COLLECTION:?set HSM_CANARY_COLLECTION}"

report="$(hsm qmd refresh "$HSM_VAULT" --json)"
printf '%s\n' "$report"
python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("fresh") is True and d.get("pending_embeddings") == 0' <<<"$report"
doctor="$(hsm qmd doctor "$HSM_VAULT" --json)"
printf '%s\n' "$doctor"
python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("runtime_ok") is True and d.get("collection_present") is True' <<<"$doctor"
