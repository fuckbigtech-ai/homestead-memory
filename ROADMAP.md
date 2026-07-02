# Roadmap

Building in public. **Stop renting your mind.**

## v0.1 — the core (in progress, most shipped)

- [x] Package + `fbt` CLI (cross-platform Python)
- [x] `core/vault.py` — markdown vault model (frontmatter, wikilinks, recency), de-personalized
- [x] **`fbt verify` + `--demo`** — memory-integrity gate; catches rot live, nonzero exit
- [x] `core/index.py` — qmd hybrid retrieval (+ direct-scan fallback) → `fbt ingest` / `ask`
- [x] `core/temporal.py` — bi-temporal from changelogs → `fbt history` / `--as-of`
- [x] `.fbtignore` exclusion parity (quarantine generated/report notes)
- [x] LongMemEval harness (real `--data` + synthetic validation) + RotBench
- [ ] Full LongMemEval-S (500Q) published number — A vs B + delta *(the launch proof)*
- [ ] `verify` full-fidelity: qmd-freshness + forced-fallback + fixtures checks
- [ ] `api/server.py` — local HTTP API + the `oc-route` router (builder surface)
- [ ] README hero GIF: "watch it catch its own rot"

## Then (per the FuckBigTech plan)

- **Publish** the number → GitHub / Show HN / r/LocalLLaMA (dev-first).
- **Weekly cadence:** one ship + one newsjack + one rot/benchmark post.
- **HOMESTEAD** consumer app adopts the verify moat (a "MEMORY INTACT" badge).
- **Enterprise** (inbound only, billed via Kinetic Labs): the OS-agnostic API/router
  is the wedge Osaurus (Mac-Swift-only) structurally can't follow.

## Non-goals (for now)

Heavy temporal graph DB · a second marketing motion · enterprise hardening before
dev traction. Stay focused: the number, then the launch.
