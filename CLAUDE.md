# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**JAC** (**J**ust **A**nother **C**ompanion/CLI) is an agentic harness built on Pydantic AI. See [`docs/idea.md`](docs/idea.md) for product vision and scope.

## Core philosophy

**JAC is orchestration around an intelligent layer.** The LLM is the *brain*; JAC builds the nervous system, eyes, hands, and feet — tools, memory, sub-agents, hooks, prompt assembly. The model only thinks. Everything around it determines whether thinking is *cheap and effective* or *expensive and wasteful*.

**Cost is the metric.** Cost ≈ Σ over turns of `turn_tokens × turn_price`. Every architectural decision is judged against this equation. Our job is to **feed the right amount of information to the right model at the right time** to produce the right result.

Five levers we actually pull (full design: [`docs/design/cost-efficient-orchestration.md`](docs/design/cost-efficient-orchestration.md)):

1. **Sub-agents** — delegate context-heavy work to an isolated agent so the main loop's history doesn't bloat. The intermediate 50k–200k tokens stay in the sub-agent's context; only the result returns to the main agent.
2. **Tier-aware model selection** — small / medium / large per profile. Small handles bulk summarization and quick lookups; large is reserved for hard reasoning. The main agent picks a *tier*, never a specific model.
3. **Tool result post-processor** — when any tool returns above a threshold *and* a small tier is configured, route the result through the small model to extract + summarize before it enters the main loop. Original saved to disk; agent can re-read on demand.
4. **Cache-friendly prompt assembly** — order system prompt + tools + memory + history so the cache breakpoint sits at the stable/changing boundary. Cache hits on Anthropic cost 10% of input.
5. **Deterministic hooks** — post-flight validators (typecheck, tests, lint) that run after a sub-agent finishes. If all pass, the sub-agent's response returns verbatim — no extra LLM turn. If any fails, the failure routes back into the same sub-agent's loop for a fix (bounded retries).

**Anti-patterns to refuse on sight:**

- Letting the main agent's context grow turn-by-turn with tool results it doesn't need to reason over.
- Calling an LLM to validate something a deterministic check can answer (`pytest --exitcode` beats "did the tests pass?").
- Adding "another agent type" or "another runtime mode" when the answer is one more tool the existing agent calls.
- Optimizing model choice while leaving raw 200KB tool outputs unfiltered.
- Inventing bespoke skill formats when the community Anthropic format already covers it.

## Project state

Start with [`docs/progress.md`](docs/progress.md) for the live dashboard: what's implemented, what's active, and what should happen next. **Update it as work lands.** If you need deeper context, use the split progress archives: [`docs/progress-history.md`](docs/progress-history.md) for completed-phase detail, [`docs/progress-a2a.md`](docs/progress-a2a.md) for the detailed A2A log, and [`docs/progress-roadmap.md`](docs/progress-roadmap.md) for extended queued/future context.

For the *why*, read [`docs/idea.md`](docs/idea.md). For the *how*, read [`docs/architecture.md`](docs/architecture.md). When the design is ambiguous, **`docs/architecture.md` is the source of truth for *how* JAC is built**; **`docs/idea.md` is the source of truth for *what it is and is not*.** If you deviate from either, update the doc in the same change.

Long-form docs live under [`docs/`](docs/) and are published as a Zensical site (`just docs-serve`). **Before placing a new file or moving an existing one, read [`docs/developer/module-strategy.md`](docs/developer/module-strategy.md)** — it's the canonical rulebook for where things live and the slash-vs-capability distinction. For the as-built tree see [`docs/developer/codebase-map.md`](docs/developer/codebase-map.md); for contributing workflow see [`docs/developer/contributing.md`](docs/developer/contributing.md).

## Stack

- **Python 3.13**, managed with `uv` (`uv.lock` committed).
- **Pydantic AI** (`pydantic-ai-slim` with anthropic/openai/google/openrouter/mistral providers, plus duckduckgo, fastmcp, evals).
- **Logfire** for tracing — every model call, tool call, sub-agent spawn, and memory write must be instrumented.
- **typer + rich + prompt-toolkit** for the CLI surface.
- **fasta2a** for A2A (Phase 4; server-side only — outbound is a bespoke HTTP toolset).
- **pydantic-settings[yaml]** for layered config.

## Commands

Day-to-day commands are wrapped in a [`justfile`](justfile) — `just check`, `just fix`, `just docs-serve`, `just run -- <args>`. Full recipe list: [`docs/developer/contributing.md`](docs/developer/contributing.md).

Use `uv` for all Python execution in this repo. Prefer `just` recipes for checks and docs. Do **not** run bare `python`, `pip`, `pytest`, `ruff`, or `ty` unless there is a specific reason; use `uv run ...` or the matching `just` recipe so the project environment from `uv.lock` is active.

```bash
uv sync                          # install / refresh dependencies
uv run python -m pytest           # run pytest inside the uv-managed environment
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
| MCP server catalog (community `mcpServers` shape — D46) | `~/.jac/mcp.json`, `<repo>/.agents/mcp.json` | **JSON** (interop artifact, *deliberate* exception to "YAML for human config" — see D46) |
| System prompts | `~/.jac/prompts/*.md`, `<repo>/.agents/prompts/*.md` | **Markdown** |
| Project context (auto-loaded) | `<repo>/AGENTS.md` (at repo root, community convention) | **Markdown** |
| User context (auto-loaded) | `~/.jac/AGENTS.md` | **Markdown** |
| Session message history | `<repo>/.agents/sessions/<ts>/messages.json` | **JSON** |
| Session plan checklist (D27) | `<repo>/.agents/sessions/<ts>/plan.json` | **JSON** |
| Project memory (JAC-managed, auto-loaded) | `<repo>/.agents/memory.md` | **Markdown** |
| User memory (JAC-managed, auto-loaded) | `~/.jac/memory.md` | **Markdown** |
| Per-session token usage (D25 budgets) | `<repo>/.agents/usage.jsonl` | **JSONL** |
| Tool-result cache (Phase A post-processor) | `<repo>/.agents/cache/tool-results/<session-id>/<call-id>.txt` | **plain text** |
| A2A context threads (D24) | `<repo>/.agents/a2a/contexts/<context_id>.json` | **JSON** |
| A2A inbound audit log (D24) | `<repo>/.agents/a2a/inbound.jsonl` | **JSONL** |
| A2A received files (D33) | `<repo>/.agents/a2a/inbound-files/<task_id>/<file>` | binary |
| A2A guest-upload files (D33) | `<repo>/.agents/a2a/guest-uploads/<context_id>/<file>` | binary |
| Secrets backend (dotenv mode) | `~/.jac/.env` (`chmod 600`) | **dotenv** |
| REPL command history | `~/.jac/history` | prompt-toolkit `FileHistory` |

**Unified standards:** one format per category. YAML for human-edited structured data; JSON / JSONL for machine state; Markdown for prose; dotenv for secrets.

### Layered config precedence (highest → lowest)

1. CLI arguments (`--model ...`, `--profile NAME`)
2. Environment variables (`JAC_MODEL=...`, `JAC_SECRETS__BACKEND=...`)
3. `.env` file in CWD
4. Project config (`<repo>/.agents/config.yaml`)
5. User config (`~/.jac/config.yaml`)
6. Package defaults (`src/jac/data/defaults.yaml` — *non-required* values only)

Implementation lives in `jac.workspace.config_loader`. Missing required values raise `JacConfigError` at point of use. Profiles, tiers, compaction, budgets, secrets: [`docs/user-guide/configuration.md`](docs/user-guide/configuration.md). Memory read/write paths: [`docs/user-guide/sessions-and-memory.md`](docs/user-guide/sessions-and-memory.md).

### Changing config schema

When you touch any field on `Settings` (or its sub-models like `CostSettings`) or any key in `defaults.yaml`, pick the right path — they are not the same kind of change:

- **Default-value flip** (e.g. `sub_agent_bidirectional: false → true`): change `src/jac/config.py` + `src/jac/data/defaults.yaml` and update the docs. **No migration code.** Pydantic-settings merges YAML sources field-level, so any key absent from a user's `~/.jac/config.yaml` falls through to `defaults.yaml` and the new value lands on upgrade. Users who explicitly set the old value keep it — that's correct: they made a deliberate choice.
- **Schema shape change** (field rename, removal, new *required* field, type change, restructuring): add an idempotent migration alongside `migrate_old_profiles` in [`src/jac/profiles_io.py`](src/jac/profiles_io.py), wire it into `_run_pending_migrations` in [`src/jac/cli/init.py`](src/jac/cli/init.py), and note it in `docs/progress.md`. The migration must detect the old shape, print a panel explaining what's about to change, and be safe to run on every `jac init` (no-op if already migrated).

Either way, update [`docs/user-guide/configuration.md`](docs/user-guide/configuration.md) so the user-visible reference reflects the new default or shape in the same change.

## Architecture — non-negotiables

Structural rules every change must respect. Full rationale in [`docs/architecture.md`](docs/architecture.md); capability patterns in [`docs/developer/capabilities.md`](docs/developer/capabilities.md).

### Fail-first, no hardcoding

- Every path, model, provider, and prompt must be configurable through the layered config above.
- Required config that's missing **raises `JacConfigError`** with a message telling the user exactly how to fix it. Never silently default to something that costs money or behaves unexpectedly.
- Paths are derived constants — one source of truth in `jac.workspace.paths`, never strings sprinkled across modules.
- "Silent fallback to a safe default" is forbidden. Be loud, be explicit.

### Capabilities are the atom of the system

Almost every cross-cutting concern is a Pydantic AI `Capability`, not a hand-rolled class. Tools, memory, telemetry, the sub-agent factory, sandboxing, even the CLI event bus — all capabilities. **If you find yourself writing a class that hooks into the agent lifecycle without being a Capability, you are probably wrong.**

### Hooks are the runtime event bus, not a logging detail

The CLI does not poll the agent. The CLI installs a `Hooks` capability that pushes lifecycle events onto an `asyncio.Queue`; the CLI renderer consumes the queue. All surfaces (CLI today; TUI/web later) reuse the same capability set; only the renderer changes.

### Every tool requires a `reason: str` parameter

Every tool exposed to Gru or a sub-agent **must** accept `reason: str` as its first argument. Enforced structurally via a `@jac_tool` decorator and a wrapper toolset that rejects tools missing the parameter at agent construction (fail-fast, not at runtime).

### HITL is built into Pydantic AI; don't reinvent it

Use `ApprovalRequiredToolset` + the `deferred_tool_calls` hook for approval flows. Do not write a custom approval system.

### Don't reinvent what Pydantic AI already provides

Use built-in or `pydantic-ai-harness` primitives: `ApprovalRequiredToolset`, `deferred_tool_calls`, `ProcessHistory`, `Instrumentation`, `CodeMode`, `ModelMessagesTypeAdapter`, `pydantic_ai.direct.model_request_sync`.

### Tracing fields on every Logfire span

Every span carries: `template`, `task_id`, `parent_run_id`, `token_cost`, `duration`, `exit_status`.

## Active roadmap (post-2026-05-26 reframe)

The roadmap was reframed around the cost-efficiency thesis. Live tracker: [`docs/progress.md`](docs/progress.md). Design spec: [`docs/design/cost-efficient-orchestration.md`](docs/design/cost-efficient-orchestration.md). Old Phase 3/5/6 entries archived: [`docs/progress-archive-2026-05.md`](docs/progress-archive-2026-05.md).

Phases in dependency order:

- **Phase A — Context-cost foundation.** Tool result caps; tool result post-processor (small-tier AI summarization above threshold); cache-friendly prompt assembly; `/tokens` breakdown. **Shipped v0.3.0.**
- **Phase B — Sub-agent tool.** Single `spawn_sub_agent(reason, task_summary, tier, task_packet)` tool. Sequential only. Tier-HITL approval. Depth cap = 1 (no recursive spawning). Logfire parent chain, budget rollup. **Shipped v0.3.0.**
- **Phase C — Deterministic hooks.** **Dropped** — complexity didn't earn its keep; `success_criteria` in the packet + a post-return `run_shell` call cover verification without framework machinery.
- **Phase D — Skill loader.** Anthropic community format. Loadable prompts / playbooks the main agent reads when relevant. Skills are advice — they do NOT carry a runtime-mode declaration (no `mode:` frontmatter, no `mode: minion`, no `mode: planner` etc.). **Shipped v0.4.0.**
- **Phase E — Parallel sub-agents + HITL multiplexing.** Polish on top of B/D. Next up.
- **Phase F — MCP loader + tool search (D28, D46). Shipped 2026-05-29.** External MCP servers (`mcp.json`, standard `mcpServers` shape) wired in as deferred-loaded, HITL-gated, summarized toolsets via `MCPCapability`; `.defer_loading()` + auto `ToolSearch` keeps definitions out of the prompt. `/mcp list|reload|enable|disable`.
- **Phase G — Plan Mode (D23).** Pulled forward from v2; demoted from old Phase F to follow MCP.
- **Phase H — A2A Phase 4.e (OIDC/GCP) + broader test coverage.** Lower priority than A-G.
- **Phase 7 stream — Evaluation via Logfire span replay (D44).** Trajectory tests asserting span shape, not output text. Ongoing; not a numbered phase.
- **Web surface — local-first control panel + chat + dashboard (D48). Slices 1–3 shipped.** A third surface alongside CLI and A2A: a Starlette + HTMX + SSE browser UI under `src/jac/web/` (mirrors `src/jac/cli/`), reached via `jac web serve`. It is a *renderer + management API* over the same engine the CLI drives — **not** a new runtime mode (refuse any change that makes it one). The chat surface reuses the engine via `runtime/bootstrap.py::build_session_runtime`, **extracted from the REPL so both surfaces wire the identical Gru + capabilities + driver** (don't re-duplicate that bootstrap; extend it). **Local-first, single-user by charter: binds `127.0.0.1`, no accounts, never multi-tenant.** The loopback boundary *is* the security model; a non-loopback `--host` is allowed but warns loudly because the settings panel reads and writes API keys in the clear. Session scope follows the launch directory via `paths.project_state_root()` — **project-only in v1** (running in project A shows project A's sessions); cross-project browsing is deliberately deferred. Slices: 1 = config/session control panel (done), 2 = streaming chat + HITL over SSE (done — HITL resolves the same `asyncio.Future` the CLI does, from a browser POST), 3 = activity dashboard (done — token meter + minion cards from `_pending_spawns` + files-changed, via a polled `/chat/status`). Remaining: visual/theming polish. Design: [`docs/design/web-surface.md`](docs/design/web-surface.md).

What's still genuinely v2:

- YOLO mode + sandboxing via direct `pydantic-monty` (D43) + Git-Clean Guard. **Decided 2026-05-27:** adopt the Monty library directly (Rust-written minimal Python interpreter; microsecond cold start; zero-grant default), NOT Docker, NOT `sandbox-exec` / `bwrap`, NOT `pydantic-ai-harness`'s `CodeExecutionToolset` wrapper (which forces a "write code instead of call tools" model that conflicts with JAC's per-tool HITL UX).
- **ACP — editor surface (D45, condition-gated).** `ACPCapability` wrapping the Python [ACP SDK](https://agentclientprotocol.com). ACP is the LSP analogue for coding agents — one spec, any compliant editor. VS Code / Zed / JetBrains extensions become generic ACP clients; JAC writes the server once. **Two conditions before building:** (1) ACP remote HTTP/WebSocket transport stabilises (currently WIP); (2) at least one major editor ships an ACP client. Full design in [`docs/progress-roadmap.md`](docs/progress-roadmap.md) "ACP — Editor surface" section and locked in D45.
- Stuck-loop detection — low value in HITL; mandatory only for YOLO. (Watch Harness PR #186.)
- Night Shift / cron-triggered headless runs.
- User-tier predict-calibrate memory extraction (the `~/.jac/memory.md` *file* already exists per Phase 2a.1; what's deferred is *automatic extraction*).
- Other native SDK / editor surfaces beyond the local web UI (post-ACP).

**Harness alignment policy:** several JAC capabilities overlap with `pydantic-ai-harness` PRs (sub-agents #178, skills #183, compaction #191, post-processor #185, budgets #182, etc.). Today they're PR-tracked in Harness, shipped in JAC. **We keep ours** because they're tightly coupled to JAC's HITL / `/tokens` UX and instrumentation; we revisit migration only when a Harness PR lands a clean stable API. **We don't reinvent** infrastructure with no UX (`pydantic-monty` for sandboxing; pydantic-ai's `ApprovalRequiredToolset`/`deferred_tool_calls`/`Instrumentation`/`ProcessHistory`). Full reuse-vs-build table: [`docs/progress-roadmap.md`](docs/progress-roadmap.md) "Harness alignment" section.

If a task seems to require any v2 item, stop and ask before scaffolding.

**On the "minion" name.** The *old Phase 5 minion runtime* — a separate process / runtime mode with its own factory — is **archived**. What we ship is a single `spawn_sub_agent` tool the main agent calls, plus `spawn_sub_agents` for parallel fan-out. The **vocabulary**, however, stayed: **"minion", "sub-agent", and "worker" are interchangeable terms in this project**, and the user-facing labels (spawn IDs, `/spawns` table, approval panel "who's asking") use `minion-N`. The code API surface stays `spawn_sub_agent`, `SubAgentCapability`, etc. — don't sweep-rename those. Gru's system prompt teaches the model the alias so casual user phrasing routes correctly.

## Documentation discipline

**Where to update what** is defined in [`docs/design/documentation-strategy.md`](docs/design/documentation-strategy.md) — read it before adding or reorganising any doc. The short version:

- Structural decision → `docs/architecture.md` §5 in the same change.
- Vision or scope shift → `docs/idea.md`.
- Work lands → `docs/progress.md` + relevant `docs/user-guide/*.md` page if user-visible.
- **CLI surface changes** (new/renamed Typer command, root flag, slash command, A2A peer auth shape, budget kind, etc.) → update [`docs/user-guide/cli-reference.md`](docs/user-guide/cli-reference.md) **and** the shipped [`src/jac/data/skills/jac-cli/SKILL.md`](src/jac/data/skills/jac-cli/SKILL.md) in the same change. The skill is what Gru consults when the user asks "how do I run X" — drift here means Gru improvises wrong commands.
- **New/moved module, new slash command, new tool** → update [`docs/developer/codebase-map.md`](docs/developer/codebase-map.md) (the as-built tree + tables) in the same change. `just drift` enforces slash-command coverage + version sync; it does **not** catch a moved module, so keep the map honest by hand.
- When updating docs, **replace outdated information** — don't append. One fact, one home.
- Don't accumulate undocumented architectural debt.
