# Homestead release canary

The canary runs the release wheel against a separate QMD cache, config, state,
and collection. It reads the canonical vault but never mutates the production
collection. Promote only after the external 24-hour health gate passes. Keep
`HSM_CANARY_*` paths outside production paths.

During promotion, the deployment runner stops the refresh timer and keeps the
previous release directory. Rollback is a symlink switch, then a QMD restart and
the doctor, refresh, and retrieval checks.
