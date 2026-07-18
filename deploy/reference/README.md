# Homestead reference deployment

This directory contains an optional systemd-user deployment for Linux hosts.
It is an operational reference, not part of the Python package and never
installs itself automatically.

Set `HSM_VAULT` in the service environment, copy the units into
`~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now homestead-qmd.service homestead-refresh.timer
```

The refresh service uses Homestead's explicit QMD cache/config/state paths,
keeps the vault read-only, and records checkpoint/freshness state beneath the
vault's `.hsm` directory. It does not touch a user's global QMD index.
