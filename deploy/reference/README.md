# Homestead reference deployment

This directory holds an optional systemd-user deployment for Linux hosts — an
operational reference, not part of the Python package, that never installs
itself automatically.

Set `HSM_VAULT` in the service environment, copy the units into
`~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now homestead-qmd.service homestead-refresh.timer
```

The refresh service uses Homestead's explicit QMD cache/config/state paths,
keeps the vault read-only, and records checkpoint/freshness state beneath the
configured writable `HSM_REFRESH_STATE_DIR` (or the normal writable vault
`.hsm` directory when no override applies). It does not touch a user's
global QMD index.
