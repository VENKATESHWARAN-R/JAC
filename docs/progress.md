# JAC — Implementation Progress

> **Updated:** 2026-05-22 · keep this in sync as work lands.

This file tracks **what is implemented**, **what is in flight**, and **what is queued**.
For the *why* see `idea.md`. For the *how* see `architecture.md` and `CLAUDE.md`.

Each phase block leads with **Goal** + **why/what/how** before the checklist. This is intentional — phases get revisited after long gaps and the rationale must survive without re-derivation. Architectural decisions live in `architecture.md §11`; this file is the *what*, but each phase here should hand you enough *why* that the *how* makes sense.

## Status summary

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 0 — Skeleton | ✅ Complete | bare CLI + Gru, Logfire wired, no tools |
| Phase 0.5 — Config foundation | ✅ Complete | workspace, layered config, AGENTS.md, `jac init` |
| Phase 1 — Solo Gru | ✅ Complete | event bus, tools, HITL, session persistence + resume |
| Phase 1.5 — Profiles & secrets | ✅ Complete | multi-profile config, keyring/dotenv/env-only backends, `jac profiles`/`jac keys` |
| Phase 2a — `remember` tool | ✅ Complete | HITL-gated `remember`, JAC-owned `.agents/memory.md`, fixed category enum, auto-injected into Gru's context |
| Phase 2a.1 — User scope + `forget` | ✅ Complete | `~/.jac/memory.md`, scope-aware `remember`/`forget`, session-id audit trail, soft size warning, fail-first on no-repo |
| Phase 1.6 — Tool surface polish | ✅ Complete | plan, background processes, fs/grep upgrades, web search, clarify (all landed 2026-05-22 after a tool retrospective) |
| Phase 2b — Summarizer minion | ⏸ Queued | proposes deltas at session close, routes through `remember` approval — needs Phase 3 minion infra |
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
- [x] Docs updated: CLAUDE.md, architecture.md §11 (D4, D10, D11), progress.md

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

## Phase 2a — `remember` tool ✅

**Goal:** give Gru a structured, HITL-gated way to persist durable project facts. Avoid touching the user's `AGENTS.md`; write to a JAC-owned `memory.md` instead. Cheap, immediate, low-bloat — the high-signal path.

- [x] `MemoryCapability` + `remember(reason, content, category)` tool (`jac.capabilities.memory`) — HITL-gated, atomic writes, exact-normalized de-dup with loud feedback
- [x] Fixed category enum: `convention` / `fact` / `preference` / `gotcha` / `decision` — predictable file structure, easy de-dup
- [x] `<repo>/.agents/memory.md` lazily bootstrapped from template on first call; `project_memory_file()` in `jac.workspace.paths` is the single source of truth
- [x] Audit trail: every entry carries an HTML-comment timestamp (`<!-- jac: 2026-... -->`)
- [x] Context loader (`jac.workspace.context.load_project_memory`) auto-injects memory.md **after** AGENTS.md so the freshest facts dominate
- [x] `MemoryCapability` wired into `_default_tool_capabilities` — every session gets `remember` for free
- [x] `gru_system.md` updated with the "When to call `remember`" discipline (durable-only, anti-examples)
- [x] Architecture decision recorded as D14; §8 memory-subsystem diagram refreshed

## Phase 2a.1 — User scope + `forget` ✅

**Goal:** complete the 2×2 memory matrix (user/project × authored/JAC-managed) and add the symmetric removal tool. Random-terminal use case now writes to `~/.jac/memory.md` rather than scribbling into CWD.

- [x] `~/.jac/memory.md` lazily bootstrapped on first user-scope write; `USER_MEMORY_FILE` in `jac.workspace.paths`
- [x] `is_in_project_repo()` helper in `paths` for scope-aware fail-first checks
- [x] `remember(reason, content, category, scope)` — `scope` is `Literal["user", "project"]`, required, no default
- [x] `forget(reason, content, scope)` — symmetric removal, exact-normalized match, errors on 0 / >1 matches with actionable disambiguation
- [x] `scope="project"` outside a git repo raises `JacConfigError` with a clear "use scope=user instead" message — no silent fallback
- [x] Session-id stamping: `jac.runtime.session_ctx` ContextVar-backed `set_current_session_id` / `get_current_session_id`; REPL sets it once per session; audit comment becomes `<!-- jac: <ts> session: <sid> -->`
- [x] Soft size warning surfaced through the tool result when a section crosses 25 entries — loud, no automation
- [x] Context loader refactored into per-source loaders (`load_user_context`, `load_user_memory`, `load_project_context`, `load_project_memory`); `load_session_context` concatenates in the order user-AGENTS → user-memory → project-AGENTS → project-memory
- [x] `gru_system.md` extended with `scope` semantics, `forget` discipline, and a "Picking scope" heuristic table (preference→user, conv/gotcha/decision→project, fact case-by-case)

## Phase 1.6 — Tool surface polish ⏳

**Goal:** close the most concrete gaps in Gru's current tool surface before we layer on minion infra. Five small, mostly independent capability additions / upgrades, each with a clear payoff.

**Why this exists (the retrospective):** on 2026-05-22 we audited our tool surface against a peer agent's. The conclusion was that we should *not* expand to match it 1:1, but a handful of additions are clear wins for the *learning + minion + A2A + local-first* thesis. We also explicitly rejected: ad-hoc subagent definition (we use YAML specs), cron/`schedule` (that's v2 Night Shift), MCP plumbing (defer until a concrete server justifies it), and line-range `replace_file_content` (our unique-match `edit_file` is strictly safer).

**Why these five (and not the others):**

- **Plan tool** — Gru currently has no commitment device for multi-step work. The user can't see what Gru *intends* to do, only what it's doing right now. A visible checklist is the cheapest visibility win possible and helps Gru self-discipline.
- **Background processes** — `run_shell` is synchronous with a 30s timeout, which means *we cannot start a dev server, watch a build, or run a long test suite*. This is the single largest "are we a toy or a real harness" gap.
- **Filesystem + grep upgrades** — small surgical improvements (line-range reads, multi-patch edits, include/exclude globs) that compound over long sessions. None individually critical; collectively they cut a lot of round-trips.
- **Web search + fetch URL** — needed before Phase 3's researcher minion has anything to research. Pydantic AI's DuckDuckGo support is already in deps.
- **Clarify tool** — Gru currently asks ambiguous routing questions in free-form prose. A structured multi-choice prompt makes "which approach should I take?" decisions cleaner, and prepares the ground for the minion-factory delegation gate later.

**Non-negotiables (the same ones as every phase):**

- Every new tool carries `reason: str` and goes through `@jac_tool`.
- Mutating tools are HITL-gated through the existing approval flow — no new approval channel.
- New event types extend `JacEventT` in `jac.runtime.events` and the renderer learns to draw them; no other surface changes are needed.
- Anything that needs a new architectural decision gets a D-number in `architecture.md §11` *in the same change*.
- No tool may import another tool's capability — each capability is self-contained.

### Phase 1.6.a — `PlanCapability` (plan + update_plan) ✅

**Why:** Gru hides its multi-step intent behind tool calls; the user only sees individual actions. A visible plan is (a) better UX, (b) a memory Gru can update across turns without re-deriving, (c) a primitive the renderer can draw without the agent having to "report back" in prose.

**What:**
- `plan(reason, steps: list[str]) -> str` — replaces the current plan with the given steps. Each step starts as `pending`. First call in a session bootstraps; later calls overwrite.
- `update_plan(reason, step: int, status: Literal["pending", "in_progress", "completed"]) -> str` — flips one step. Errors loudly on bad indexes.
- `get_plan(reason) -> str` (read-only) — returns the current plan as text. Useful for resumed sessions where the plan was set in a prior turn.

**How:**
- New `jac.capabilities.plan` module — capability holds the plan state in-process (per session). Persistence is **deliberately deferred** — the plan is ephemeral working memory; durable facts belong in `remember`.
- New events: `PlanReplaced(steps)` and `PlanStepUpdated(index, status)`. Add to `JacEventT`. Renderer draws a checklist panel below the spinner on each event.
- Approval policy: **no approval required**. The plan is a visible side-effect-free todo list, not a mutation of the workspace.
- Plan state lives on the capability instance, NOT in a global. Avoid `_PLAN` module globals — they break minion isolation later.

- [x] `PlanStore` in-memory data type (1-25 steps, ≤240 chars each, loud rejection on bad input)
- [x] `PlanCapability` with `make_plan_capability(bus)` factory mirroring the `make_hooks`/`make_approval_handler` pattern
- [x] Async `plan` + `update_plan` tools emit `PlanReplaced` / `PlanStepUpdated` on the bus; sync `get_plan` for read-only inspection
- [x] `PlanStepView` / `PlanStepStatus` / `PlanReplaced` / `PlanStepUpdated` added to `jac.runtime.events`; `JacEventT` union extended
- [x] CLI renderer keeps a copy of the current plan and re-draws a Rich panel (○ pending / ◐ in_progress / ● completed) on every plan event
- [x] `gru_system.md` updated with "When to call `plan`" discipline + tool listings reordered
- [x] Wired into the REPL alongside hooks + approval; not added to `_default_tool_capabilities` so headless callers don't accidentally pick up a no-bus instance
- [x] architecture.md §11 D15 recorded

### Phase 1.6.b — `ProcessCapability` (background commands) ✅

**Why:** the single biggest "JAC is a real harness, not a chatbot" unlock. Without this we can't start `npm run dev`, can't watch a test suite, can't run anything that takes longer than 30s. Every later phase (researcher minion in particular) benefits.

**What:**
- `start_process(reason, command, name=None) -> task_id` — spawns via `asyncio.subprocess`, returns an opaque id. Approval-required.
- `tail_process(reason, task_id, lines=50) -> str` — read the most recent N lines from the per-process ring buffer. Read-only.
- `kill_process(reason, task_id, signal="TERM") -> str` — terminate. Approval-required.
- `list_processes(reason) -> list[dict]` — id, name, status (running/exited), exit code, runtime. Read-only.

**How:**
- New `jac.capabilities.process` — capability holds `dict[task_id, _ProcessRecord]` on a `ProcessStore` carried by the instance.
- Per process: `asyncio.create_subprocess_shell` (stderr merged into stdout) + background asyncio task that drains output into a `collections.deque(maxlen=2000)` line buffer and records the exit code on completion.
- Auto-cleanup at session close: REPL calls `capability.shutdown()` in a `finally:` block on the REPL loop — SIGTERMs every still-running child and waits up to 5s before SIGKILL. Best-effort, never raises.
- Events: `ProcessStarted(task_id, command, name)`, `ProcessExited(task_id, exit_code)`. Renderer prints both as muted single-line notifications; exit color = green (0) / yellow (>0) / red (<0, signal).
- Process logs do **not** stream to the event bus — that would flood the renderer. They live in the ring buffer; Gru asks for them via `tail_process`.
- **Open question (decided lean):** no `jac processes` user-facing subcommand yet. The agent surface is enough until we have a use case that proves otherwise.
- Architecture decision recorded as D16.

- [x] `ProcessStore` + `_ProcessRecord` data types; per-instance monotonic task-id counter
- [x] `ProcessCapability` with `make_process_capability(bus)` factory
- [x] `start_process` (async, approval) + `tail_process` (sync, read-only) + `kill_process` (async, approval) + `list_processes` (sync, read-only)
- [x] Drain task per process: streams merged stdout/stderr into 2000-line ring buffer, sets exit code, emits `ProcessExited`
- [x] `shutdown()` reaper: SIGTERM all → wait 5s on drain tasks → SIGKILL stragglers; REPL `finally:` invokes it on every exit path
- [x] `ProcessStarted` / `ProcessExited` added to `jac.runtime.events`; `JacEventT` union extended
- [x] CLI renderer prints muted ▶/■ lines with exit-code coloring
- [x] `gru_system.md` updated with the `run_shell` vs `start_process` discipline + tool listings
- [x] architecture.md §11 D16 recorded

### Phase 1.6.c — Filesystem & grep upgrades ✅

**Why:** small surgical wins that compound. None individually critical, all near-zero cost.

**What & how:**

1. **`read_file` line ranges** — added `start_line: int | None`, `end_line: int | None`. 1-indexed, inclusive. Hard cap 1000 lines per call. Without range params: full file up to 1MB. Returns include a `[lines N-M of TOTAL]` header when a range was requested or the file overflowed the line cap. Error order tweaked so "start_line exceeds file length" fires before the derived "end < start" check.
2. **`edit_file` multi-patch** — signature is now `edit_file(reason, path, patches: list[dict[str, str]])` with each patch `{"old": ..., "new": ...}`. Patches applied sequentially against in-memory text; single atomic write at the end. Per-patch unique-match is preserved (errors carry the patch index). Breaking change — old `(old, new)` signature gone (pre-1.0, no compat shims). `gru_system.md` updated.
3. **`grep` upgrades** — added `include` / `exclude` glob params. When `rg` is on `PATH` we shell out (fast, honors `.gitignore`); when it isn't we use the Python walker with `fnmatch`-based include/exclude. Output shape is identical either way (`relpath:lineno:line`). Hit cap stays at 100.
4. **`list_dir` enrichment** — each entry now carries an annotation: `name (SIZE)` for files (B/kB/MB/GB/TB), `name/ (N entries)` for dirs. Dirs sorted first, then files; hidden entries (`.foo`) skipped unless `show_hidden=True`. Unreadable child counts and stat failures surface as `(unreadable)` / `(stat failed)` so a noisy directory doesn't crash the call.

- [x] `read_file` accepts `start_line`/`end_line`; results carry a header when sliced; 1000-line cap enforced
- [x] `edit_file` takes a `patches: list[dict[str, str]]`; identical/no-match/non-unique patches rejected; failure mid-list leaves file untouched
- [x] `grep` accepts `include`/`exclude`; prefers ripgrep, falls back to Python walker — verified both produce equivalent hit counts
- [x] `list_dir` shows annotated sizes / child counts; `show_hidden` toggle; safe for unreadable subdirs
- [x] `gru_system.md` updated with the new `edit_file` signature + multi-patch discipline

### Phase 1.6.d — `WebSearchCapability` (web_search + fetch_url) ✅

**Why:** the researcher minion (Phase 3) needs internet access. Even before Phase 3, Gru benefits from being able to verify a library API or look up an error message. `pydantic-ai-slim[duckduckgo]` was already a dep; the `web-fetch` extra (markdownify) was added alongside this phase.

**What:**
- `web_search(reason, query, max_results=5) -> list[{title, url, snippet}]` — DuckDuckGo text search via the `ddgs` package.
- `fetch_url(reason, url) -> str` — fetch a URL and return the page content as Markdown. SSRF-protected, binary payloads rejected.

**How:**
- New `jac.capabilities.web` module. We **wrap** rather than directly use `pydantic_ai.common_tools.duckduckgo` / `pydantic_ai.common_tools.web_fetch` because those ship as bare `Tool` objects without our `reason: str` discipline — we re-implement the small surface (`@jac_tool` functions) and delegate to the upstream `DDGS` client and `WebFetchLocalTool` for the heavy lifting (SSRF protection, markdownify, JSON pretty-print).
- Approval policy: **none** (read-only, no local side effects). SSRF guard prevents local-network abuse.
- Hard caps: `max_results` 1-10 (default 5); `fetch_url` returns ≤50k chars; 30s timeout.
- Wired into `_default_tool_capabilities` (no bus needed — these are stateless).
- Architecture decision recorded as D18.

- [x] `web-fetch` extra added to the `pydantic-ai-slim` extras list in pyproject.toml; `uv lock` regenerated
- [x] `web_search` + `fetch_url` as module-level `@jac_tool` async functions wrapping DDGS and `WebFetchLocalTool`
- [x] `WebCapability` exposing both via `jac_function_toolset`
- [x] Added to `_default_tool_capabilities()` — every session gets web tools for free
- [x] Validation paths: empty query / out-of-range max_results / empty URL rejected with actionable messages
- [x] Live smoke test: search for "pydantic ai documentation" returns three real hits, fetch_url on the top result returns ~50k chars of Markdown
- [x] `gru_system.md` updated with `web_search` / `fetch_url` tool listings
- [x] architecture.md §11 D18 recorded

### Phase 1.6.e — `clarify` tool (structured multi-choice prompt) ✅

**Why:** Gru currently asks ambiguous questions in free-form prose ("should we use approach A or B?"), and the user replies as a sentence the model has to parse. That's lossy. A structured picker is unambiguous and lets the minion factory's "should I delegate?" gate (Phase 3) reuse the same primitive.

**What:**
- `clarify(reason, question, options: list[str]) -> str` — returns the selected option's text. Raises `RuntimeError` on cancellation (Ctrl-C / EOF) so the agent picks a different approach rather than re-prompting.

**How:**
- Parallels the approval flow: emit `ClarifyRequest(question, options, response_future)` event, renderer renders an interactive Rich numbered prompt, future resolves with the choice index + verbatim text.
- New event types: `ClarifyRequest` (with the future), `ClarifyResponse(selected_index, selected_text, cancelled)`. Added to `JacEventT`.
- Renderer handles it in the main `consume` loop the same way it handles `ApprovalRequest` (pause spinner, prompt via `IntPrompt.ask`, resume).
- **Not approval-required** — the prompt IS the side effect. Approval on a tool whose purpose is to ask the user would be a double prompt.
- Validation: 2-8 distinct (case-insensitive) options, each ≤200 chars; question ≤500 chars. Reuses the same factory + closure pattern as plan/process.
- Architecture decision recorded as D17.

- [x] `ClarifyCapability` + `make_clarify_capability(bus)` (bus is **required** — without one the tool would block forever)
- [x] `ClarifyRequest` / `ClarifyResponse` events; `JacEventT` union extended
- [x] Renderer learned `_prompt_clarify` parallel to `_prompt_approval` (pause spinner, Rich-numbered panel, `IntPrompt.ask` on thread, Ctrl-C → cancelled response)
- [x] Wired into the REPL alongside hooks + approval + plan + process
- [x] `gru_system.md` updated with "When to call `clarify`" discipline
- [x] architecture.md §11 D17 recorded

---

## Phase 2b — Summarizer minion ⏸

The "first minion." Built on top of Phase 3 infra so we don't paint ourselves into a corner. Acts as a redundant safety net on the Phase 2a primary path — proposes deltas at session close, routes them through the same `remember` approval flow rather than writing directly.

- [ ] `summarizer.yaml` minion template (no write tools — structurally cannot mutate memory.md)
- [ ] Session-close hook that invokes the summarizer, then funnels proposed entries through `remember` (each one HITL-gated)
- [ ] Token-cost / opt-in gating so summarization isn't surprise spend

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
- Architectural decisions go in `architecture.md §11`, not here. This file is *what*, not *why*.
