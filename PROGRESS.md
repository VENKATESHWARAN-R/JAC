# JAC — Implementation Progress

> **Updated:** 2026-05-19 · keep this in sync as work lands.

This file tracks **what is implemented**, **what is in flight**, and **what is queued**.
For the *why* see `IDEA.md`. For the *how* see `ARCHITECTURE.md` and `CLAUDE.md`.

## Status summary

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 0 — Skeleton | ✅ Complete | bare CLI + Gru, Logfire wired, no tools |
| Phase 0.5 — Config foundation | ✅ Complete | workspace, layered config, AGENTS.md, `jac init` |
| Phase 1 — Solo Gru | ⏳ Next | hooks bus + tools + HITL + session memory |
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

- [ ] `Hooks` capability — push lifecycle events onto an `asyncio.Queue`
- [ ] CLI renderer consumes the queue (replaces direct `await gru.run` in `repl.py`)
- [ ] `JacTool` decorator enforcing the `reason: str` first-arg requirement
- [ ] Wrapper toolset that rejects tools missing `reason: str` at agent construction
- [ ] Filesystem capability: `read_file`, `write_file`, `edit_file`, `list_dir`
- [ ] Shell capability: `run_shell` (HITL-gated)
- [ ] Search capability: `grep`, `glob`
- [ ] `ApprovalRequiredToolset` wired for risky tools
- [ ] Approval prompt UI in CLI (renders `reason` alongside args)
- [ ] Session persistence at `<repo>/.agents/sessions/<timestamp>/messages.json`
- [ ] `ProcessHistory` window management
- [ ] Resume support: `jac --resume <session-id>` or auto-resume last

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
