# Homestead release canary

The canary runs the release wheel against a separate QMD cache, config, state,
and collection. It reads the canonical vault but never mutates the production
collection. Promote only after the external 24-hour health gate passes. Keep
`HSM_CANARY_*` paths outside production paths.

During promotion, the deployment runner stops the refresh timer and keeps the
previous release directory. Rollback is a symlink switch, then a QMD restart and
the doctor, refresh, and retrieval checks.

## Enable the canary

The canary is optional. The release workflow skips it, and stays green, when the
deploy secrets are absent. To turn it on, add these repository secrets under
Settings > Secrets and variables > Actions:

- `HOMESTEAD_DEPLOY_HOST` — the canary host.
- `HOMESTEAD_DEPLOY_USER` — a restricted deploy user.
- `HOMESTEAD_DEPLOY_SSH_KEY` — the private key for that user.
- `HOMESTEAD_DEPLOY_KNOWN_HOSTS` — the pinned host key.

The workflow reads these secrets at run time. It never stores host details in the
repository. Provide an adapter that runs `deploy.sh` and `health_gate.sh`, and
keeps rollback evidence.
