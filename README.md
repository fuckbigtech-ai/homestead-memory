# homestead-memory

<!-- mcp-name: io.github.fuckbigtech-ai/homestead-memory -->

[![PyPI](https://img.shields.io/pypi/v/homestead-memory)](https://pypi.org/project/homestead-memory/)
[![CI](https://github.com/fuckbigtech-ai/homestead-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/fuckbigtech-ai/homestead-memory/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/homestead-memory)](https://pypi.org/project/homestead-memory/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Stop renting your mind.**

**A tamper-evident record of what your AI agent actually did**, in a local file, that
someone who does not trust you can verify.
Tamper-**evident**, not tamper-proof:
[what it defends against and what it does not](#what-this-defends-against-and-what-it-does-not).

Agents fail quietly. The run reports success, the tool returns 200, and the thing you
asked for did not happen. There is usually no record to contradict it. This keeps one,
and every entry is hash-chained to the one before it, so *see it catch a forged record,
live:*

<!-- Absolute URL, not `docs/demo-watch.gif`. This README is also the PyPI long_description,
     and PyPI cannot resolve repo-relative paths: it rendered a broken-image icon where the
     demo should be. GitHub resolves the absolute form fine, so one URL serves both. -->
![hsm watch --demo: three tool calls are recorded in order, then one record is edited in place and the chain reports a break at the exact index, exiting nonzero](https://raw.githubusercontent.com/fuckbigtech-ai/homestead-memory/master/docs/demo-watch.gif)

```bash
pip install homestead-memory      # Python 3.10+, macOS / Linux / Windows

hsm watch --demo
#      0  14:02:11  Bash    npm test
#      1  14:02:19  Read    src/api/billing.py
#      2  14:02:24  Edit    src/api/billing.py
#
# ② now someone edits record 1…
#   !! chain break at index 1: hash_mismatch
#      record content does not match its own hash (edited in place)
#   exit 1  ·  gate it in CI like a test
```

> **Got `No matching distribution found`?** macOS still ships Python 3.9 as its built-in
> `python3`, and this needs 3.10+. Nothing is wrong with the package. Either use a newer
> Python, or skip installing entirely:
> `uvx --from homestead-memory hsm watch --demo`
> ([uv](https://docs.astral.sh/uv/) fetches a suitable Python for you.)

## Record your own agent

```bash
hsm hook --install     # prints a Claude Code hook; you paste it, nothing is edited for you
hsm watch              # what your agent did, in order
```

Every entry is hash-chained to the one before it, so editing, deleting, or reordering
any record breaks every hash after it. `hsm watch` reports the break at the exact index
and exits non-zero. Sign it and a wholly rebuilt chain is caught too.

**Both phases are recorded, which is what makes it evidence rather than a log.** The hook
captures the decision before a tool runs and the outcome after. A record of outcomes alone
shows what happened; it cannot show that anything was authorised first. As
[the IETF Agent Audit Trail draft](https://datatracker.ietf.org/doc/draft-sharif-agent-audit-trail/)
puts it, "a denial that is only logged after execution provides no evidence that the
denial was enforced".

```bash
hsm export --format aat     # the same ledger as draft-sharif-agent-audit-trail-01
```

That draft maps explicitly to EU AI Act Article 12, and this emits it **alongside** the
native format rather than replacing it, so nothing already on disk changes. Where we
differ we say so: it specifies ECDSA P-256, which the AAT export uses, while the native
EvidencePack stays Ed25519 so its verifier can remain standard-library only and a
recipient needs nothing installed.

**Know this before you rely on the AAT export.** That draft is an individual
Internet-Draft, not an adopted standard: it has no working group behind it and carries the
usual notice that it "is not endorsed by the IETF". Its author has also filed
[IPR disclosure 7558](https://datatracker.ietf.org/ipr/7558/), covering revisions 00 and 01
in full, declaring "Reasonable and Non-Discriminatory License to All Implementers with
Possible Royalty/Fee". RAND with a possible fee is not royalty-free, and this package is
MIT, which grants you no patent licence. We ship the export because interoperability is
worth having and we have asked the author to clarify what implementers are taking on.
Until there is an answer, treat `--format aat` as a convenience rather than something to
build a compliance programme on. The native ledger and EvidencePack carry no such
disclosure, and they are what the rest of this README argues for.

**What it costs you: about 124ms per tool call** (median; 138ms p95, measured on an M3 Pro
with a 4KB tool response). Since 0.4.0 the hook records both phases, so it runs as a fresh
process **twice** per call, once before your tool runs and once after: about 61ms and 63ms
respectively. On a 100-call session that is roughly 12 seconds spread across the run.
Almost all of it is Python interpreter and import startup rather than the recording itself.
If that is too much for your loop, do not install the hook: the number is here so you can
decide before you find out.

Earlier releases documented 68ms, which was correct when only `PostToolUse` was recorded.
Recording the decision phase is what makes the ledger evidence of enforcement rather than
of observation, and it costs a second process. Stating the higher number rather than the
one that flatters us is the same reason the rest of these figures are here.

**This is a file, not a platform.** Agent observability tools are far richer than this
and they want a deployment: the self-hosted ones document a production floor of several
services and roughly 16 GB of RAM. This is `pip install`, one hook line, and a JSONL
file on your disk. Different job. If you need dashboards, evals, and span analytics,
use one of those. If you want a record you can grep and prove, use this.

Secret-shaped values are redacted and payloads truncated to a 200-character head, with
a SHA-256 of the full original kept so the evidence survives redaction. That is a
mitigation, not a guarantee: no pattern list is complete.

```bash
hsm export --evidence  # a pack anyone can verify with no install at all
```

The pack carries the records, the signature, the public key, an integrity report, and a
standard-library verifier a third party can read in full and run. It states what it does
NOT prove, including that a signature only establishes origin if you already know which
key to expect.

## What this defends against, and what it does not

Tamper-**evident**, not tamper-proof. The difference matters, so here it is plainly.

**It catches:**

- A record edited in place. The hash stops matching and `hsm watch` names the index.
- A record deleted or reordered. Every hash after it breaks.
- A silently dropped write. Drops are recorded and reported, never swallowed.
- A whole chain rebuilt from scratch by someone who recomputed every hash, **provided you
  ran `hsm checkpoint`, you pin the expected signer, and they do not hold your key**. All
  three matter. A checkpoint verified without a pinned key is self-asserted: it checks the
  signature against whichever key sits beside it, so a rebuilt chain re-signed with the
  attacker's own key also passes. `hsm verify --deep` now says so when no key is pinned.

  ```bash
  hsm verify --deep --signer <your pubkey>   # or the checkpoint is self-asserted
  ```
- Anything altered after you handed someone an EvidencePack, which they can check with no
  install and nothing from us.

**A checkpoint covers its prefix, not the future.** Records appended after your last
checkpoint can be replaced with a correctly re-chained forgery and still verify, because a
signature cannot cover records that did not exist when it was made. `hsm verify` tells you
how many records are uncovered. Re-checkpoint often, or that tail grows.

**It does not catch:**

- **An attacker who holds your signing key.** The key lives at
  `~/.config/homestead-memory/ed25519_key`, on the same machine as the ledger. Anyone who
  can rewrite the file can usually read that key, re-sign the rewrite, and pass. If that is
  your threat model, run `hsm checkpoint --export`, publish the line somewhere they do not
  control, and check the ledger against it later with `hsm checkpoint --verify`. A head
  hash reveals nothing about the records, so publishing one leaks nothing.
- **A tool that lies.** See below.
- Anything that never reached the ledger at all, such as a hook you did not install.

```bash
hsm checkpoint            # sign the current head
hsm checkpoint --export   # one line to publish where an attacker cannot reach
hsm checkpoint --verify   # check this ledger against a line you published earlier
```

`hsm watch` reports how much of the ledger the checkpoint covers, and fails if the
checkpoint no longer verifies. Everything appended since your last checkpoint is uncovered,
so re-checkpoint often.

## It records what your harness reported

The hook receives what the harness says a tool did. It is not independent observation.

If a tool reports success while doing nothing, that is precisely the failure this exists to
catch, because you get a durable record of the claim and can compare it against reality.
But if a tool reports something false about *what* it did, the ledger will faithfully
record a hash-chained, signed falsehood.

The record proves the record was not altered. It does not make the harness truthful.

## Why not just OpenTelemetry, or signed logs, or Langfuse

Reasonable questions, and mostly they are different jobs.

- **OpenTelemetry** gives you far richer tracing and an ecosystem this does not have. It is
  not built to be tamper-evident, and spans usually leave the machine. Use both if you want:
  they answer "what happened, in detail" and this answers "can I prove it was not edited".
- **Signing your log files** gets you most of the way, and if you already do it you may not
  need this. The differences are per-record chaining, so a single altered line is located
  rather than the whole file being invalidated, and a pack whose verifier a recipient runs
  with nothing installed.
- **Langfuse, Braintrust, Phoenix** are observability platforms with dashboards, evals and
  span analytics this will never have. They also want a deployment. The self-hosted ones
  document a production floor of several services and roughly 16 GB of RAM. This is one hook
  line and a JSONL file. If you want dashboards, use them.

The narrow thing this does that those do not: produce a record a third party can verify
without trusting you, without installing anything, and without the data leaving your machine.

## And the memory it reads is yours too

The same tool owns the memory your agent reads and writes: plain markdown you can read,
`git diff`, and walk away with. It **catches its own rot, tampering, and poisoning** with
mechanical checks rather than an LLM judge, scores it 0 to 100, and exits non-zero so it
gates CI and cron like a test suite.

![hsm verify --demo: a clean vault scores MEMORY INTACT 100/100, then rot is planted and caught live: ROT DETECTED 0/100 with every finding named](https://raw.githubusercontent.com/fuckbigtech-ai/homestead-memory/master/docs/demo.gif)

```bash
npm install -g @tobilu/qmd@2.1.0  # optional hybrid retrieval runtime

hsm verify --demo
# ① a clean vault           ✅  MEMORY INTACT — 100/100
# ② rot is planted…         🔴  ROT DETECTED —   0/100
#    🔴 [self_contradiction] the note argues with itself about its own status
#    🔴 [uncited_claim]      a distilled claim has no source citation
#    🔴 [dangling_citation]  a cited source no longer exists
#    ⚠️  [broken_link]        a reference points at a note that isn't there
```

That scoring is [RotBench](benchmarks/ROTBENCH.md), published as an open spec so the
number is reproducible rather than self-reported.

**Capture is Claude Code only right now.** The MCP integration below works anywhere MCP
does; the hooks that record *every* tool call use Claude Code's `PreToolUse` and
`PostToolUse`. Cursor and Codex need their own mechanisms and those are not built yet.

## Quickstart (60 seconds)

```bash
hsm init   ./my-vault          # scaffold or adopt any markdown folder
hsm ingest ./my-vault          # index it (hybrid BM25+vector via qmd, optional)
hsm ask    "what did I decide about X?"
hsm ask    "what did I decide about X?" ./my-vault --budget 1200 --json
hsm search "what did I decide?" ./my-vault --retrieval balanced --json
hsm qmd start                  # persistent loopback runtime; no shared global index
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

### Retrieval profiles

Homestead keeps qmd in dedicated cache and config directories. It never runs
maintenance against qmd's global index. Every structured result reports `engine`,
`retrieval_mode`, `degraded`, `reason`, `elapsed_ms`, and `index_age_seconds`.

| profile | behavior | use |
|---|---|---|
| `fast` | BM25 only | exact names, paths, and low-latency probes |
| `balanced` | lexical + vector, no LLM reranker | hooks and normal agent context |
| `quality` | lexical + vector + reranker | explicit high-value research queries |

The route is persistent qmd MCP, then the dedicated qmd CLI, then a read-only
direct scan. Run `hsm qmd doctor`, `hsm qmd refresh`, and `hsm qmd status` to inspect
the runtime without touching any other qmd collection.

Refresh is explicit and incremental. It writes an atomic checkpoint beneath
`.hsm/refresh-state.json`, refuses foreign or unhealthy QMD runtimes, emits a
live heartbeat while QMD works, and commits the vault fingerprint only after
embedding reaches zero pending vectors. Reads never trigger an implicit
refresh; if QMD is unavailable, retrieval falls back to a read-only scan and
reports the degraded engine and reason.

For a Linux/systemd reference deployment, see `deploy/reference/`.

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

**Claude Code / Desktop / Cursor** (MCP: memory tools, not action capture):

```bash
claude mcp add homestead-memory -- hsm mcp ~/my-vault
# tools: memory_ask · memory_search · memory_verify · memory_history ·
#        memory_ingest · memory_distill
```

## Why this exists

"Runs on your device" is table stakes now. Every memory tool stores locally.
**Nobody verifies.** Memory rots quietly: a note contradicts itself, an extracted
"fact" loses its source, a body drifts past its own changelog, the current value
gets shadowed by a stale one. You find out weeks later, when your agent confidently
tells you something that stopped being true in March.

Rot is only the *passive* failure. Memory also gets **tampered** with (a fact
edited after it was written) and **poisoned** (untrusted input injects a "memory"
that was never true, a named 2026 attack class). homestead-memory catches all
three mechanically. Sign the vault and any edited byte breaks the signature. A
distilled claim must cite a source that resolves, or it is dropped. Recall benchmarks
measure whether the model *remembers*. **RotBench measures whether the memory can be
trusted.** See [`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md).

homestead-memory is built around three commitments:

1. **Markdown-primary.** The human-readable files ARE the memory. Indexes and
   projections are derived and disposable. You can leave any time. It is your folder.
   Import/export Google's **Open Knowledge Format** (`hsm export --format okf`) plus
   Mem0/Zep: we are OKF, but signed and verifiable.
2. **Verification over trust.** Integrity is a *number* (RotBench, 0–100), computed
   by mechanical checks, with no LLM judging its own homework. See
   [`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md).
3. **Auditable extraction.** The optional distilled layer ([`docs/DISTILL_SPEC.md`](docs/DISTILL_SPEC.md))
   extracts entity facts *with verbatim quotes, checked in code*. A claim either
   cites a real source or it is dropped. Contradictions append a changelog line
   (`update current_crm: "Salesforce" -> "HubSpot" (source: chat-042.md)`), never a
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
| RotBench | **99.4 / 100** (integrity of the *constructed benchmark vault*, not a claim about arbitrary vaults) |

Read the RotBench row carefully: it scores the vault this harness builds, so it is a
statement about *this* run, not a promise that your vault will score 99. Pointed at a
real working vault it usually will not, and that is the tool doing its job. See
[`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md) for what the score does and does
not measure.

What we will and will not claim: recall is elite and *reader-independent*. QA is honest
and mid. Published systems self-report higher on their own harnesses (Mem0 94.4%,
Zep 63.8% independent). We publish the harness, the judge, and every failed
experiment instead. No number here is from a harness you cannot run yourself.

## Design

- **Cross-platform.** Pure Python except for `cryptography`, which does the signing.
  CI: ubuntu / macos / windows. **The pack's bundled verifier stays stdlib-only**, so a
  recipient checks a pack with nothing installed, which is the part that matters.
- **Degrades explicitly.** qmd 2.1+ is optional. MCP failure falls back to the
  dedicated CLI; qmd failure falls back to a read-only scan. Machine-readable output
  names the engine and reason instead of silently pretending the fast path worked.
- **Local by default.** The HTTP API binds loopback with bearer auth + DNS-rebind
  protection; the MCP server is stdio (client-spawned). Nothing phones home.
- **Temporal.** Changelog lines make history queryable: `hsm history note --as-of DATE`.

## Status

v0.4, building in public. Roadmap: [`ROADMAP.md`](ROADMAP.md). Break our benchmark:
[`benchmarks/ROTBENCH.md`](benchmarks/ROTBENCH.md). Adversarial fixtures get merged.

MIT © Kinetic Labs Inc. · a [FuckBigTech](https://fuckbigtech.ai) / HOMESTEAD project.
