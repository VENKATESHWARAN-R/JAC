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

Five levers we pull (full design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md)):

1. **Sub-agents** — delegate context-heavy work (large file exploration, web research, long shell pipelines) to an isolated agent. Its intermediate tokens stay in *its* context; only the result returns to the main agent.
2. **Tier-aware model selection** — small / medium / large per profile. The main agent picks a *tier* when it spawns a sub-agent; the user approves the tier (HITL). Specific model names never appear in the LLM's context.
3. **Tool result post-processor** — when a tool returns above a configured threshold *and* a cheap tier is available, route the raw output through the small model to summarize before it lands in the main loop. Original is saved to disk; agent re-reads on demand.
4. **Cache-friendly prompt assembly** — order system prompt + tools + memory + history to maximize prompt-cache hits.
5. **Deterministic hooks** — post-flight validators (typecheck, tests, lint) that run after a sub-agent finishes. All pass → return verbatim, no extra LLM turn. Any fail → route the failure back into the same sub-agent for a fix.

## What JAC focuses on

- **One visible coworker ("Gru")** that owns the conversation, memory, mode selection, and final accountability.
- **A single `spawn_sub_agent` tool** Gru calls when a task benefits from isolation. No bespoke "agent types" or "modes" — just one tool with a task packet, called many ways.
- **Community-format skills** (Anthropic SKILL.md spec) as *loadable playbooks* — prompts and recipes the main agent reads when relevant. Skills are advice, not a runtime mode.
- **Deterministic post-flight hooks** attached to sub-agent spawns: typecheck, tests, lint, format. Free cost reduction by avoiding "is this done?" LLM turns.
- **HITL-first execution** with per-tool approval; sub-agent spawns are HITL-gated too (the user sees task summary + tier and can counter-propose).
- **Tiered memory:** session, project, and user scopes — prose first, structured later.
- **A2A interop** as a headline non-table-stakes feature (Phase 4 — feature-complete).

## What JAC explicitly does NOT do

- Compete on benchmarks against Claude Code, Devin, Aider, etc.
- Run as a hosted/cloud service. Local-first means local execution.
- Provide a managed/SaaS offering.
- Ship its own LLM. Multi-provider via Pydantic AI.
- Support every IDE/surface up front. **CLI first.**
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

**Sub-agents** are disposable Pydantic AI `Agent` instances. They receive a task packet, run with their own toolset and tier, optionally have deterministic hooks attached, and return a result. They do not inherit Gru's conversation. They cannot spawn further sub-agents (depth cap = 1 in v1).

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

- **Phase A — Context-cost foundation.** Tool result caps; tool result post-processor; cache-friendly prompt assembly. Highest leverage; do first.
- **Phase B — Sub-agent tool.** `spawn_sub_agent` with task packet, tier-HITL approval, depth cap = 1.
- **Phase C — Deterministic hooks.** Post-flight callables; failures route back into the sub-agent.
- **Phase D — Skill loader.** Anthropic community format; skills are loadable prompts, no `mode: minion`.
- **Phase E — Parallel sub-agents + HITL multiplexing.**
- **Phase F — Plan Mode** (pulled forward from v2).
- **Phase G — A2A Phase 4.e (OIDC/GCP), MCP loader, broader test coverage.**

Already shipped (do not redo): Phase 1.7 coworker experience, Phase 2a/2a.1 memory, Phase 4 A2A (PR1–PR4 + 4.d hotfixes + file transfer + demo peer).

Full phase checklist: [`progress.md`](progress.md). Locked decisions: [`architecture.md`](architecture.md) §5.

## v2 — explicitly deferred

- YOLO mode with native sandboxing (Monty + `sandbox-exec` / `bwrap`)
- CodeMode integration (`pydantic-ai-harness`)
- Stuck-loop detection
- Night Shift / cron-triggered headless runs
- User-tier predict-calibrate memory extraction (the `~/.jac/memory.md` file exists; automatic extraction is v2)
- Browser / API / SDK surfaces
