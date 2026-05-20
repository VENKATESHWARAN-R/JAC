# JAC — Implementation Progress

> **Updated:** 2026-05-19 · keep this in sync as work lands.

This file tracks **what is implemented**, **what is in flight**, and **what is queued**.
For the *why* see `IDEA.md`. For the *how* see `ARCHITECTURE.md` and `CLAUDE.md`.

## Status summary

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 0 — Skeleton | ✅ Complete | bare CLI + Gru, Logfire wired, no tools |
| Phase 0.5 — Config foundation | ✅ Complete | workspace, layered config, AGENTS.md, `jac init` |
| Phase 1 — Solo Gru | ✅ Complete | event bus, tools, HITL, session persistence + resume |
| Phase 1.5 — Profiles & secrets | ✅ Complete | multi-profile config, keyring/dotenv/env-only backends, `jac profiles`/`jac keys` |
| Phase 2 — Project memory | ⏸ Queued | richer AGENTS.md write-back via summarizer minion |
| Phase 3 — Minion factory | ⏸ Queued | spec loader + factory + first templates |
| Phase 4 — Quality | ⏸ Queued | CodeMode + stuck-loop + tests + docs |
| v2 | ⏸ Future | A2A / scheduling / YOLO / user memory tier / surfaces |

---

## Phase 0 — Skeleton ✅

**Goal:** smallest possible working JAC. `uv run jac` opens a REPL and chats with a bare Gru.

- [x] Project scaffold + `uv` + `pyproject.toml` (build system + console script entry)
- [x] Logfire instrumentation (`logfire.configure(send_to_logfire="if-token-present")` + `instrument_pydantic_ai`)
- [x] Typer CLI entry (`jac`, `python -m jac`)
- [x] Prompt-toolkit + rich REPL
- [x] Bare `Gru` agent (no tools, no capabilities yet)
- [x] In-memory message history within a single session
- [x] `.env.template` documenting all supported provider keys
- [x] Fail-first when no model is configured (`JacConfigError`)

## Phase 0.5 — Config foundation ✅

**Goal:** establish workspace + layered config + format conventions before any Phase 1 code touches the filesystem.

- [x] Workspace layout: user at `~/.jac/`, project at `<repo>/.agents/`, project context at `<repo>/AGENTS.md` (community convention)
- [x] Format-convention table locked: YAML for human-edited structured data, JSON for state, Markdown for prose, dotenv for secrets
- [x] Layered settings loader: package defaults → user YAML → project YAML → env → CLI (`jac.workspace.config_loader`)
- [x] Shipped `src/jac/defaults.yaml` (intentionally empty — fail-first means no required defaults)
- [x] `JacConfigError` with actionable messages everywhere a required value is missing
- [x] Layered prompt loader (`jac.workspace.prompts`) — project → user → package, first hit wins
- [x] AGENTS.md auto-loader (`jac.workspace.context`) — concatenates user + project context into Gru's instructions
- [x] First-run silent bootstrap (`jac.workspace.bootstrap.ensure_user_workspace`) — idempotent, creates skeleton + template files
- [x] Workspace path resolver (`jac.workspace.paths`) — one source of truth for every path
- [x] `jac init` interactive wizard (`jac.cli.init`) — provider + model + config write with confirmation
- [x] Multi-command Typer app (`jac`, `jac init`)
- [x] Settings made lazy via `get_settings()` so bootstrap can run first
- [x] History file moved under `~/.jac/history` (was already there via prompt-toolkit; now path-resolved)
- [x] Docs updated: CLAUDE.md, ARCHITECTURE.md §11 (D4, D10, D11), PROGRESS.md

---

## Phase 1 — Solo Gru ⏳

**Recommended order:** event bus **before** tools. The bus is the architectural inversion; every later piece slots into it cleanly.

### Step 1: event bus + tool guard ✅

- [x] `Hooks` capability emitting `JacEvent`s onto an `asyncio.Queue` (`jac.capabilities.hooks.make_hooks`)
- [x] `EventBus` (`jac.runtime.bus`) + typed event dataclasses (`jac.runtime.events`)
- [x] `CliRenderer` consumes the bus and draws status + final markdown (`jac.cli.renderer`)
- [x] `repl.py` runs the agent in a background task while the renderer consumes events concurrently — direct `await gru.run` no longer in the CLI control flow
- [x] `build_gru(extra_capabilities=...)` parameter so the CLI wires hooks without touching the function's defaults
- [x] `@jac_tool` decorator enforcing `reason: str` as the first non-ctx parameter (fail at decoration time)

### Step 2: first tools + HITL ✅

- [x] `jac_function_toolset` enforces `@jac_tool` on every member at construction (`jac.tools.toolset`)
- [x] `@jac_tool` resolves PEP 563 string annotations (`from __future__ import annotations` no longer fools it)
- [x] Filesystem capability: `read_file`, `write_file`, `edit_file`, `list_dir` — write + edit are approval-required (`jac.capabilities.filesystem`)
- [x] Search capability: `grep`, `glob` — read-only (`jac.capabilities.search`)
- [x] Shell capability: `run_shell` — always approval-required (`jac.capabilities.shell`)
- [x] `resolve_under_project` helper — project-relative paths anchor to the git root
- [x] `HandleDeferredToolCalls`-based approval handler that emits `ApprovalRequest` events with embedded futures (`jac.capabilities.approval`)
- [x] `ApprovalRequest` / `ApprovalResponse` event types; bus is now bidirectional via the future
- [x] CLI renderer prompts for approval inline (panel with `reason` + args; pauses spinner)
- [x] `build_gru` ships default tool capabilities (fs / search / shell) with an `include_default_tools=False` escape hatch
- [x] Updated `gru_system.md` so Gru knows what tools it has and the discipline around `reason`

### Step 3: persistence ✅

- [x] `Session` class wrapping disk persistence (`jac.runtime.session`) — folder-per-session under `<repo>/.agents/sessions/<timestamp>/`, `messages.json` via `ModelMessagesTypeAdapter`
- [x] Save after every completed turn (mid-turn kills don't lose prior turns)
- [x] `Session.resume(id)`, `Session.resume_latest()`, `Session.list_ids()`, `Session.latest_id()`
- [x] CLI flags `--resume / -r` (latest) and `--session / -s ID` (specific)
- [x] `jac sessions` subcommand lists ids oldest → newest with a "(latest)" marker
- [x] Greeting shows session id and "(resumed, N prior messages)" / "(new)"
- [x] `ProcessHistory`-based exchange-aware sliding window (`jac.capabilities.history`) — slices on user-prompt boundaries so tool-call/return pairs stay paired; default cap 40 exchanges
- [x] History capability included in `_default_tool_capabilities` so every session gets it for free
- [x] Fail-first when resuming a missing id or `--resume` with no sessions

## Phase 1.5 — Profiles & secrets ✅

**Goal:** stop the "re-export every terminal" friction. Let users configure multiple providers as named profiles and store credentials securely.

- [x] `Profile` model + `~/.jac/config.yaml` schema (`profiles:`, `default_profile:`, `secrets.backend:`) (`jac.profiles`)
- [x] Strict profile-name validation: `[a-z0-9-]+`, no leading/trailing hyphen
- [x] Provider → required env vars map; auto-inferred from `model:` prefix, overridable
- [x] Three secrets backends: `keyring` (OS-native, default), `dotenv` (`~/.jac/.env`, chmod 600), `env-only` (read-through, no storage) (`jac.secrets`)
- [x] Resolution layering: process env > backend > fail-first with actionable message
- [x] `apply_profile_env(name, profile)` injects `JAC_MODEL` + non-secret env + resolved secrets into `os.environ` before REPL starts
- [x] `jac init` rewritten: secrets-backend pick (first run), provider, model, name, env-scan-with-explicit-prompt, optional default
- [x] `jac profiles` / `jac profiles list` / `jac profiles use NAME` / `jac profiles remove NAME`
- [x] `jac keys` / `jac keys list` / `jac keys set KEY` (interactive prompt, no `--value`) / `jac keys unset KEY`
- [x] `--profile / -p` flag on the root command; `--model` continues to bypass profile machinery
- [x] Old top-level `model:` field dropped (hard cutover; YAML rewritten on `jac init`)
- [x] `keyring>=25.0` added as a hard dep

## Phase 2 — Project memory ⏸

Project context already auto-loads from `<repo>/AGENTS.md` since Phase 0.5. Phase 2 adds dynamic write-back.

- [ ] `ProjectMemoryCapability` — formal capability wrapping the existing context loader + write path
- [ ] Summarizer minion (first minion!) at session close
- [ ] Append-delta back into `<repo>/AGENTS.md` (or a managed section thereof)

## Phase 3 — Minion factory ⏸

- [ ] Minion runner — load YAML spec, instantiate via `Agent.from_spec()`, run with task packet
- [ ] `MinionFactory` capability exposing `spawn_minion(reason, template, task)`
- [ ] First three templates: `researcher.yaml`, `builder.yaml`, `reviewer.yaml`
- [ ] Playbook docstring on `spawn_minion` — Gru's delegation guidance

## Phase 4 — Quality ⏸

- [ ] CodeMode integration (`pydantic-ai-harness`)
- [ ] Stuck-loop detection
- [ ] Test suite (pytest)
- [ ] Ruff / mypy config
- [ ] User docs

## v2 ⏸

- [ ] YOLO mode + sandboxing (Monty + sandbox-exec / bwrap + Git-Clean Guard)
- [ ] A2A — `fasta2a` outbound, bespoke HTTP client toolset inbound
- [ ] Night Shift / cron scheduling
- [ ] User-tier memory + predict-calibrate extraction
- [ ] Agent-authored skills
- [ ] Richer `jac init` (tier-based models, project workspace setup, key-validation)
- [ ] Browser / API / SDK surfaces

---

## How to use this file

- When you start a task, change `- [ ]` to `- [~]` (in flight).
- When you finish, `- [x]` and a one-line note if anything deviated from the plan.
- When a new task surfaces, add it to the relevant phase or "v2" — don't let it float.
- Architectural decisions go in `ARCHITECTURE.md §11`, not here. This file is *what*, not *why*.
