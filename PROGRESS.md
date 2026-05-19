# JAC — Implementation Progress

> **Updated:** 2026-05-19 · keep this in sync as work lands.

This file tracks **what is implemented**, **what is in flight**, and **what is queued**.
For the *why* see `IDEA.md`. For the *how* see `ARCHITECTURE.md` and `CLAUDE.md`.

## Status summary

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 0 — Skeleton | ✅ Complete | bare CLI + Gru, Logfire wired, no tools |
| Phase 0.5 — Config foundation | ⏳ Next | workspace layout, layered config, fail-first |
| Phase 1 — Solo Gru | ⏸ Queued | hooks bus + tools + HITL + session memory |
| Phase 2 — Project memory | ⏸ Queued | `PROJECT.md` + summarizer minion |
| Phase 3 — Minion factory | ⏸ Queued | spec loader + factory + first templates |
| Phase 4 — Quality | ⏸ Queued | CodeMode + stuck-loop + tests + docs |
| v2 | ⏸ Future | A2A / scheduling / YOLO / user memory / surfaces |

---

## Phase 0 — Skeleton ✅

**Goal:** smallest possible working JAC. `uv run jac` opens a REPL and chats with a bare Gru.

- [x] Project scaffold + `uv` + `pyproject.toml` (build system + console script entry)
- [x] Logfire instrumentation (`logfire.configure(send_to_logfire="if-token-present")` + `instrument_pydantic_ai`)
- [x] Typer CLI entry (`jac`, `python -m jac`)
- [x] Prompt-toolkit + rich REPL with input history at `~/.jac/history`
- [x] Bare `Gru` agent (no tools, no capabilities yet)
- [x] In-memory message history within a single session
- [x] `.env.template` documenting all supported provider keys
- [x] Fail-first when no model is configured (`JacConfigError`)

**Phase 0 known gaps that Phase 0.5 closes:**

- Gru's system prompt is loaded only from the installed package. Should support user/project workspace overrides (`~/.jac/prompts/`, `<repo>/.jac/prompts/`).
- No `~/.jac/` or `<repo>/.jac/` workspace bootstrap.
- No TOML config file loading.

---

## Phase 0.5 — Config foundation ⏳

**Goal:** establish the workspace + layered config + format conventions **before** any Phase 1 code lands. No more hardcoded defaults.

- [ ] Define `~/.jac/` and `<repo>/.jac/` workspace layouts (doc landed in ARCHITECTURE.md §11 D11)
- [ ] Format-convention doc landed (CLAUDE.md "Configuration & workspace")
- [ ] Layered settings loader: package defaults → user TOML → project TOML → env → CLI args
- [ ] Ship a package `defaults.toml` (with **no** required values pre-filled — fail-first)
- [ ] `JacConfigError` raised with actionable messages for any missing required value
- [ ] Layered prompt loader: project `<repo>/.jac/prompts/` → user `~/.jac/prompts/` → package `src/jac/prompts/`
- [ ] First-run bootstrap: silently create `~/.jac/` skeleton if missing (interactive onboarder is v2 polish)
- [ ] Workspace path resolver (one source of truth: `jac.workspace.paths`)
- [ ] Smoke-test: model can be set via env, project TOML, user TOML, or CLI; precedence is correct

---

## Phase 1 — Solo Gru ⏸

Starts after Phase 0.5. Recommended order: build the event bus **before** the tools, so tools slot into a stable architecture.

- [ ] Hooks capability — push lifecycle events to an `asyncio.Queue` (the event bus)
- [ ] CLI renderer consumes the queue (replaces direct `await gru.run` in `repl.py`)
- [ ] `JacTool` decorator enforcing the `reason: str` first-arg requirement
- [ ] Wrapper toolset that rejects tools missing `reason: str` at agent construction
- [ ] Filesystem capability: `read_file`, `write_file`, `edit_file`, `list_dir`
- [ ] Shell capability: `run_shell` (HITL-gated)
- [ ] Search capability: `grep`, `glob`
- [ ] `ApprovalRequiredToolset` wired for risky tools
- [ ] Approval prompt UI in CLI (renders `reason` alongside args)
- [ ] Session persistence at `<repo>/.jac/sessions/<timestamp>/messages.json`
- [ ] `ProcessHistory` window management
- [ ] Resume support: `jac --resume <session-id>` or auto-resume last

## Phase 2 — Project memory ⏸

- [ ] `ProjectMemory` capability — inject `PROJECT.md` via `get_instructions()`
- [ ] Summarizer minion (first minion) at session close
- [ ] Append delta back to `PROJECT.md`

## Phase 3 — Minion factory ⏸

- [ ] Minion runner — load YAML spec, instantiate via `Agent.from_spec()`, run with task packet
- [ ] `MinionFactory` capability exposing `spawn_minion(reason, template, task)`
- [ ] First three templates: `researcher.yaml`, `builder.yaml`, `reviewer.yaml`
- [ ] Playbook docstring on `spawn_minion` — Gru's decision guidance

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
- [ ] Interactive onboarder for first-run setup
- [ ] Browser / API / SDK surfaces

---

## How to use this file

- When you start a task, change `- [ ]` to `- [~]` (in flight).
- When you finish, `- [x]` and a one-line note if anything deviated from the plan.
- When a new task surfaces, add it to the relevant phase or "v2" — don't let it float.
- Architectural decisions go in `ARCHITECTURE.md §11`, not here. This file is *what*, not *why*.
