# JAC — Just Another Companion/CLI

> **Status:** Draft v3 · **Last revised:** 2026-05-24 · **Type:** product vision
>
> For what's shipped today see [`progress.md`](progress.md). For how it's built see [`architecture.md`](architecture.md).

## What JAC is

**JAC** (**J**ust **A**nother **C**ompanion/CLI) is a Python CLI built on Pydantic AI. It runs on the user's machine and wraps an LLM with the things the model lacks on its own: persistent memory, tools, orchestration, context discipline, and continuity across sessions.

JAC is the runtime around the model that turns raw reasoning into a practical worker.

Stack direction: **Python + Pydantic AI**, multi-provider for models.

## Why JAC exists

In honest priority order:

1. **Learning.** Building a harness end-to-end is the best way to understand modern agentic systems. The build itself is the point — shipping is a side effect.
2. **Cross-repo coworking via A2A.** Two JAC instances on two repos can talk to each other via the A2A protocol — letting an agent in a frontend repo negotiate API changes with an agent in a backend repo without a human mediating. Nothing in the open-source landscape covers this well today.

JAC is **not** trying to beat Claude Code at what Claude Code already does well. Most table-stakes capabilities will be shared with the existing pack. We're not chasing benchmark wins.

## What JAC focuses on

- **One visible coworker ("Gru")** that owns the conversation, memory, mode selection, and final accountability.
- **Disposable scoped workers ("minions")** spawned only when delegation reduces context noise, cost, latency, or risk.
- **Community-format skills (D21)** as the extension mechanism — inline context injection and optional `mode: minion` sub-agents from the same `SKILL.md` files.
- **HITL-first execution** with per-tool approval; YOLO (sandboxed, autonomous) deferred to v2.
- **Tiered memory:** session, project, and user scopes — prose first, structured later.
- **A2A interop** as a headline non-table-stakes feature (Phase 4).

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
Gru ── works directly for simple tasks
  ↓
Skills / Minion Factory ── given Gru's intent, produces a scoped worker
  ↓
Minion(s) ── short-lived, scoped, isolated worker(s)
  ↓
Gru ── merges results, decides next action, reports back
```

**Gru** owns: the conversation, memory, mode, accountability, the decision to work directly vs. delegate.

**Minions** are disposable. Receive clean task packets, use only granted tools, return structured output, disappear. They do not inherit Gru's full conversation by default.

**Important separations (do not conflate):**

- **Mode** (HITL/YOLO) — how much of the loop runs without human input.
- **Approval policy** — which tools/actions require confirmation, attached per-tool not per-persona.
- **Model tier** — selected per step / per minion, not globally.
- **Role/persona** — what kind of work a worker is doing.

## Operating modes

- **HITL (default):** Every tool call requires user approval. The right mode for any non-trivial environment, and the only mode shipped today.
- **YOLO:** Autonomous; tool calls auto-approve within configured boundaries. **Requires sandbox.** Best for scheduled / overnight runs. **Deferred to v2.**

## Current scope (post-2026-05-22 reshuffle)

The roadmap was reordered in May 2026 to prioritize user-visible value. Key shifts:

- **Phase 1.7** ("Coworker experience") shipped: token-aware compaction, status bar, slash commands, tiered profiles, token budgets, plan persistence on resume.
- **Phase 2b** (session-close summarizer minion) **superseded** by D20 compaction.
- **Phase 3** is now **Skills** (community Anthropic format), not YAML minion templates.
- **Phase 4 A2A** moved out of v2 — inbound guest-Gru server + outbound `a2a_call` tools.
- **Phase 5 Minions** builds on the skills substrate; runtime grooming pending.

Full phase checklist: [`progress.md`](progress.md). Locked decisions: [`architecture.md`](architecture.md) §11.

## v2 — explicitly deferred

- YOLO mode with native sandboxing (Monty + `sandbox-exec` / `bwrap`)
- CodeMode integration (`pydantic-ai-harness`)
- Night Shift / cron-triggered headless runs
- User-tier predict-calibrate memory extraction (the `~/.jac/memory.md` file exists; automatic extraction is v2)
- Plan Mode structural toolset swap (D23)
- Browser / API / SDK surfaces
