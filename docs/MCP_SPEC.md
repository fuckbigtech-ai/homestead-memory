# `hsm mcp` — spec v1 (MCP server)

## Why

Distribution parity: MemPalace ships 19 MCP tools, Cognee ships an MCP server —
MCP is how Claude Code / Claude Desktop / Cursor users adopt a memory layer with one
config line. homestead-memory already has the hardened HTTP API; MCP is a second,
stdio-based skin over the same core ops.

## Design constraints

- **Stdlib-only** (the zero-dependency claim holds): a minimal JSON-RPC 2.0 server
  over stdio implementing the MCP subset that tool use requires — `initialize`,
  `notifications/initialized`, `tools/list`, `tools/call`, `ping`. No `mcp` SDK.
- **Local trust model:** MCP stdio servers are launched by the client as a child
  process — no network surface, so no Host/token gating needed (unlike `hsm serve`).
  The vault root comes from `--vault` / `$HSM_VAULT` at launch; tools cannot escape it.
- **Reuse, don't duplicate:** tools call the same `core` functions the CLI/HTTP API
  use (`index.ask/search/ingest`, `verify.verify_vault`, `temporal.history/as_of`,
  `distill.distill`).

## Tools (6)

| tool | args | returns (text content) |
|---|---|---|
| `memory_ask` | `query` (req), `k` | answer if a reader is configured, else ranked passages |
| `memory_search` | `query` (req), `k` | ranked passages (title, rel, score, snippet) |
| `memory_verify` | `deep` (bool) | the integrity report: score/100, fails, warns — ROT or INTACT |
| `memory_history` | `note` (req), `as_of` | a note's recorded change history |
| `memory_ingest` | — | index + temporal build report |
| `memory_distill` | `dry` (bool) | distill pass report (facts kept/dropped, entities, changelog lines) |

Every tool returns MCP `content: [{type:"text", text:…}]`; errors return
`isError: true` with a plain message (never a crash — one bad call must not kill
the server loop).

## Protocol shape (minimum viable, per MCP 2024-11-05)

- `initialize` → `{protocolVersion, capabilities:{tools:{}}, serverInfo:{name,version}}`
- `notifications/initialized` → no response (notification)
- `tools/list` → `{tools:[{name, description, inputSchema(JSON Schema)}...]}`
- `tools/call` → `{content:[...], isError?}`
- `ping` → `{}`
- Unknown method → JSON-RPC error `-32601`; malformed JSON → skip the line (log to
  stderr); requests are answered in order (single-threaded loop is fine for v1).
- Messages are newline-delimited JSON on stdio (the transport Claude Code uses).

## CLI + client config

`hsm mcp [path]` (vault = arg / `$HSM_VAULT` / cwd). Claude Code registration:

```bash
claude mcp add homestead-memory -- hsm mcp ~/my-vault
```

## Failure modes to design against

- A tool exception must return `isError`, not kill the loop.
- stdout is PROTOCOL-ONLY: any diagnostic goes to stderr (a stray print corrupts
  the JSON-RPC stream — the classic stdio-MCP bug).
- Large results truncated (~50k chars) with a note, so a huge vault report can't
  blow the client's context.
- `distill`/`ingest` can be slow → the server stays single-threaded v1 (clients
  timeout gracefully); document that ingest on a big vault takes time.

## Non-goals (v1)

Resources/prompts capabilities · streaming/progress · concurrency · auth (stdio is
client-spawned) · HTTP/SSE transport (that's `hsm serve`).

## v1.1 protocol addenda (from spec review — implementation contract)

- Full JSON-RPC 2.0 envelopes; request `id` preserved EXACTLY (type included).
- Notifications (no `id`) NEVER get a response — including unknown ones and
  `notifications/cancelled` (swallowed). Unknown *requests* → `-32601`.
- Parse error → `-32700` with `id: null`; structurally invalid message with an id →
  `-32600`.
- Lifecycle gating: before `initialize`, only `initialize`/`ping` are served (others
  → `-32002` "server not initialized"); `notifications/initialized` flips ready state.
- Version negotiation: respond with OUR `protocolVersion` (2024-11-05); client decides.
- Transport: one JSON object per line, no embedded raw newlines, flush after every
  write, stdout PROTOCOL-ONLY (diagnostics → stderr). No LSP Content-Length framing.
- Tool schemas: concrete JSON Schema (`type:object`, `properties`, `required`,
  `additionalProperties:false`), `k` clamped 1–20 (default 5).
- `tools/list` ignores `cursor`, omits `nextCursor`.
- Mutating tools say so in their descriptions (`memory_ingest` rebuilds the index;
  `memory_distill` writes distilled notes; `dry` defaults false, explicit).
- Verify/history/search results are flattened to plain text (never raw dataclasses);
  tool exceptions → `isError:true` content, never a crashed loop.
- Robustness: stdin EOF → clean exit 0; BrokenPipe/SIGINT → quiet exit; MCP handlers
  call CORE functions only (CLI functions print to stdout and would corrupt the stream).
