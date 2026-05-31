# JAC — Just Another Companion/CLI

> **Status:** Draft v4 · **Last revised:** 2026-05-26 · **Type:** product vision
>
> For what's shipped today see [`progress.md`](progress.md). For how it's built see [`architecture.md`](architecture.md). For the cost-efficiency thesis driving the active roadmap see [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md).

## What JAC is

**JAC** (**J**ust **A**nother **C**ompanion/CLI) is a Python CLI built on Pydantic AI. It runs on the user's machine and wraps an LLM with the things the model lacks on its own: persistent memory, tools, orchestration, context discipline, and continuity across sessions.

**JAC is orchestration around an intelligent layer.** The LLM is the brain. JAC is the nervous system, the eyes, the hands, the feet. The model only thinks — everything else (what it sees, what it can do, when work is delegated elsewhere, how big tool outputs are filtered) is JAC's responsibility.

Stack direction: **Python + Pydantic AI**, multi-provider for models.

## Why JAC exists

In honest priority order:

1. **Learning.** Building a harness end-to-end is the best way to understand modern agentic systems. The build itself is the point — shipping is a side effect.
2. **Cross-repo coworking via A2A.** Two JAC instances on two repos can talk to each other via the A2A protocol — letting an agent in a frontend repo negotiate API changes with an agent in a backend repo without a human mediating. Nothing in the open-source landscape covers this well today.

JAC is **not** trying to beat Claude Code at what Claude Code already does well. Most table-stakes capabilities will be shared with the existing pack. We're not chasing benchmark wins.

## The product thesis: cost-efficient orchestration

Cost is the dominant force. **Cost ≈ Σ over turns of `turn_tokens × turn_price`.** The model's price-per-token is set by the provider. What JAC controls is *how many tokens flow into each turn*. Every architectural decision is judged against that equation.

Four levers we pull (full design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md)):

1. **Sub-agents** — delegate context-heavy work (large file exploration, web research, long shell pipelines) to an isolated agent. Its intermediate tokens stay in *its* context; only the result returns to the main agent.
2. **Tier-aware model selection** — small / medium / large per profile. The main agent picks a *tier* when it spawns a sub-agent; the user approves the tier (HITL). Specific model names never appear in the LLM's context.
3. **Tool result post-processor** — when a tool returns above a configured threshold *and* a cheap tier is available, route the raw output through the small model to summarize before it lands in the main loop. Original is saved to disk; agent re-reads on demand.
4. **Cache-friendly prompt assembly** — order system prompt + tools + memory + history to maximize prompt-cache hits.

A fifth lever — *deterministic post-flight hooks* — was designed and dropped (D37): a sub-agent's `success_criteria` plus a post-return `run_shell` call cover verification without framework machinery, and JAC runs in any environment so baked-in hooks are wrong.

## What JAC focuses on

- **One visible coworker ("Gru")** that owns the conversation, memory, mode selection, and final accountability.
- **A single `spawn_sub_agent` tool** Gru calls when a task benefits from isolation. No bespoke "agent types" or "modes" — just one tool with a task packet, called many ways.
- **Community-format skills** (Anthropic SKILL.md spec) as *loadable playbooks* — prompts and recipes the main agent reads when relevant. Skills are advice, not a runtime mode.
- **Verification without framework machinery** — a sub-agent's `success_criteria` plus a post-return `run_shell` call (typecheck, tests, lint) replace the once-planned post-flight hook runner (D37). Cheaper than an "is this done?" LLM turn, and it works in any environment.
- **HITL-first execution** with per-tool approval; sub-agent spawns are HITL-gated too (the user sees task summary + tier and can counter-propose).
- **Tiered memory:** session, project, and user scopes — prose first, structured later.
- **A2A interop** as a headline non-table-stakes feature (Phase 4 — feature-complete).

## What JAC explicitly does NOT do

- Compete on benchmarks against Claude Code, Devin, Aider, etc.
- Run as a hosted/cloud service. Local-first means local execution.
- Provide a managed/SaaS offering. **The web UI (D48) is no exception: it is single-user and loopback-bound by charter — never a multi-tenant server.**
- Ship its own LLM. Multi-provider via Pydantic AI.
- Support every IDE/surface up front. **CLI first; a local-first, single-user web UI is the second human surface.** Every surface is a thin renderer over one shared engine + control plane — never a new runtime mode (see [`architecture.md`](architecture.md) §1.5).
- Market "cost-effective tiered model routing" as a differentiator — tiered routing is an internal capability, not a marketed angle.
- Win on raw autonomy — Devin and OpenHands already compete there.

## Architecture thesis

```
User
  ↓
Gru ── handles the conversation, calls tools directly for inline work
  ↓
spawn_sub_agent (tool, HITL-approved) ── delegates context-heavy tasks
  ↓
Sub-agent(s) ── isolated context, scoped tier, optional post-flight hooks
  ↓
Gru ── receives result (full-fidelity, not compressed), decides next action
```

**Gru** owns: the conversation, memory, mode, accountability, the decision to work inline vs. delegate.

**Sub-agents** are disposable Pydantic AI `Agent` instances. They receive a task packet, run with their own toolset and tier, and return a result. They do not inherit Gru's conversation. They cannot spawn further sub-agents (depth cap = 1 in v1).

**Important separations (do not conflate):**

- **Mode** (HITL/YOLO) — how much of the loop runs without human input.
- **Approval policy** — which tools/actions require confirmation, attached per-tool not per-persona.
- **Model tier** — selected per spawn, surfaced for HITL approval. Never a specific model name.
- **Skill** — a loadable prompt / playbook the main agent injects when relevant. Not a runtime mode.

## Operating modes

- **HITL (default):** Every tool call requires user approval. The right mode for any non-trivial environment, and the only mode shipped today.
- **YOLO:** Autonomous; tool calls auto-approve within configured boundaries. **Requires sandbox.** Best for scheduled / overnight runs. **Deferred to v2.**

## Current scope (post-2026-05-26 reframe)

The roadmap was reframed around the cost-efficiency thesis. Old Phase 3/5/6 entries (Skills with `mode: minion`, the minion runtime, the MCP loader) were archived to [`progress-archive-2026-05.md`](progress-archive-2026-05.md). New phasing:

- **Phase A — Context-cost foundation.** Tool result caps; tool result post-processor; cache-friendly prompt assembly. Highest leverage; do first. **Shipped v0.3.0.**
- **Phase B — Sub-agent tool.** `spawn_sub_agent` with task packet, tier-HITL approval, depth cap = 1. **Shipped v0.3.0.**
- **Phase C — Deterministic hooks.** **Dropped** — complexity didn't earn its keep; `success_criteria` + post-return `run_shell` covers verification.
- **Phase D — Skill loader.** Anthropic community format; skills are loadable prompts, no `mode: minion`. **Shipped v0.4.0.**
- **Phase E — Parallel sub-agents + bidirectional comms.** `spawn_sub_agents` fan-out; D41 bidirectional channel (on by default); `minion-N` IDs; sub-agent HITL/skills/A2A parity. **Shipped v0.5.0.**
- **Phase F — MCP loader** (promoted from old Phase G after 2026-05-27 review). **Shipped 2026-05-29.**
- **Phase G — Plan Mode + Accept-Edits** (pulled forward from v2; demoted from old Phase F to follow MCP). **Shipped v0.7.0.**
- **Phase H — A2A Phase 4.e (OIDC/GCP) + broader test coverage.** Future.
- **Web surface (D48) — local-first browser UI.** A streaming chat + a full control panel (profiles, keys, config, MCP, A2A, skills, …), alongside the CLI and A2A. Single-user, loopback-bound, never multi-tenant. **Slices 1–3 + the R0–R5 redesign shipped (2026-05-31); the SDK control plane (D49) makes it — and every surface — a thin adapter over one engine.**

Already shipped (do not redo): Phase 1.7 coworker experience, Phase 2a/2a.1 memory, Phase 4 A2A (PR1–PR4 + 4.d hotfixes + file transfer + demo peer).

Full phase checklist: [`progress.md`](progress.md). Locked decisions: [`architecture.md`](architecture.md) §5.

## v2 — explicitly deferred

- YOLO mode with native sandboxing via direct `pydantic-monty` (D43) — NOT Docker, NOT CodeMode wrapper
- Stuck-loop detection (watch Harness PR #186)
- Night Shift / cron-triggered headless runs
- User-tier predict-calibrate memory extraction (the `~/.jac/memory.md` file exists; automatic extraction is v2)
- **ACP — editor surface (D45, condition-gated)** — `ACPCapability` implementing the [Agent Client Protocol](https://agentclientprotocol.com) so VS Code / Zed / JetBrains can reach JAC through a standard editor-agent protocol instead of bespoke extensions. Ships when ACP remote transport stabilises and a major editor ships an ACP client.
- Other browser / native SDK surfaces (post-ACP)
