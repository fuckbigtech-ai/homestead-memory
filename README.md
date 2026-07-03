# homestead-memory

**Stop renting your mind.**

Verifiable, local-first AI memory. Your notes stay plain markdown you can read, `git diff`, and own — and `homestead-memory` **proves the memory never rotted.**

Every other memory layer asks you to *hope* it remembers. This one lets you *watch it prove it didn't rot.*

```bash
pip install homestead-memory        # (soon) cross-platform: macOS / Linux / Windows

hsm init      ./my-vault      # scaffold or adopt a markdown folder
hsm ingest    ./my-vault      # index it (hybrid BM25 + vector via qmd)
hsm ask       "what did I decide about X?"
hsm verify    ./my-vault      # <-- the point: score memory integrity /100, nonzero exit on rot
hsm verify --demo             # plant a contradiction and watch the gate catch it
```

## Why this exists

Local, on-device AI memory is already commoditized — everyone stores your data on your machine now. Nobody **verifies** it. Memory silently rots: a note's body drifts from its own changelog, a "current" fact gets shadowed by a stale copy, an embedding goes bad, a source disappears. You never find out until your agent confidently tells you something that was true six weeks ago.

`hsm verify` is a weighted memory-integrity gate. It exits non-zero when your memory can't prove it surfaces the *current* truth over the stale one. That number — **RotBench** — is something no other memory tool publishes.

## Design

- **Markdown-primary, graph-derived.** The human-readable markdown is the source of truth; indexes/temporal projections are derived and disposable.
- **Cross-platform.** Pure Python. Runs identically on macOS, Linux, and Windows.
- **qmd is an optional dependency, not vendored.** Hybrid retrieval when it's installed; a direct markdown scan when it isn't. Memory survives the index being down.
- **Local by default; you own the burst.** Route inference local → your own rented GPU → a zero-data-retention provider. Nothing is hard-wired to one vendor.

## Status

v0.0.1 — early. Building in the open. See `ROADMAP` / the issues.

MIT © Kinetic Labs Inc. Part of the [FuckBigTech](https://fuckbigtech.ai) / Homestead local-AI line.
