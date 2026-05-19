# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

See `PROGRESS.md` for what's implemented, what's in flight, and what's queued. **Update it as work lands.**

For the *why*, read `IDEA.md`. For the *how*, read `ARCHITECTURE.md`. When the design is ambiguous, **`ARCHITECTURE.md` is the source of truth for *how* JAC is built**; **`IDEA.md` is the source of truth for *what it is and is not*.** If you deviate from either, update the doc in the same change.

## Stack

- **Python 3.13**, managed with `uv` (`uv.lock` committed).
- **Pydantic AI** (`pydantic-ai-slim` with anthropic/openai/google/openrouter/mistral providers, plus duckduckgo, fastmcp, evals).
- **Logfire** for tracing — every model call, tool call, minion spawn, and memory write must be instrumented.
- **typer + rich + prompt-toolkit** for the CLI surface.
- **fasta2a** for A2A (v2; server-side only — client side is a bespoke HTTP toolset we write).
- **pydantic-settings** for layered config.

## Commands

```bash
uv sync                    # install / refresh dependencies
uv run jac                 # interactive REPL (requires a provider key + a model setting)
uv run jac --help          # CLI help
uv run python -m jac       # equivalent invocation
```

**No required runtime values are defaulted in code.** Set them via `.env`, env vars, project/user config files, or the `--model` flag. See `.env.template` for the canonical list of environment variables.

## Configuration & workspace

### File-format conventions (locked)

| What | Location | Format |
| --- | --- | --- |
| Secrets (API keys, tokens) | `.env`, env vars | dotenv |
| App config | `~/.jac/config.toml`, `<repo>/.jac/config.toml` | **TOML** |
| Agent / minion specs | `~/.jac/minions/*.yaml`, `<repo>/.jac/minions/*.yaml` | **YAML** |
| System prompts | `~/.jac/prompts/*.md`, `<repo>/.jac/prompts/*.md` | **Markdown** |
| Session message history | `<repo>/.jac/sessions/<ts>/messages.json` | **JSON** |
| Project memory (prose) | `<repo>/.jac/PROJECT.md` | **Markdown** |
| Project memory (structured, v2) | `<repo>/.jac/facts.jsonl` | **JSONL** |
| Skills (v2) | `~/.jac/skills/*.py`, `<repo>/.jac/skills/*.py` | **Python** |

**Unified standards:** TOML for typed app config (Python-ecosystem-native, matches `pyproject.toml` familiarity); YAML for declarative specs (matches Pydantic AI's `Agent.from_spec()` format); JSON/JSONL for machine state; Markdown for human-authored prose; dotenv for secrets. Do not mix formats within a category.

### Layered config precedence (highest → lowest)

1. CLI arguments (`--model anthropic:claude-opus-4-6`)
2. Environment variables (`JAC_MODEL=...`)
3. Project config (`<repo>/.jac/config.toml`)
4. User config (`~/.jac/config.toml`)
5. Package defaults (`src/jac/defaults.toml` — *non-required* values only)

### Workspace layout

```text
~/.jac/                   # user workspace (cross-project)
├── config.toml
├── prompts/              # overrides for shipped defaults
├── minions/templates/
├── skills/               # v2
└── history               # prompt-toolkit input history

<repo>/.jac/              # project workspace (per repo, typically gitignored)
├── config.toml
├── PROJECT.md
├── prompts/              # project-level prompt overrides
├── minions/templates/    # project-level minion templates
├── skills/               # v2
└── sessions/<timestamp>/
    └── messages.json
```

Project-level files **shadow** user-level files of the same name; user-level files shadow package defaults. Sessions live only at project scope.

See `.env.template` for the canonical list of environment variables — keep it in sync when adding new tunables.

## Architecture — non-negotiables

Structural rules every change must respect. Full rationale lives in `ARCHITECTURE.md`; this is the cheat sheet.

### Fail-first, no hardcoding

- Every path, model, provider, and prompt must be configurable through the layered config above.
- Required config that's missing **raises `JacConfigError`** with a message telling the user exactly how to fix it. Never silently default to something that costs money or behaves unexpectedly.
- Paths are derived constants (one source of truth in `jac.workspace.paths` once Phase 0.5 lands), not strings sprinkled across modules.
- "Silent fallback to a safe default" is forbidden. Be loud, be explicit.

### Capabilities are the atom of the system

Almost every cross-cutting concern is a Pydantic AI `Capability`, not a hand-rolled class. Tools, memory tiers, telemetry, the minion factory, sandboxing, even the CLI event bus — all capabilities. **If you find yourself writing a class that hooks into the agent lifecycle without being a Capability, you are probably wrong.** See ARCHITECTURE.md §2.

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

Minions are short-lived agents loaded from declarative YAML specs in `src/jac/minions/templates/` (package defaults) with overrides at `~/.jac/minions/templates/` and `<repo>/.jac/minions/templates/`. They receive a **locked task packet schema** via `deps`:

| Field | Required | Purpose |
| --- | --- | --- |
| `objective` | yes | What the minion must accomplish (one sentence) |
| `success_criteria` | yes | How the minion knows it's done |
| `relevant_files` | no | Files the minion should focus on |
| `forbidden_actions` | no | Specific actions the minion must not perform |
| `expected_output` | yes | Description / JSONSchema of return shape |

Templates may add their own `deps_schema` fields, but these five stay stable. Gru never sees a minion's internal turns — only its structured output.

### Memory: prose first, structured later

Project memory starts as a single `PROJECT.md` (prose), auto-injected via a `ProjectMemory` capability's `get_instructions()`. Structured `facts.jsonl` is added **only when prose retrieval gets noisy** — memory management is a last resort, not a first move. Session memory lives under `<repo>/.jac/sessions/<timestamp>/` (folder-per-session, timestamp-named, human-readable).

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
- Interactive onboarder for first-run setup

The full roadmap is `ARCHITECTURE.md` §9; the live tracker is `PROGRESS.md`.

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
- When you start or finish a piece of work, **update `PROGRESS.md`**.
- Don't accumulate undocumented architectural debt.
