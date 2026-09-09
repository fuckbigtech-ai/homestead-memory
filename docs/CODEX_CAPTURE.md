# Codex capture: investigated and BUILT 2026-09-09

## Verdict

**Built, as an import rather than a hook.** `hsm import-session --harness codex` reads
Codex's rollout files and writes ledger records, each marked `meta.source=codex-rollout`.

## This document reversed itself twice. Both reversals are kept.

**First position: build it, Codex has the same hooks as Claude Code.** Wrong. That came
from reading a third-party `~/.codex/hooks.json` and grepping strings out of the binary.
Neither is a protocol contract.

**Second position: do not build it, the hooks are shell-only so a Codex ledger would omit
every file read and edit.** Also wrong, and wrong in a more interesting way. That reasoned
from CLAUDE CODE's tool model, where Read, Edit and Write are separate tools and losing
them is severe. **Codex has no such tools.** Measured across 60 real sessions and 848 tool
calls on this machine:

| tool | calls |
|---|---:|
| `exec` | **799 (94%)** |
| `send_message` | 20 |
| `request_user_input` | 13 |
| everything else | 16 |

Codex does its file work by shelling out. Shell coverage is near-total coverage FOR CODEX.
The migration doc's "shell commands only" warning is addressed to people PORTING Claude
Code hooks, not a claim that Codex agents act outside the shell.

**Third position, and the one that shipped: import the rollouts.** Codex's PreToolUse and
PostToolUse did not fire at all under `codex exec` when probed directly, matcher or no
matcher. Its rollout files, however, record every call with input, output and a pairing
`call_id`, which is strictly more than the hooks would have given.

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

## What an imported record cannot claim, and how that is enforced

A hook observes an action as it happens. An import reads a file the harness wrote
afterwards, so it inherits whatever that file says and cannot prove the record is
contemporaneous.

That difference is carried in the data, not just in documentation:

- every imported row sets `meta.source = "codex-rollout"` and `meta.harness = "codex"`
- `hsm watch` prints **(imported)** on those rows, so the distinction survives into the
  output a person actually reads
- an output whose call is missing is recorded with `meta.orphan = true` rather than
  dropped, because a silent gap is the failure this project exists to catch

## Usage

    hsm import-session --dry-run          # newest Codex session, nothing written
    hsm import-session -n 5               # the five most recent
    hsm import-session --session <path>   # one specific rollout

Named `import-session` because `hsm import` already exists and brings MEMORIES into the
vault from Mem0, Zep and OKF. The first draft of this feature was also called `cmd_import`
and would have shadowed that function silently, since it is defined later in the module. A
test now pins both commands to their own handlers.

## Method note

The payload contract was verified before any code was written, by backing up
`~/.codex/hooks.json`, appending a stdin-dumping probe alongside the existing hooks, running
a real Codex task, and restoring the original (hash-verified). No capture code was written
against an assumed field shape. The first version of the Claude Code hook shipped broken
because it read `CLAUDE_TOOL_NAME` environment variables that the harness never sends, and
that failure is the reason this check happens first now.
