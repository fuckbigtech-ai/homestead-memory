# Codex capture: investigated 2026-09-09, NOT built, and why

## Verdict

**Do not build Codex capture in the shape homestead-memory uses for Claude Code.** Codex
fires tool hooks for **shell commands only**. A ledger that silently omits every file read,
edit and write is not a smaller version of this product. It is a misleading one, and the
whole claim is that the record is complete and that omissions are visible.

This document exists because the investigation reversed the plan. Recording that is worth
more than a build that would have understated what an agent did.

## What prompted it

The README's biggest disclosed limitation is "Capture is Claude Code only". Removing it
would roughly double the addressable audience, so Codex looked like the highest-value
post-launch feature.

An initial read of a local `~/.codex/hooks.json` showed `PreToolUse`, `PostToolUse` and
`PermissionRequest`, and the Codex binary (0.153.4) contains all three strings. On that
evidence the conclusion was "Codex has the same event shape as Claude Code, and
PermissionRequest even gives us the enforcement moment Claude Code lacks."

**Both halves of that were wrong.** A third-party tool's config file is not a protocol
contract, and a string in a binary is not a guarantee of when it fires.

## What Codex actually does

From Codex's own migration reference, on disk at
`~/.codex/vendor_imports/skills/skills/.curated/migrate-to-codex/references/differences.md`:

- **`PreToolUse`**: "Codex currently runs PreToolUse for **shell commands only**."
- **`PostToolUse`**: "Codex currently runs PostToolUse for **shell commands only**"; and
  "only Bash is matched for `PostToolUse`."
- **`PermissionRequest`**: listed under "No direct equivalent / **Unsupported**". Codex does
  not expose it, despite a third-party config on this machine registering a hook for it.
- Hooks sit behind a feature flag: `[features] hooks = true` in `~/.codex/config.toml`.
- `matcher` is regex and applies to `PreToolUse`, `PostToolUse` and `SessionStart` only.

Empirically, a `codex exec` run that definitely shelled out (`wc -c probe.txt`, output
`21 probe.txt`) fired `Stop` but produced nothing on a `PreToolUse` or `PostToolUse` probe,
so non-interactive mode may narrow coverage further. That was not chased to a root cause,
because the documented shell-only limit already settles the decision.

## Why shell-only breaks the product claim

On Claude Code the hook installs with matcher `"*"` and records every tool call: Bash, Read,
Edit, Write, WebFetch. On Codex the same design would record Bash and nothing else.

A coding agent spends most of its calls reading and editing files. A ledger of that session
would show a handful of shell commands and none of the edits, while presenting itself as
"what your agent actually did". The README already refuses weaker versions of this claim:

> Anything that never reached the ledger at all, such as a hook you did not install.

Shipping a Codex mode that omits the majority of actions **by design** would turn that
caveat into the normal case, on a product whose differentiator is that omissions are
detectable rather than silent.

## What would change the verdict

1. Codex extending `PreToolUse` / `PostToolUse` beyond shell commands to file operations.
2. Or a different capture point for Codex entirely: its session rollout files under
   `~/.codex/sessions/**/rollout-*.jsonl` record the full turn history. That is a
   READ-AFTER-THE-FACT source, not a hook, so it cannot record the decision phase and it
   inherits whatever the file says. It would need its own honest framing, closer to
   "import a Codex session" than "capture what the agent did".

Option 2 is the realistic path and it is a different feature, not a port.

## If it is built anyway

It must be labelled at every surface as **"Codex: shell commands only"**, in the README, in
`hsm hook --install` output, and in the ledger records themselves via a coverage field. A
user must not be able to acquire a Codex ledger without knowing what is missing from it.

## Method note

The payload contract was verified before any code was written, by backing up
`~/.codex/hooks.json`, appending a stdin-dumping probe alongside the existing hooks, running
a real Codex task, and restoring the original (hash-verified). No capture code was written
against an assumed field shape. The first version of the Claude Code hook shipped broken
because it read `CLAUDE_TOOL_NAME` environment variables that the harness never sends, and
that failure is the reason this check happens first now.
