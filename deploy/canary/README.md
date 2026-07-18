# Homestead release canary

The canary runs the release wheel against a separate QMD cache, config, state,
and collection. It reads the canonical vault but never mutates the production
collection. Promotion is allowed only after the external 24-hour health gate
passes. Keep `HSM_CANARY_*` paths outside production paths.

The deployment runner must stop the refresh timer during promotion, retain the
previous release directory, and make rollback a symlink switch followed by
QMD restart and doctor/refresh/retrieval checks.
