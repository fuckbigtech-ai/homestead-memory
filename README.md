# homestead-memory

**Stop renting your mind.**

Local-first, verifiable AI memory. Your notes stay plain markdown you can read,
`git diff`, and own — and the memory **proves it hasn't rotted.**

Every other memory layer asks you to *hope* it remembers. This one lets you
*watch it prove it didn't rot:*

![hsm verify --demo: a clean vault scores MEMORY INTACT 100/100, then rot is planted and caught live: ROT DETECTED 0/100 with every finding named](docs/demo.gif)

```bash
pip install homestead-memory      # macOS / Linux / Windows (pure Python, zero deps)

hsm verify --demo
# ① a clean vault           ✅  MEMORY INTACT — 100/100
# ② rot is planted…         🔴  ROT DETECTED —   0/100
#    🔴 [self_contradiction] the note argues with itself about its own status
#    🔴 [uncited_claim]      a distilled claim has no source citation
#    🔴 [dangling_citation]  a cited source no longer exists
#    ⚠️  [broken_link]        a reference points at a deleted note
```

`hsm verify` exits non-zero on rot — it gates CI and cron like a test suite.

## Quickstart (60 seconds)

```bash
hsm init   ./my-vault          # scaffold or adopt any markdown folder
hsm ingest ./my-vault          # index it (hybrid BM25+vector via qmd, optional)
hsm ask    "what did I decide about X?"
hsm verify ./my-vault          # the integrity gate — the whole point
hsm distill ./my-vault         # optional: build the cited, verifiable fact layer
hsm history <note> --as-of 2026-06-01   # what was true THEN (temporal layer)
hsm serve                      # local HTTP API (auth'd, loopback-only)
```

Python agents can use the SDK directly:

```python
from homestead_memory import connect

memory = connect("~/my-vault", agent="my-agent")
memory.remember("user", "city", "Berlin")
memory.ask("what city is the user in?")
```

The local HTTP API is documented in [`docs/openapi.yaml`](docs/openapi.yaml).

## Memory under the router

Routers can swap the served model while homestead-memory keeps the same vault
underneath. The model name is just the runtime argument; provenance is stamped as
`name@model` in the `agent` field when a write happens.

```python
from homestead_memory import connect
from homestead_memory.adapters.openai_compat import MemoryChat

memory = connect("~/my-vault")

def remember_reply(response, memory, agent):
    memory.remember(
        "conversation",
        "last_reply",
        response.choices[0].message.content,
        source="chat",
        agent=agent,
    )

chat = MemoryChat(openai_compatible_client, memory, remember_fn=remember_reply)
chat.create(model="claude-sonnet-4.7", messages=[{"role": "user", "content": "brief me"}])
chat.create(model="glm-4.7", messages=[{"role": "user", "content": "continue"}])

memory.history("conversation")  # agents include assistant@claude-sonnet-4.7 and assistant@glm-4.7
```

LiteLLM can use the same pattern with a pre-call injection helper and a success
logger:

```python
from homestead_memory import connect
from homestead_memory.adapters.litellm_memory import MemoryLogger, inject_memory

memory = connect("~/my-vault")
messages = inject_memory([{"role": "user", "content": "brief me"}], memory)

# LiteLLM callback registration style depends on your app setup.
logger = MemoryLogger(memory, agent_name="assistant")
```

MCP already sits above harness-level routers. In a `claude-code-router`-style
setup that swaps the backend model, homestead-memory keeps working with
zero config because memory is external to the model. `history()` and `verify`
then attribute every recorded fact to the exact `name@model` that wrote it.

## Integrations

Adapters target the public framework interfaces listed here as of the current
releases and may need version bumps as those APIs evolve. Core remains
stdlib-only; install only the extra for the framework you use.

Universal tools work with any orchestrator that can register callables or
JSON-schema function tools:

```python
from homestead_memory import connect
from homestead_memory.adapters.tools import recall_tool, remember_tool, tool_specs, verify_tool

memory = connect("~/my-vault", agent="my-agent")
tools = [remember_tool(memory), recall_tool(memory), verify_tool(memory)]
specs = tool_specs(memory)  # name, description, parameters
```

LangGraph `BaseStore` (targets `langgraph>=0.2`):

```python
from homestead_memory import connect
from homestead_memory.adapters.langgraph_store import HomesteadStore

store = HomesteadStore(connect("~/my-vault", agent="langgraph"))
graph = builder.compile(checkpointer=checkpointer, store=store)
```

CrewAI storage/memory (targets `crewai>=0.70`, storage-style
`save/search/reset`):

```python
from homestead_memory import connect
from homestead_memory.adapters.crewai_memory import HomesteadCrewAIStorage

storage = HomesteadCrewAIStorage(connect("~/my-vault", agent="crewai"))
storage.save("Researcher found the supplier shortlist", metadata={"task": "supplier_shortlist"})
```

AutoGen `autogen_core` Memory protocol (targets `autogen-core>=0.4`):

```python
from autogen_core.memory import MemoryContent, MemoryMimeType
from homestead_memory import connect
from homestead_memory.adapters.autogen_memory import HomesteadAutoGenMemory

memory = HomesteadAutoGenMemory(connect("~/my-vault", agent="autogen"))
await memory.add(MemoryContent(content="Use metric units", mime_type=MemoryMimeType.TEXT))
```

OpenAI Agents SDK Session protocol or function tools (targets
`openai-agents>=0.0.1`):

```python
from homestead_memory import connect
from homestead_memory.adapters.openai_agents import HomesteadSession, function_tools

memory = connect("~/my-vault", agent="openai-agents")
session = HomesteadSession(memory, session_id="user-123")
agent_tools = function_tools(memory)
```

**Claude Code / Desktop / Cursor** (MCP):

```bash
claude mcp add homestead-memory -- hsm mcp ~/my-vault
# tools: memory_ask · memory_search · memory_verify · memory_history ·
#        memory_ingest · memory_distill
```

## Why this exists

"Runs on your device" is table stakes now — every memory tool stores locally.
**Nobody verifies.** Memory rots quietly: a note contradicts itself, an extracted
"fact" loses its source, a body drifts past its own changelog, the current value
gets shadowed by a stale one. You find out weeks later, when your agent confidently
tells you something that stopped being true in March.

And rot is only the *passive* failure. Memory also gets **tampered** with (a fact
edited after it was written) and **poisoned** (untrusted input injects a "memory"
that was never true — a named 2026 attack class). homestead-memory catches all
three mechanically: sign the vault and any edited byte breaks the signature; a
distilled claim must cite a source that resolves or it's dropped. Recall benchmarks
measure whether the model *remembers*; **RotBench measures whether the memory can be
trusted** — see [`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md).

homestead-memory is built around three commitments:

1. **Markdown-primary.** The human-readable files ARE the memory. Indexes and
   projections are derived and disposable. You can leave any time — it's your folder.
   Import/export Google's **Open Knowledge Format** (`hsm export --format okf`) plus
   Mem0/Zep: we're OKF, but signed and verifiable.
2. **Verification over trust.** Integrity is a *number* (RotBench, 0–100), computed
   by mechanical checks — no LLM judging its own homework. See
   [`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md).
3. **Auditable extraction.** The optional distilled layer ([`docs/DISTILL_SPEC.md`](docs/DISTILL_SPEC.md))
   extracts entity facts *with verbatim quotes, checked in code* — a claim either
   cites a real source or it's dropped. Contradictions append a changelog line
   (`update current_crm: "Salesforce" -> "HubSpot" (source: chat-042.md)`) — never a
   silent overwrite. Extraction you can audit is extraction you can trust.

## The two camps (where this sits)

| | extraction camp (Mem0, Zep) | verbatim camp (MemPalace, **this**) |
|---|---|---|
| write cost | LLM call per turn/episode | **$0** (embed only; distill optional) |
| information | lossy summaries | **lossless** raw text |
| auditability | trust the extractor | **cite-or-drop, checked mechanically** |
| integrity score | — | **RotBench, published every run** |

## Honest numbers (LongMemEval)

Measured on the full 500-question `_s` set (48-session haystacks with distractors),
scored with the **official per-type judge methodology**, reader `glm-5.2`,
independent judge `deepseek-v4-pro`. Reproduce: [`benchmarks/README.md`](benchmarks/README.md).
Full run history including the failures: [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md).

| metric | value |
|---|---|
| retrieval recall@k | **85%** (evidence surfaced into top-k) |
| QA accuracy (official methodology) | **52.8%** |
| context tokens / query | **~5.2k** |
| RotBench | **99.4 / 100** |

What we will and won't claim: recall is elite and *reader-independent*; QA is honest
and mid — published systems self-report higher on their own harnesses (Mem0 94.4%,
Zep 63.8% independent); we publish the harness, the judge, and every failed
experiment instead. No number here is from a harness you can't run yourself.

## Design

- **Cross-platform.** Pure Python, stdlib-only core. CI: ubuntu / macos / windows.
- **Degrades gracefully.** qmd (hybrid retrieval) is an optional dependency; without
  it, retrieval falls back to a direct scan. Memory survives its index being down —
  `verify --deep` *tests* that.
- **Local by default.** The HTTP API binds loopback with bearer auth + DNS-rebind
  protection; the MCP server is stdio (client-spawned). Nothing phones home.
- **Temporal.** Changelog lines make history queryable: `hsm history note --as-of DATE`.

## Status

v0.2, building in public. Roadmap: [`ROADMAP.md`](ROADMAP.md). Break our benchmark:
[`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md) — adversarial fixtures get merged.

MIT © Kinetic Labs Inc. · a [FuckBigTech](https://fuckbigtech.ai) / HOMESTEAD project.
