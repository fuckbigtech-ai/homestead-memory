# Roadmap

## v0.3 and v0.4: the agent ledger (shipped)

Added 2026-09-02. This file described a product two minor versions behind, and did not
mention the ledger at all, which is now the lead feature. Items below are shipped and
tagged. Forward-looking items after v0.4 are Humza's to set, so nothing is invented here.

- [x] `core/ledger.py`: hash-chained append-only record of agent actions, with an
      Ed25519 checkpoint over the head
- [x] `core/capture.py` + `hsm hook`: PreToolUse and PostToolUse capture for Claude Code.
      Secret-shaped values are redacted and payloads are truncated
- [x] `hsm watch`: read the ledger, report chain breaks, drops and checkpoint coverage
- [x] `hsm checkpoint` and `--export`: sign the head, and publish one line somewhere the
      local attacker does not control. `--verify` checks a ledger against that line
- [x] `hsm export --evidence`: EvidencePack with a bundled standard-library verifier, so
      a recipient needs nothing installed
- [x] `core/jcs.py`: RFC 8785 canonicalization, tested against the published vectors
- [x] RotBench v1.5: ledger checks moved into the base pass

## v0.2.3: freshness and reproducible deployment

- [x] Incremental QMD refresh with atomic checkpoint and source fingerprint.
- [x] Runtime ownership checks, watchdog heartbeat, and zero-pending gate.
- [x] Explicit XDG/QMD path isolation and Linux reference units.
- [ ] Cross-platform failure-injection matrix and public retrieval benchmark.
- [ ] Publish the package and reference deployment from the same tagged source.

Building in public. **Stop renting your mind.**

## v0.1: the core (shipped)

- [x] Package + `hsm` CLI (cross-platform Python, zero deps)
- [x] `core/vault.py`: markdown vault model (frontmatter, wikilinks, recency)
- [x] **`hsm verify` + `--demo`**: memory-integrity gate; catches rot live, nonzero exit
- [x] `core/index.py`: qmd hybrid retrieval (+ direct-scan fallback) → `hsm ingest` / `ask`
- [x] `core/temporal.py`: bi-temporal from changelogs → `hsm history` / `--as-of`
- [x] `.hsmignore` exclusion parity (quarantine generated/report notes)
- [x] `verify --deep`: fallback-resilience + fixtures + freshness families
- [x] `api/server.py`: local HTTP API (`hsm serve`), hardened (auth, anti-rebind, loopback)
- [x] LongMemEval harness + official-methodology eval + RotBench
- [x] Full-500 `_s` published numbers (see `benchmarks/RESULTS.md`)

## v0.2: the distilled layer + distribution (in progress)

- [x] **`hsm distill`**: write-time, cited, verifiable distilled layer (docs/DISTILL_SPEC.md)
- [x] `verify` distill_integrity: uncited_claim + dangling_citation (auditable extraction)
- [ ] `hsm mcp`: MCP server for Claude Code / Desktop / Cursor (docs/MCP_SPEC.md)
- [ ] Parent-document retrieval (chunk-index → parent-session reading)
- [ ] 3-OS CI (ubuntu/macos/windows): the cross-platform proof
- [ ] `benchmarks/ROTBENCH.md`: the integrity score as an open spec
- [ ] Honest full-500 re-run with the distilled layer (publish whatever it says)
- [ ] README hero GIF: "watch it catch its own rot"

## Then

- **Launch**: GitHub (fuckbigtech org) → r/LocalLLaMA → Show HN → X. Weekly cadence:
  1 ship + 1 auto Rot Report + 1 newsjack.
- **HOMESTEAD app**: "MEMORY INTACT" badge via the core's verify.
- **Enterprise** (inbound, via Kinetic Labs): the hardened API + MCP.
- **Local compounding loop**: memory that gets better the more you use it. Distill + feedback
  runs on-device, privacy-preserving, no telemetry ever leaves the machine. (The frontier-lab
  move is "your usage telemetry post-trains our model." Ours is the same compounding, kept
  local-first, so the improvement stays yours.)

## Non-goals (for now)

Graph databases · unauditable extraction · benchmark-gaming · a second marketing motion.
