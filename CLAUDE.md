# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**JAC** (**J**ust **A**nother **C**ompanion/CLI) is an agentic harness built on Pydantic AI. It runs on the user's machine and wraps an LLM with persistent memory, tools, orchestration, and session continuity. See [`docs/idea.md`](docs/idea.md) for product vision and scope.

## Project state

See `docs/progress.md` for what's implemented, what's in flight, and what's queued. **Update it as work lands.**

For the *why*, read `docs/idea.md`. For the *how*, read `docs/architecture.md`. When the design is ambiguous, **`docs/architecture.md` is the source of truth for *how* JAC is built**; **`docs/idea.md` is the source of truth for *what it is and is not*.** If you deviate from either, update the doc in the same change.

All long-form design docs live under [`docs/`](docs/) and are published as a Zensical site (`just docs-serve`). `README.md`, `CLAUDE.md`, and `LICENSE` stay at the repo root.

## Stack

- **Python 3.13**, managed with `uv` (`uv.lock` committed).
- **Pydantic AI** (`pydantic-ai-slim` with anthropic/openai/google/openrouter/mistral providers, plus duckduckgo, fastmcp, evals).
- **Logfire** for tracing — every model call, tool call, minion spawn, and memory write must be instrumented.
- **typer + rich + prompt-toolkit** for the CLI surface.
- **fasta2a** for A2A (v2; server-side only — client side is a bespoke HTTP toolset we write).
- **pydantic-settings[yaml]** for layered config.

## Commands

Day-to-day commands are wrapped in a [`justfile`](justfile) — `just` to list
recipes, `just check`, `just fix`, `just typecheck`, `just docs-serve`,
`just docs-build`, `just run -- <args>` (passes through to `uv run --env-file
.env jac`). The raw equivalents:

```bash
uv sync                          # install / refresh dependencies
uv run jac                       # interactive REPL with the default profile
uv run jac --profile NAME        # one-shot profile selection
uv run jac --model PROVIDER:ID   # raw model override (bypasses profiles)
uv run jac --resume              # resume the latest project session
uv run jac --session ID          # resume a specific session by id

uv run jac init                  # wizard: backend + profile + key storage
uv run jac profiles              # list profiles, mark default
uv run jac profiles use NAME     # set default profile
uv run jac profiles remove NAME  # delete a profile
uv run jac keys                  # show required keys with status
uv run jac keys set KEY          # prompt and store in configured backend
uv run jac keys unset KEY        # delete from backend
uv run jac sessions              # list sessions in this project

uv run jac --help                # full CLI help
uv run python -m jac             # equivalent invocation
```

**No required runtime values are defaulted in code.** Set them via `jac init`, env vars, or the `--model` flag. See `.env.template` for env-var examples.

## Configuration & workspace

### File-format conventions (locked)

| What | Location | Format |
| --- | --- | --- |
| Secrets (API keys, tokens) | `.env`, env vars | **dotenv** |
| App config | `~/.jac/config.yaml`, `<repo>/.agents/config.yaml` | **YAML** |
| Provider catalog | `src/jac/data/providers.yaml` (package), `~/.jac/providers.yaml` (user overlay) | **YAML** |
| Agent / minion specs | `~/.jac/minions/templates/*.yaml`, `<repo>/.agents/minions/templates/*.yaml` | **YAML** |
| System prompts | `~/.jac/prompts/*.md`, `<repo>/.agents/prompts/*.md` | **Markdown** |
| Project context (auto-loaded) | `<repo>/AGENTS.md` (at repo root, community convention) | **Markdown** |
| User context (auto-loaded) | `~/.jac/AGENTS.md` | **Markdown** |
| Session message history | `<repo>/.agents/sessions/<ts>/messages.json` | **JSON** |
| Project memory (JAC-managed, auto-loaded) | `<repo>/.agents/memory.md` | **Markdown** |
| User memory (JAC-managed, auto-loaded) | `~/.jac/memory.md` | **Markdown** |
| Project memory (structured, v2) | `<repo>/.agents/facts.jsonl` | **JSONL** |
| Skills (v2) | `~/.jac/skills/*.py`, `<repo>/.agents/skills/*.py` | **Python** |

**Unified standards:** one format per category. YAML covers everything human-edited and structured (config + specs). JSON / JSONL is machine state. Markdown is prose. dotenv is secrets. Don't mix.

### Layered config precedence (highest → lowest)

1. CLI arguments (`--model ...`, `--profile NAME`)
2. Environment variables (`JAC_MODEL=...`, `JAC_SECRETS__BACKEND=...`)
3. `.env` file in CWD
4. Project config (`<repo>/.agents/config.yaml`)
5. User config (`~/.jac/config.yaml`)
6. Package defaults (`src/jac/data/defaults.yaml` — *non-required* values only)

Implementation lives in `jac.workspace.config_loader`. Missing required values raise `JacConfigError` at point of use.

### Profiles & secrets

User-facing config is organized into **profiles**. Each profile binds a model + optional non-secret env (e.g. `OLLAMA_BASE_URL`) and inherits required secret-key names from the model's provider prefix (defined in the **provider catalog** — shipped `src/jac/data/providers.yaml`, overridable via `~/.jac/providers.yaml`). Schema:

```yaml
default_profile: claude
profiles:
  claude:
    model: anthropic:claude-sonnet-4-5
  ollama-local:
    model: ollama:gemma3:e2b
    env:
      OLLAMA_BASE_URL: http://localhost:11434/v1
secrets:
  backend: keyring   # keyring | dotenv | env-only
```

Credentials resolve at REPL startup in this order:

1. Process env (whatever the shell exported wins — direnv / 1Password CLI / CI overrides are honored).
2. Configured backend: `keyring` (OS keychain, default), `dotenv` (`~/.jac/.env`, chmod 600), or `env-only` (read-through; no storage).
3. **Fail-first** with an actionable error pointing at `jac keys set KEY`.

Profile activation lives in `jac.secrets.apply_profile_env` and writes `os.environ` so pydantic-ai's normal provider construction stays unchanged — no custom provider plumbing.

### Workspace layout

```text
~/.jac/                       # user workspace (JAC-private, cross-project)
├── config.yaml
├── providers.yaml            # optional overlay on package providers.yaml
├── providers.yaml.example    # commented template (first-run bootstrap)
├── AGENTS.md                 # user-level context (user-authored), auto-loaded
├── memory.md                 # user-level JAC-managed memory, written via `remember`
├── prompts/                  # overrides for shipped prompts
├── minions/templates/
├── skills/                   # v2
└── history                   # prompt-toolkit input history

<repo>/AGENTS.md              # project context at REPO ROOT (community convention,
                              # not inside .agents/) — auto-loaded if present
<repo>/.agents/               # JAC project workspace (community-neutral dir name)
├── config.yaml
├── memory.md                 # JAC-managed project memory, written via `remember`
├── prompts/                  # project-level prompt overrides
├── minions/templates/        # project-level minion templates
├── skills/                   # v2
└── sessions/<timestamp>/
    └── messages.json
```

Project-level files **shadow** user-level files of the same name; user-level files shadow package defaults. Sessions live only at project scope. `AGENTS.md` is intentionally at the repo root (not inside `.agents/`) to match the community convention — other tools that read `AGENTS.md` find it where they expect.

See `.env.template` for the canonical list of environment variables — keep it in sync when adding new tunables.

## Architecture — non-negotiables

Structural rules every change must respect. Full rationale in `docs/architecture.md`; this is the cheat sheet.

### Fail-first, no hardcoding

- Every path, model, provider, and prompt must be configurable through the layered config above.
- Required config that's missing **raises `JacConfigError`** with a message telling the user exactly how to fix it. Never silently default to something that costs money or behaves unexpectedly.
- Paths are derived constants — one source of truth in `jac.workspace.paths`, never strings sprinkled across modules.
- "Silent fallback to a safe default" is forbidden. Be loud, be explicit.

### Capabilities are the atom of the system

Almost every cross-cutting concern is a Pydantic AI `Capability`, not a hand-rolled class. Tools, memory tiers, telemetry, the minion factory, sandboxing, even the CLI event bus — all capabilities. **If you find yourself writing a class that hooks into the agent lifecycle without being a Capability, you are probably wrong.** See docs/architecture.md §2.

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

Minions are short-lived agents loaded from declarative YAML specs. Resolution order (first hit wins): `<repo>/.agents/minions/templates/`, `~/.jac/minions/templates/`, `src/jac/minions/templates/` (package defaults). They receive a **locked task packet schema** via `deps`:

| Field | Required | Purpose |
| --- | --- | --- |
| `objective` | yes | What the minion must accomplish (one sentence) |
| `success_criteria` | yes | How the minion knows it's done |
| `relevant_files` | no | Files the minion should focus on |
| `forbidden_actions` | no | Specific actions the minion must not perform |
| `expected_output` | yes | Description / JSONSchema of return shape |

Templates may add their own `deps_schema` fields, but these five stay stable. Gru never sees a minion's internal turns — only its structured output.

### Memory: prose first, structured later

Memory follows a **2×2 matrix** — user / project × user-authored / JAC-managed:

|                          | User scope            | Project scope                    |
| ------------------------ | --------------------- | -------------------------------- |
| User-authored (we read)  | `~/.jac/AGENTS.md`    | `<repo>/AGENTS.md`               |
| JAC-managed (we write)   | `~/.jac/memory.md`    | `<repo>/.agents/memory.md`       |

**Read side.** All four files are auto-loaded into Gru's instructions on session start (when present), in the order user-AGENTS → user-memory → project-AGENTS → project-memory, so project specifics dominate and the freshest JAC-learned facts come last.

**Write side.** Gru persists durable facts via the **HITL-gated `remember(reason, content, category, scope)` tool** (Phase 2a / 2a.1) and removes them via the symmetric **`forget(reason, content, scope)`**. Categories are a fixed enum — `convention / fact / preference / gotcha / decision`. `scope` is required: `"user"` for cross-project facts, `"project"` for repo-specific facts. `scope="project"` outside a git repo raises `JacConfigError` rather than scribbling into CWD. Each entry carries `<!-- jac: <timestamp> session: <id> -->` for audit, written atomically, de-duped against the target section (loud rejection — Gru is told, not silently dropped). A soft "consider pruning" warning surfaces past ~25 entries per section. We **never** write to either `AGENTS.md` — those are owned by the user.

The summarizer minion (Phase 2b, queued) will propose additional deltas at session close, but routes them through the same `remember` approval path — it never writes directly. Structured `facts.jsonl` (v2) is added **only when prose retrieval gets noisy** — memory management is a last resort, not a first move. Session memory lives under `<repo>/.agents/sessions/<timestamp>/` (folder-per-session, timestamp-named, human-readable).

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
- Richer onboarding (tier-based models, per-project setup) — v1 `jac init` does just provider + model

The full roadmap is `docs/architecture.md` §9; the live tracker is `docs/progress.md`.

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

- When you make a structural decision, **update `docs/architecture.md` in the same change** — preferably by extending the decisions table in §11.
- When the vision or scope shifts, **update `docs/idea.md`**.
- When you start or finish a piece of work, **update `docs/progress.md`**.
- Don't accumulate undocumented architectural debt.
