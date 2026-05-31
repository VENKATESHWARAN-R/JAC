# JAC — Just Another Companion/CLI

> A local-first coding companion that runs on your machine — your keys, your files, your context. Built on [Pydantic AI](https://github.com/pydantic/pydantic-ai).

JAC wraps a large language model with the things a model can't do on its own: read and edit your files, run commands, search the web, remember decisions across sessions, and hand big jobs to helper agents — asking before it touches anything that changes your workspace.

> **Status:** v0.9.0 · pre-release · local-first · single-user. Latest: a local **web UI** and an **SDK control plane** that makes every surface a thin adapter over one engine. Full history: [changelog](https://venkateshwaran-r.github.io/JAC/changelog/) · [progress](https://venkateshwaran-r.github.io/JAC/progress/).

## What it is

JAC is a CLI agent you talk to from your terminal (or a local browser). The design bet behind it is plain: **the model is the brain, and everything around it decides whether that brain is used cheaply and well.** JAC is that "everything around it" — the hands, the eyes, the memory, the context discipline. The model only thinks; JAC decides what it sees, what it can do, and when to delegate.

The visible coworker is **Gru**. Gru owns the conversation, the memory, and the final say. When a task is too big or too noisy for one context window — comb a 200-file module, grind through a long log, research a library — Gru spawns **minions**: throwaway sub-agents that go do the heavy reading or running in their *own* context and report back a clean result. The intermediate 100k tokens of noise stay with the minion; your main thread stays lean. That delegation, plus cheap-tier summarization and tiered model routing, is the whole point: keep the cost of each turn down without making the work worse.

It runs entirely on your machine. No accounts, no hosted service, no telemetry you didn't opt into.

## Where it fits

- **Day-to-day in a repo** — "fix the off-by-one in pagination." Gru reads the code, sketches a plan, edits the files (with your yes), and runs the tests. Every change is yours to approve.
- **Big or noisy work** — exploring an unfamiliar codebase, distilling a giant tool output, multi-step research. Hand it to a minion so the main conversation never drowns in intermediate junk.
- **Across repos and agents** — a JAC in your frontend repo can talk to a JAC (or any A2A-compatible agent) in your backend repo to negotiate an API change, no human relaying messages. This is JAC's headline non-table-stakes trick.
- **However you like to drive** — an interactive terminal REPL, a local browser UI, or headless as an agent-to-agent server.

**What it isn't:** not a hosted/SaaS product (local-first means it runs *here*), not a benchmark contender or an autonomy race against Devin/OpenHands, and it doesn't ship its own model — bring any provider (Anthropic, OpenAI, Google, OpenRouter, Mistral, or a local model) via Pydantic AI.

## What it can do

**Work in your codebase.** Read, write, edit, and list files; search with ripgrep + glob; run shell commands synchronously or as long-lived background processes; fetch URLs and search the web (Tavily when a key is set, else DuckDuckGo). Every mutating action is approval-gated, and every tool call states a one-line reason for *why* it's doing it.

**Delegate to minions.** `spawn_sub_agent` (one) and `spawn_sub_agents` (parallel fan-out) hand context-heavy work to isolated sub-agents that run with their own toolset and model tier and return only the result. Depth-capped so there's no runaway recursion; a minion can ask Gru a single focused question mid-run and resume; you approve each spawn (and can counter-propose a cheaper tier).

**Stay cheap.** Cost-efficiency is the product thesis, not an afterthought. Pick a model *tier* (small / medium / large per profile) instead of a name; oversized tool outputs are auto-summarized through a cheap tier before they reach the main loop (the original is kept on disk to re-read on demand); prompt assembly is ordered for cache hits; token budgets and token-aware history compaction (`auto` / `sliding` / `manual`) keep long sessions in bounds. `/tokens` shows where every token went.

**Remember and continue.** Two-scope memory — `remember` / `forget` write durable facts to your user (`~/.jac/memory.md`) or project (`<repo>/.agents/memory.md`) store. Project conventions load automatically from `AGENTS.md`. Sessions persist per project and resume with `jac --resume`; a visible plan/checklist survives the resume.

**Stay in control.** Human-in-the-loop is the default and only shipped mode. Per-tool approval shows the reason and arguments; you approve, deny, or deny-with-feedback (redirect the model without burning a turn). `/mode plan` lets Gru plan without executing anything; `/mode accept-edits` auto-applies file edits while still prompting for shell. (Autonomous "YOLO" mode is deliberately held for v2, behind a real sandbox.)

**Reach other agents (A2A).** Expose your Gru as an A2A server for peer agents to call (as a read-only guest), or call other A2A-compatible agents yourself — with bidirectional file transfer and pluggable per-peer auth (bearer / API key / OAuth2 client-credentials). A working reference peer lives in [`examples/data-analyst-a2a/`](examples/data-analyst-a2a/).

**Extend it.** Load community-format **skills** (Anthropic `SKILL.md`) as on-demand playbooks Gru reads when relevant. Wire in external **MCP servers** (standard `mcpServers` JSON) as deferred-loaded, searchable, approval-gated toolsets — paste an existing config in verbatim.

**Drive it your way.** Three surfaces, one engine: the terminal **REPL**; a local-first **web UI** (`jac web serve` — streaming chat with in-browser approvals, a full control panel for profiles/keys/config/MCP/A2A/skills, and a live activity dashboard; loopback-bound and single-user); and a headless **A2A server** (`jac a2a serve`). Each is a thin renderer over the same shared engine — adding a surface is never a new runtime mode.

**Bring your own model, and see everything.** Multi-provider via Pydantic AI with layered config, named profiles and tiers, and keyring / dotenv / env-only secret backends. [Logfire](https://logfire.pydantic.dev/) traces every model call, tool call, minion spawn, and memory write out of the box.

## Requirements

- **Python 3.13+**
- **macOS or Linux** (Windows untested)
- [`uv`](https://docs.astral.sh/uv/)

## Install

```bash
# Persistent install (adds `jac` to PATH)
uv tool install "git+https://github.com/VENKATESHWARAN-R/JAC.git"

# One-off, no install
uv run --from "git+https://github.com/VENKATESHWARAN-R/JAC.git" jac --help

# From a local clone
git clone https://github.com/VENKATESHWARAN-R/JAC.git && cd JAC && uv sync
```

## Quick start

```bash
jac init        # pick a secrets backend, create your first profile, store an API key
jac             # start a session in the current repo
jac --resume    # continue the last session
jac web serve   # open the local browser UI (loopback, single-user)
jac a2a serve   # run headless as an A2A server for peer agents
```

No model or key is ever defaulted in code — if nothing is configured, `jac` exits with an actionable error pointing at `jac init`, `JAC_MODEL`, or `--model`. Full walkthrough: **[Getting started](https://venkateshwaran-r.github.io/JAC/user-guide/getting-started/)**.

## Documentation

**Published site:** [https://venkateshwaran-r.github.io/JAC/](https://venkateshwaran-r.github.io/JAC/)

| | |
| --- | --- |
| **User guide** | [Getting started](https://venkateshwaran-r.github.io/JAC/user-guide/getting-started/) · [CLI reference](https://venkateshwaran-r.github.io/JAC/user-guide/cli-reference/) · [Web UI](https://venkateshwaran-r.github.io/JAC/user-guide/web-ui/) · [Configuration](https://venkateshwaran-r.github.io/JAC/user-guide/configuration/) · [Cost controls](https://venkateshwaran-r.github.io/JAC/user-guide/cost-controls/) · [Skills](https://venkateshwaran-r.github.io/JAC/user-guide/skills/) · [MCP servers](https://venkateshwaran-r.github.io/JAC/user-guide/mcp/) · [Sessions & memory](https://venkateshwaran-r.github.io/JAC/user-guide/sessions-and-memory/) · [Examples](https://venkateshwaran-r.github.io/JAC/user-guide/examples/) · [A2A operator](https://venkateshwaran-r.github.io/JAC/user-guide/a2a-operator/) |
| **Developer** | [Contributing](https://venkateshwaran-r.github.io/JAC/developer/contributing/) · [Module strategy](https://venkateshwaran-r.github.io/JAC/developer/module-strategy/) · [Codebase map](https://venkateshwaran-r.github.io/JAC/developer/codebase-map/) · [Capabilities & hooks](https://venkateshwaran-r.github.io/JAC/developer/capabilities/) |
| **Design** | [Idea](https://venkateshwaran-r.github.io/JAC/idea/) · [Architecture](https://venkateshwaran-r.github.io/JAC/architecture/) · [Cost-efficient orchestration](https://venkateshwaran-r.github.io/JAC/design/cost-efficient-orchestration/) · [Web surface](https://venkateshwaran-r.github.io/JAC/design/web-surface/) · [Progress dashboard](https://venkateshwaran-r.github.io/JAC/progress/) |

To browse the docs locally: `just docs-serve` → http://127.0.0.1:8000 (sources live under `docs/`).

## Development

```bash
just check       # format + lint + typecheck + drift + tests (the CI gates)
just fix         # auto-format + lint fix
just docs-serve  # live-reload docs site
```

Contributions welcome — fork, branch, open a PR; CI must pass before merge. See [Contributing](https://venkateshwaran-r.github.io/JAC/developer/contributing/) for the workflow, required reading, and conventions.

## Built on

**Core**

- **[Pydantic AI](https://github.com/pydantic/pydantic-ai)** — the agent framework JAC is built on.
- **[fasta2a](https://github.com/pydantic/fasta2a)** — A2A server integration.

**Design inspiration** (not vendored)

- **[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)** — capability patterns JAC adopts selectively (`ApprovalRequiredToolset`, `Instrumentation`, …).
- **[pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents)** — overall agent-harness design reference.

**On the roadmap** (considered, not integrated yet)

- **[memv](https://github.com/vstorm-co/memv)** — predict-calibrate memory extraction (v2).
- **[Monty](https://github.com/pydantic/monty)** — Rust-based Python sandbox for YOLO mode (v2).

## License

[MIT](LICENSE) © 2026 Venkateshwaran R
