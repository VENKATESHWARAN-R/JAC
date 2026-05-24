# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**JAC** (**J**ust **A**nother **C**ompanion/CLI) is an agentic harness built on Pydantic AI. See [`docs/idea.md`](docs/idea.md) for product vision and scope.

## Project state

See [`docs/progress.md`](docs/progress.md) for what's implemented, in flight, and queued. **Update it as work lands.**

For the *why*, read [`docs/idea.md`](docs/idea.md). For the *how*, read [`docs/architecture.md`](docs/architecture.md). When the design is ambiguous, **`docs/architecture.md` is the source of truth for *how* JAC is built**; **`docs/idea.md` is the source of truth for *what it is and is not*.** If you deviate from either, update the doc in the same change.

Long-form docs live under [`docs/`](docs/) and are published as a Zensical site (`just docs-serve`). For module layout see [`docs/developer/codebase-map.md`](docs/developer/codebase-map.md); for contributing workflow see [`docs/developer/contributing.md`](docs/developer/contributing.md).

## Stack

- **Python 3.13**, managed with `uv` (`uv.lock` committed).
- **Pydantic AI** (`pydantic-ai-slim` with anthropic/openai/google/openrouter/mistral providers, plus duckduckgo, fastmcp, evals).
- **Logfire** for tracing — every model call, tool call, minion spawn, and memory write must be instrumented.
- **typer + rich + prompt-toolkit** for the CLI surface.
- **fasta2a** for A2A (Phase 4; server-side only — outbound is a bespoke HTTP toolset).
- **pydantic-settings[yaml]** for layered config.

## Commands

Day-to-day commands are wrapped in a [`justfile`](justfile) — `just check`, `just fix`, `just docs-serve`, `just run -- <args>`. Full recipe list: [`docs/developer/contributing.md`](docs/developer/contributing.md).

```bash
uv sync                          # install / refresh dependencies
uv run jac                       # interactive REPL with the default profile
uv run jac --profile NAME        # one-shot profile selection
uv run jac --model PROVIDER:ID   # raw model override (bypasses profiles)
uv run jac --resume              # resume the latest project session
uv run jac --session ID          # resume a specific session by id

uv run jac init                  # wizard: backend + profile + key storage
uv run jac profiles              # list profiles, mark default
uv run jac keys                  # show required keys with status
uv run jac sessions              # list sessions in this project
uv run jac a2a serve             # headless A2A server

uv run jac --help                # full CLI help
```

**No required runtime values are defaulted in code.** Set them via `jac init`, env vars, or the `--model` flag. See `.env.template` for env-var examples.

## Configuration & workspace

### File-format conventions (locked)

| What | Location | Format |
| --- | --- | --- |
| Secrets (API keys, tokens) | `.env`, env vars | **dotenv** |
| App config | `~/.jac/config.yaml`, `<repo>/.agents/config.yaml` | **YAML** |
| Provider catalog | `src/jac/data/providers.yaml` (package), `~/.jac/providers.yaml` (user overlay) | **YAML** |
| Skills (community Anthropic format — D21) | `~/.jac/skills/<name>/SKILL.md`, `<repo>/.agents/skills/<name>/SKILL.md` | **Markdown w/ YAML frontmatter** |
| System prompts | `~/.jac/prompts/*.md`, `<repo>/.agents/prompts/*.md` | **Markdown** |
| Project context (auto-loaded) | `<repo>/AGENTS.md` (at repo root, community convention) | **Markdown** |
| User context (auto-loaded) | `~/.jac/AGENTS.md` | **Markdown** |
| Session message history | `<repo>/.agents/sessions/<ts>/messages.json` | **JSON** |
| Session plan checklist (D27) | `<repo>/.agents/sessions/<ts>/plan.json` | **JSON** |
| Project memory (JAC-managed, auto-loaded) | `<repo>/.agents/memory.md` | **Markdown** |
| User memory (JAC-managed, auto-loaded) | `~/.jac/memory.md` | **Markdown** |
| Per-session token usage (D25 budgets) | `<repo>/.agents/usage.jsonl` | **JSONL** |

**Unified standards:** one format per category. YAML for human-edited structured data; JSON / JSONL for machine state; Markdown for prose; dotenv for secrets.

### Layered config precedence (highest → lowest)

1. CLI arguments (`--model ...`, `--profile NAME`)
2. Environment variables (`JAC_MODEL=...`, `JAC_SECRETS__BACKEND=...`)
3. `.env` file in CWD
4. Project config (`<repo>/.agents/config.yaml`)
5. User config (`~/.jac/config.yaml`)
6. Package defaults (`src/jac/data/defaults.yaml` — *non-required* values only)

Implementation lives in `jac.workspace.config_loader`. Missing required values raise `JacConfigError` at point of use. Profiles, tiers, compaction, budgets, secrets: [`docs/user-guide/configuration.md`](docs/user-guide/configuration.md). Memory read/write paths: [`docs/user-guide/sessions-and-memory.md`](docs/user-guide/sessions-and-memory.md).

## Architecture — non-negotiables

Structural rules every change must respect. Full rationale in [`docs/architecture.md`](docs/architecture.md); capability patterns in [`docs/developer/capabilities.md`](docs/developer/capabilities.md).

### Fail-first, no hardcoding

- Every path, model, provider, and prompt must be configurable through the layered config above.
- Required config that's missing **raises `JacConfigError`** with a message telling the user exactly how to fix it. Never silently default to something that costs money or behaves unexpectedly.
- Paths are derived constants — one source of truth in `jac.workspace.paths`, never strings sprinkled across modules.
- "Silent fallback to a safe default" is forbidden. Be loud, be explicit.

### Capabilities are the atom of the system

Almost every cross-cutting concern is a Pydantic AI `Capability`, not a hand-rolled class. Tools, memory, telemetry, the minion factory, sandboxing, even the CLI event bus — all capabilities. **If you find yourself writing a class that hooks into the agent lifecycle without being a Capability, you are probably wrong.**

### Hooks are the runtime event bus, not a logging detail

The CLI does not poll the agent. The CLI installs a `Hooks` capability that pushes lifecycle events onto an `asyncio.Queue`; the CLI renderer consumes the queue. All surfaces (CLI today; TUI/web later) reuse the same capability set; only the renderer changes.

### Every tool requires a `reason: str` parameter

Every tool exposed to Gru or a minion **must** accept `reason: str` as its first argument. Enforced structurally via a `@jac_tool` decorator and a wrapper toolset that rejects tools missing the parameter at agent construction (fail-fast, not at runtime).

### HITL is built into Pydantic AI; don't reinvent it

Use `ApprovalRequiredToolset` + the `deferred_tool_calls` hook for approval flows. Do not write a custom approval system.

### Don't reinvent what Pydantic AI already provides

Use built-in or `pydantic-ai-harness` primitives: `ApprovalRequiredToolset`, `deferred_tool_calls`, `ProcessHistory`, `Instrumentation`, `CodeMode`, `ModelMessagesTypeAdapter`, `pydantic_ai.direct.model_request_sync`.

### Tracing fields on every Logfire span

Every span carries: `template`, `task_id`, `parent_run_id`, `token_cost`, `duration`, `exit_status`.

## What is v2 (do not build now)

After the 2026-05-22 roadmap reshuffle, **A2A and skills moved out of v2** (to Phase 4 and Phase 3 respectively). What's actually still v2:

- YOLO mode + sandboxing (Monty + `sandbox-exec` / `bwrap` + Git-Clean Guard)
- CodeMode integration (`pydantic-ai-harness`) — deferred until we see real context bloat from individual file tools
- Stuck-loop detection — low value in HITL where the human catches loops; mandatory only for YOLO
- Night Shift / cron-triggered headless runs
- User-tier predict-calibrate memory extraction (the `~/.jac/memory.md` *file* already exists per Phase 2a.1; what's deferred is the *automatic extraction*)
- Browser / API / SDK surfaces

If a task seems to require any of the above, stop and ask before scaffolding. The full roadmap is `docs/architecture.md` §9; the live tracker is `docs/progress.md`.

**Things that are NOT v2 anymore** (look at the right phase block before touching):

- **A2A interop** — Phase 4 (server-side `fasta2a` + outbound `a2a_call` tool, isolated guest-Gru — D24)
- **Skills** — Phase 3 (community Anthropic format — D21)
- **Tier-based models** — Phase 1.7.c (D22) — shipped
- **Minion runtime** — Phase 5; grooming pending. **Don't write code against it yet.**

## Documentation discipline

**Where to update what** is defined in [`docs/design/documentation-strategy.md`](docs/design/documentation-strategy.md) — read it before adding or reorganising any doc. The short version:

- Structural decision → `docs/architecture.md` §5 in the same change.
- Vision or scope shift → `docs/idea.md`.
- Work lands → `docs/progress.md` + relevant `docs/user-guide/*.md` page if user-visible.
- When updating docs, **replace outdated information** — don't append. One fact, one home.
- Don't accumulate undocumented architectural debt.
