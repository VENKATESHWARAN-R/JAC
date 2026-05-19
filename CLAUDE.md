# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state: design-phase, pre-implementation

JAC is a **local-first AI coworker harness** built on Pydantic AI. The repo currently contains design documents and project scaffolding — there is no source code yet. Implementation begins with Phase 0 of `ARCHITECTURE.md` §9 (skeleton + Logfire + minimal CLI shell + bare Gru agent).

**Read these first, in order:**

1. `IDEA.md` — what JAC is, why it exists, what's in/out of scope, v1 vs v2 split.
2. `ARCHITECTURE.md` — the design contract. Mermaid diagrams, JAC↔Pydantic AI mapping, locked decisions (§11), proposed module layout (§3), phased roadmap (§9), user journeys (§10).

When the design is ambiguous, **`ARCHITECTURE.md` is the source of truth for *how* JAC is built**; **`IDEA.md` is the source of truth for *what it is* and *what it is not*.** If you need to deviate from either, update the doc in the same change.

## Stack

- **Python 3.13**, managed with `uv` (`uv.lock` committed).
- **Pydantic AI** (`pydantic-ai-slim` with anthropic/openai/google/openrouter/mistral providers, plus duckduckgo, fastmcp, evals).
- **Logfire** for tracing — every model call, tool call, minion spawn, and memory write must be instrumented.
- **typer + rich + prompt-toolkit** for the CLI surface.
- **fasta2a** for A2A (v2; server-side only — client side is a bespoke HTTP toolset we write).
- **pydantic-settings** for config.

## Commands

```bash
uv sync                    # install / refresh dependencies
uv run python -c "..."     # quick check inside the env
```

No `jac` CLI entry point, no tests, no linter config yet — these arrive in Phase 0. Update this section as those land.

## Architecture — non-negotiables

These are the structural rules every change must respect. Full rationale is in `ARCHITECTURE.md`; this is the cheat sheet.

### Capabilities are the atom of the system

Almost every cross-cutting concern is a Pydantic AI `Capability`, not a hand-rolled class. Tools, memory tiers, telemetry, the minion factory, sandboxing, even the CLI event bus — all capabilities. **If you find yourself writing a class that hooks into the agent lifecycle without being a Capability, you are probably wrong.** See §2.

### Hooks are the runtime event bus, not a logging detail

The CLI does not poll the agent. The CLI installs a `Hooks` capability that pushes lifecycle events onto an `asyncio.Queue`; the CLI renderer consumes the queue. All surfaces (CLI today; TUI/web later) reuse the same capability set; only the renderer changes. See §7.

### Every tool requires a `reason: str` parameter

Every tool exposed to Gru or a minion **must** accept `reason: str` as its first argument. The LLM must justify each call in one sentence; the CLI renders the reason in the approval prompt. Enforced structurally via a `JacTool` decorator and a wrapper toolset that rejects tools missing the parameter at agent construction (fail-fast, not at runtime). See §6a.

### HITL is built into Pydantic AI; don't reinvent it

Use `ApprovalRequiredToolset` + the `deferred_tool_calls` hook for approval flows. Do not write a custom approval system.

### Don't reinvent what Pydantic AI already provides

The following are built-in or shipped via `pydantic-ai-harness` — use them, don't reimplement:

- `ApprovalRequiredToolset` and `deferred_tool_calls` (HITL)
- `ProcessHistory` (sliding-window / summarization of message history)
- `Instrumentation` (Logfire spans)
- `CodeMode` (run_code single-tool pattern, includes Monty sandbox)
- `ModelMessagesTypeAdapter` (message history serialization)
- `pydantic_ai.direct.model_request_sync` (lightweight model calls for routing/classification — use this instead of spinning up a full agent for tiny tasks like "should I delegate?")

### Minions = `Agent.from_spec()` loaded from YAML

Minions are short-lived agents loaded from declarative YAML specs in `src/jac/minions/templates/`. They receive a **locked task packet schema** via `deps`:

| Field | Required | Purpose |
| --- | --- | --- |
| `objective` | yes | What the minion must accomplish (one sentence) |
| `success_criteria` | yes | How the minion knows it's done |
| `relevant_files` | no | Files the minion should focus on |
| `forbidden_actions` | no | Specific actions the minion must not perform |
| `expected_output` | yes | Description / JSONSchema of return shape |

Templates may add their own `deps_schema` fields, but these five stay stable. Gru never sees a minion's internal turns — only its structured output.

### Memory: prose first, structured later

Project memory starts as a single `PROJECT.md` (prose), auto-injected via a `ProjectMemory` capability's `get_instructions()`. Structured `facts.jsonl` is added **only when prose retrieval gets noisy** — memory management is a last resort, not a first move. Session memory lives under `.jac/sessions/<timestamp>/` (folder-per-session, timestamp-named, human-readable).

### Tracing fields on every Logfire span

Every span carries: `template`, `task_id`, `parent_run_id`, `token_cost`, `duration`, `exit_status`. This is what makes minion runs debuggable later.

## What is v2 (do not build in v1)

If a task seems to require any of these, stop and ask before scaffolding:

- A2A interop (outbound exposure via `fasta2a`, inbound calls via bespoke HTTP toolset)
- Night Shift / cron-triggered headless runs
- YOLO mode + sandboxing (Monty + `sandbox-exec`/`bwrap`)
- User-tier memory + predict-calibrate extraction
- Browser / API / SDK surfaces
- Agent-authored reusable skills

The full roadmap is `ARCHITECTURE.md` §9.

## Reference projects (read-only, for inspiration)

Cloned to `~/Projects/personal/JAC-research/` — peer to this repo, not inside it. **Do not fork or vendor; read for design ideas only.**

- `pydantic-ai-harness/` — official capability library (`CodeMode` lives here)
- `pydantic-deepagents/` — closest analog to JAC; source of stuck-loop detection and orphan-repair patterns
- `pydantic-ai-backend/` — console toolset + Docker sandbox patterns
- `memv/` — predict-calibrate memory (v2)
- `monty/` — Rust-based Python sandbox (v2 YOLO)
- `pi/` — multi-package harness; agent-authored skills pattern

## Documentation discipline

This project will outlive any single session. The design docs are how it survives long gaps.

- When you make a structural decision, **update `ARCHITECTURE.md` in the same change** — preferably by extending the decisions table in §11.
- When the vision or scope shifts, **update `IDEA.md`**.
- Don't accumulate undocumented architectural debt.
