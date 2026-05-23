# JAC — Implementation Progress

> **Just Another Companion/CLI** · **Updated:** 2026-05-23 · keep this in sync as work lands.

This file tracks **what is implemented**, **what is in flight**, and **what is queued**.
For the *why* see `idea.md`. For the *how* see `architecture.md` and `CLAUDE.md`.

Each phase block leads with **Goal** + **why/what/how** before the checklist. This is intentional — phases get revisited after long gaps and the rationale must survive without re-derivation. Architectural decisions live in `architecture.md §11`; this file is the *what*, but each phase here should hand you enough *why* that the *how* makes sense.

**2026-05-22 roadmap reshuffle:** after a brainstorm pass on 2026-05-22 we reordered the post-1.6 phases. Phase 1.7 (Coworker experience — UX + cost-control batch) jumps to the front; Phase 2b (standalone summarizer minion) is **superseded** by 1.7.a (token-aware compaction); Phase 3 switches from bespoke YAML minion templates to the community **Skills** format (D21); A2A moves out of v2 to Phase 4; minions move to Phase 5 (pending a grooming session). See `architecture.md §9` for the rationale, §11 D20–D27 for the new decisions.

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
| **Phase 1.7 — Coworker experience** | ⏳ **In flight** | umbrella for compaction, status bar, slash commands, budgets, feedback channels — see sub-phases below. **Complete:** 1.7.c (D22 schema + slash scaffolding + `/model` + `/profile` + `jac profiles edit`), 1.7.a (token-aware compaction, 200k default budget), 1.7.b (bottom-toolbar status bar), 1.7.d (approval/clarify feedback channels — D26), 1.7.g (plan persistence on resume — D27). **Deferred to v2:** 1.7.e Plan Mode + `ModeCapability` base — design needs more time (multi-plan handoff, plan-injection budget hazard, mode-base scope). **Next:** 1.7.f budgets, then 1.7.h Tavily. |
| Phase 2b — Summarizer minion | ⛔ Superseded | rolled into Phase 1.7.a (token-aware compaction). No separate minion. |
| Phase 3 — Skills (D21) | ⏸ Queued | community-format skill loader + inline mode (replaces old bespoke minion factory plan) |
| Phase 4 — A2A (D24) | ⏸ Queued | inbound server + outbound client — moved up from v2 |
| Phase 5 — Minions | ⏸ Queued | runtime for skills with `mode: minion` — **needs grooming session before implementation** |
| Phase 6 — MCP | ⏸ Queued | external MCP servers + the `reason:` discipline call (D26 reasoning) |
| Phase 7 — Quality | ⏸ Queued | broader pytest, ruff, mypy, user docs |
| v2 | ⏸ Future | YOLO + Monty + CodeMode + stuck-loop + Night Shift + user-tier predict-calibrate memory |

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
- [x] Shipped `src/jac/data/defaults.yaml` (non-required tunables only; fail-first for model/keys)
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
- [x] Provider catalog externalized: `src/jac/data/providers.yaml` + optional `~/.jac/providers.yaml` overlay (`jac.providers.registry`); powers init wizard + credential inference
- [x] `defaults.yaml` ships `secrets.backend: keyring`; bootstrap writes `providers.yaml.example`
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

## Phase 2b — Summarizer minion ⛔ Superseded

**Status (2026-05-22):** rolled into **Phase 1.7.a** (token-aware compaction). Rationale: the old plan was a standalone minion that summarized at session close. The new compaction path runs the *same* summarization mid-session (when the context window crosses 70%) using the profile's `small` tier model, against a far more useful trigger (cost burn, not session close). Two summarizers for one job would have been waste. Nothing else in this phase block survives — see Phase 1.7.a for the actual work.

---

## Phase 1.7 — Coworker experience ⏳

**Goal:** turn JAC from "a single agent that works" into "an agent you'd actually want to sit next to all day." A batch of small-to-medium changes that share renderer surface area. The ordering follows brainstorm 2026-05-22 — biggest-pain items first. **Plan Mode (1.7.e) was pulled out of the batch on 2026-05-23 — see below for the deferral rationale.**

**Why this exists (single paragraph):** by the end of Phase 1.6 Gru has a real tool surface but no visibility (you don't know what model is active, how much context you've burned, what session you're on), no slash commands (every action is a CLI restart), the sliding-window cap is exchange-count not token-count (a real cost trap — D20), and approval / clarify are yes-no choices that cost a model turn when the user wants to redirect. Phase 1.7 closes all of those at once because they fight over the same UI real estate (status bar + bottom prompt + inline panels).

**Non-negotiables:** every new tool / event still carries `reason: str` and goes through `@jac_tool` and the EventBus. Slash commands share internals with their `jac <subcommand>` counterparts — *no duplicate logic*. New decisions ride to `architecture.md §11` in the same change.

### Phase 1.7.a — Token-aware history compaction (D20) ✅

**Why:** the old `ProcessHistory` capped at 40 *exchanges*. At 40 heavy exchanges total context could sit at 180k+ tokens and every model call re-processed them — silent input-token cost burn. This phase replaces exchange-count gating with a **user-configurable token budget** (default 200k, *not* the model's published context window — newer models advertise 1M+ but quality typically degrades past ~200-300k) and a three-step ladder.

**Landed (2026-05-23):**
- [x] `TokenAwareHistory` processor — char-based 3-tokens-per-char heuristic, threshold ladder 60/70/85 with `target_pct_after_compact=50`
- [x] User-configurable budget: `compaction.max_context_tokens` (default 200_000) with env override `JAC_COMPACTION__MAX_CONTEXT_TOKENS` — every threshold is a percent of *that*, not the model's window
- [x] Small-tier summarizer via `pydantic_ai.direct.model_request` (async); drop-only fallback on failure (no profile / no `small` tier / call raises)
- [x] `<session>/compacted/<n>.json` snapshots of every dropped slice (via `ModelMessagesTypeAdapter`)
- [x] Portable `<<conversation_summary>>` `UserPromptPart` survives `/profile` provider swaps mid-session
- [x] `CompactionWarning` / `CompactionTriggered` / `CompactionRefused` events on `JacEventT`; CLI renderer prints inline notices
- [x] REPL pre-flight refuse check: emits `CompactionRefused` and skips the turn if `history + prompt` exceeds `refuse_pct` — the model is never called
- [x] `summarizer_model` threaded through `build_gru`; rebuilt on `/profile` switch via `_resolve_summarizer_model`
- [x] `gru_system.md` "Context management" section so Gru doesn't redundantly summarize
- [x] architecture.md §11 D20 updated with the user-configurable-budget refinement
- [x] 14 tests: estimator, drop-boundary, threshold ladder paths, drop-only fallback, snapshot persistence, env override

**Deferred (1.7.b status bar):** status bar color flip on warning/refused — the renderer prints inline notices today; the persistent bottom-toolbar lands with 1.7.b.

**Refinement vs the original D20:** the budget is *user-configurable*, not "the active model's context window". A 1M model with a 200k budget compacts at 140k (70% of budget), not 700k — matching the actual useful context envelope rather than the marketing one. Users on smaller / cheaper models can lower it; users who trust their model with more can raise it.

### Phase 1.7.b — Status bar ✅

**Why:** the user had no persistent visibility into what model / tier / profile / session / branch / context usage is active. Every modern CLI agent shows this. Cheapest meaningful UX win.

**What:** single-line `prompt_toolkit` `bottom_toolbar`, always visible:

```
 profile:claude  tier:medium (claude-sonnet-4-5)  branch:main*  ctx:34%/200k  session:20260523T20-00-00
```

**Landed (2026-05-23):**
- [x] `jac.cli.statusbar` — `StatusState` dataclass (mutable bag the REPL keeps fresh), `_BranchCache` (5s-debounced git shellout, fails-quiet on no-git), `tier_for_model` / `short_model` pure helpers, `format_toolbar(state)` returning a prompt-toolkit `HTML`.
- [x] Ctx % is measured against `compaction.max_context_tokens` (the user-configurable budget from 1.7.a) and color-flips through the same ladder — neutral / yellow at warn_pct / orange at auto_compact_pct / red at refuse_pct. Single source of truth.
- [x] Wired into `_make_prompt_session` via `bottom_toolbar=lambda: format_toolbar(state)`. State updates on every turn (`message_history`), `/clear` + `/resume` (`session_id`), and `/model` + `/profile` rebuilds (`model_id`, `profile_name`, `profile`).
- [x] Branch shown only when we're in a git repo; `*` suffix when the working tree is dirty.
- [x] When the running model isn't in any tier of the active profile (ad-hoc `/model PROVIDER:ID`), the segment becomes `model:short-name` instead of `tier:NAME (...)` so what's running is always visible at a glance.
- [x] Profile/tier fields hidden entirely when the REPL is started with `--model` (no profile).
- [x] 20 tests: helper coverage (tier lookup, short-model splits), branch-cache debounce + no-git + dirty, ctx-color thresholds, toolbar rendering across profile/ad-hoc/no-git/dirty branches.

**Deferred:** `BudgetWarning` / `BudgetHardStop` re-render hooks live with their owning phase (1.7.f token budgets) — those events don't exist yet. The ctx-color path already covers what we can compute today.

### Phase 1.7.c — Slash commands + tiered profile schema (D22) 🚧

**Why:** every meaningful in-session action today (switch model, see sessions, compact, see cost, exit) requires killing the REPL and restarting with new args. Slash commands fix that and *share internals with the CLI subcommands* — no duplicate logic. Tiered profiles (D22) ship in this phase because `/model` is meaningless without them.

**What:**
- Slash registry: `/help`, `/exit`, `/clear` (new session in-place), `/model`, `/profile`, `/compact`, `/sessions`, `/resume`, `/cost`, `/tokens`, `/remember`, `/forget`, `/budget`, `/plan` (the *mode* entry — see 1.7.e)
- Profile YAML schema gains `tiers:` (ordered list per tier, first = default) and `active_tier:`
- `/model` with no arg shows configured tiers + alternates; `/model TIER` switches tier; `/model PROVIDER:ID` ad-hoc one-session override (persists until changed again — per user decision 2026-05-22)
- `prompt_toolkit` `WordCompleter` populated from the registry + active profile's models

**How:**
- New `jac.cli.slash/` package — one module per command family
- Each slash handler delegates to the same internals as the corresponding `jac` subcommand (the profile commands are the prototype; replicate that pattern)
- `/remember TEXT` and `/forget TEXT` shortcut Gru's tool call — go through the same `MemoryCapability` but without the model roundtrip
- Schema change is a **hard breaking change**. Library-level `list_profiles()` fails first on the old `model:` shape; `jac init` detects + auto-rewrites with user confirmation (2026-05-22 decision — friendlier than hand-edit-or-die).

**PR carve-out (2026-05-22):** this phase ships in three PRs. PR1 = D22 schema + migration (landed). PR2 = slash scaffolding + `/help` `/exit` `/clear` `/sessions` `/resume`. PR3 = `/model` `/profile` + the rebuild-Gru path. **Slashes deferred to their upstream phases:** `/compact` (1.7.a), `/tokens` `/budget` (1.7.f), `/plan` (1.7.e). `/cost` is permanently out — D25 refuses dollar conversion. `/remember` `/forget` deferred to a follow-up; the UX needs more thought than fit in PR1-3.

- [x] `jac.profiles.Profile` schema extended with `tiers:` (ordered list per tier) and `active_tier:` *(PR1)*
- [x] Migration path for old `model:` field (auto-rewrite on `jac init` with user confirmation) *(PR1)*
- [x] `apply_profile_env` uses `default_model()` and unioned secret inference; `apply_ad_hoc_model_env` for `--model` overrides *(PR1)*
- [x] `jac init` collects per-tier models (medium required; small/large optional via follow-up prompt) *(PR1)*
- [x] `jac profiles list` renders tiers with `← active` marker *(PR1)*
- [x] Tests for tier schema, union secret inference, old-shape rejection, migration idempotency *(PR1)*
- [x] `jac.cli.slash.registry` with `@register` + dispatch + `UnknownSlashCommand` + `command_names()` for the completer *(PR2)*
- [x] First batch of handlers (`/help`, `/exit`, `/clear`, `/sessions`, `/resume`); session-list rendering extracted to `jac.cli.session_view` so `jac sessions` and `/sessions` share it *(PR2)*
- [x] `prompt_toolkit` `WordCompleter` populated from registered command names *(PR2)*
- [x] REPL threads `profile_name` through and handles `SwitchSession` / `Exit` results *(PR2)*
- [x] `/model` (numbered picker + ad-hoc `PROVIDER:ID`) + `/profile` (list + switch) with snapshot-try-rollback in REPL — failed switches stay on the previous model with a yellow warning, never leave half-applied env. **No `/model TIER`** — tiers are for Gru-to-minion delegation, humans pick models. *(PR3)*
- [x] `snapshot_env` / `restore_env` helpers in `jac.secrets` for the rebuild safety net *(PR3)*
- [x] Profile-listing rendering extracted to `jac.cli.profile_view` so `jac profiles list` and `/profile` share it *(PR3)*
- [x] `gru_system.md` updated with a "Slash commands" section so Gru knows the user has out-of-band controls (`/clear`, `/sessions`, `/resume`, `/model`, `/profile`) *(PR3)*
- [x] `jac profiles edit NAME` — single minimal command, opens the profile's YAML in `$EDITOR`, validates on save, offers re-open on error. No add-model/remove-model/set-active subcommands (deliberately minimal CLI surface — hand-edit covers every case). *(PR4)*

### Phase 1.7.d — Approval & clarify accept feedback (D26) ✅

**Why:** today denying a tool call costs a turn — the model has to re-decide what to do. With in-band feedback the user types "edit the test file instead" on the deny prompt and the model gets the redirection as a tool result. Same for clarify — adding a "type your own" option avoids a follow-up turn.

**What:**
- Approval prompt gets a third option: `[y]es / [n]o / [r]edirect with feedback`. Selecting `r` opens a follow-up text input; the response becomes a `denied_with_feedback(text)` variant
- Clarify prompt gets a final numbered option "Type your own answer" that opens a text input; resolves with `free_text=True`
- Tool result for `denied_with_feedback` is structured: the deny-message string the model sees on `ToolDenied.message` embeds a labeled `user_feedback: "..."` field plus an explicit "do not retry" hint so Gru reads it as a redirection.

**How:**
- Reuses the existing event-bus `Future` plumbing — no new approval channel.
- `ApprovalResponse` grew `feedback: str | None = None` (default `None`, non-breaking for existing callers).
- `ClarifyResponse` grew `free_text: bool = False`; the clarify capability returns `selected_text` verbatim, so the runtime needed no further changes.
- Approval handler centralizes the deny-message build in `_deny_message(response)` — feedback wins over `deny_message` when both are set, falls back to the default copy when neither is.
- Renderer: `_prompt_approval` is now a 3-way `Prompt.ask` over `[y, n, r]` (`r` → `_collect_approval_feedback`); `_prompt_clarify` always appends "Type your own answer" as the last numbered option (picking it → `_collect_clarify_free_text`). Empty input or Ctrl-C on either follow-up degrades to plain deny / plain cancel — never a half-set response. Free-text inputs cap at 600 chars.
- Tests: `tests/test_hitl_feedback.py` covers `_deny_message` plain / explicit / feedback / feedback-beats-deny-message; the approval handler under approve, plain-deny, and deny-with-feedback (asserts the embedded `user_feedback` label survives into `ToolDenied.message`); clarify under free-text, picked-option, and cancel paths (regression-guarded).

- [x] `ApprovalResponse.feedback` field + `denied_with_feedback` semantics
- [x] `ClarifyResponse.free_text` field + extra menu option
- [x] Renderer updates for both (`_prompt_approval` 3-way, `_prompt_clarify` free-text affordance)
- [x] `gru_system.md` notes that denials may carry `user_feedback` and that clarify always offers a free-text answer
- [x] 10 tests in `tests/test_hitl_feedback.py`

### Phase 1.7.e — Plan Mode (D23) ⛔ Deferred to v2

**Status (2026-05-23):** deferred along with the `ModeCapability` base and the `plan`→`tasks` rename. The design surface area is larger than 1.7.e's slot can absorb without rushing — flagged risks (in the order they bit):

- **Multi-plan handoff.** If Plan Mode is entered twice in one session, does the second approved plan replace or append to the first in the executor's instructions? Replace is simpler but loses prior context; append bloats. Needs a real decision, not a guess.
- **Plan-injection budget hazard.** Auto-injecting an approved plan into every subsequent turn's system prompt costs tokens proportional to plan size. An unbounded `write_plan` is a quiet cost trap on top of D20/D25. Needs an explicit cap (and a `write_plan` size limit) before it ships.
- **`ModeCapability` base scope.** Brainstorming surfaced ≥4 plausible modes (Plan, Explore/read-only, Curate/memory, YOLO). The base needs both `filter_capabilities` and `approval_override` to cover all of them — but only Plan Mode currently uses the first, only YOLO the second. Building the base for one mode risks the abstraction being wrong for the rest.
- **Rename collateral.** The `plan`→`tasks` rename was bundled with D23 to free the word "plan" for the artifact. With D23 deferred, the rename also defers — current code (`PlanCapability`, `plan`/`update_plan`/`get_plan` tools, `<session>/plan.json` filename for D27 persistence) stays put until Plan Mode actually ships in v2.

Decisions D23 / D29 (the YOLO sketch) stay in `architecture.md §11` as the design we'll use when this lands — the deferral is about timing, not direction. v2 entry below carries the work item.

### Phase 1.7.f — Token budgets (D25) 🚧

**Why:** running a learning project against paid providers without a stop button is asking for a surprise bill. Token-based (not dollar-based — D25) budgets give us a provider-agnostic guardrail.

**What:**
- New `budget:` block in profile/project config: `session_input_tokens:`, `session_total_tokens:`, `project_total_tokens:`
- All defaults are `null` (opt-in only — no surprise stops on first run)
- Warn at 80% (`BudgetWarning` event, status bar yellow), hard-stop at 100% (`BudgetHardStop` event, refuses next user turn)
- `/budget extend N` overrides for the rest of the session
- `<repo>/.agents/usage.jsonl` aggregates per-session totals so `project_total_tokens` works across sessions

**How:**
- New `jac.runtime.usage` module — observes `after_model_request`, accumulates into per-session + per-project counters
- Status bar reads from this module
- `/cost` is intentionally absent — D25 explicitly refuses dollar conversion

- [ ] `jac.runtime.usage.UsageTracker` accumulating from `RunUsage`
- [ ] `<repo>/.agents/usage.jsonl` append-on-turn-close
- [ ] `BudgetWarning` / `BudgetHardStop` events; renderer + status bar integration
- [ ] `/budget` slash + `budget:` config block
- [ ] architecture.md §11 D25 recorded ✅ (done in this change)

### Phase 1.7.g — Plan-list persistence on resume (D27) ✅

**Why:** the in-session checklist (`PlanCapability`) was in-memory only. Process killed → cross-terminal resume → lost. D27 revises D15 to persist it per session — and this is the first cost-control phase to actually ship in 1.7.

**Landed (2026-05-23):**
- [x] `PlanCapability` learned `plan_file: Path | None` + `initial_steps: list[dict] | None`. Every `plan(...)` / `update_plan(...)` call atomically rewrites `<session>/plan.json` (tempfile + rename, matches the memory.md pattern). Without `plan_file` the capability stays ephemeral for tests / headless callers.
- [x] JSON schema: `{"version": 1, "steps": [{"text", "status"}]}`. Future-proofed with a version field; loader rejects unknown statuses, wrong shapes, empty step text.
- [x] `Session.plan_file` property + `Session.load_plan()` method returning `(steps, warning_or_None)`. In-progress flips to pending on load — the actor was killed mid-step. Malformed files **log a yellow warning and return empty** (per locked decision) instead of failing the resume.
- [x] REPL wiring: `_repl_loop` calls `session.load_plan()`, surfaces any warning, seeds `make_plan_capability(plan_file=, initial_steps=)`, prints a one-line "N step(s) restored (M pending)" hint in the greeting, and emits a synthesized `PlanReplaced` event so the renderer paints the checklist panel on the first turn (no special startup render path — all rendering still flows through the bus).
- [x] `PlanCapability.switch_session(new_plan_file, restored_steps)` re-points an existing capability at a different session's file on `/clear` and `/resume`. Mutates `self.store` in place (rather than replacing it) so the tool closures from `_build_tools()` — which captured the store by reference — stay valid. The REPL invokes it right after `_switch_session`.
- [x] `gru_system.md` extended with an "On session resume" note under the plan section so Gru knows to expect restored state.
- [x] 15 tests in `tests/test_plan_persistence.py` covering persist-on-mutation, atomic write (no leftover `.tmp`), ephemeral mode, initial-steps seeding, missing/malformed/wrong-shape/unknown-status/empty-text load paths, switch_session repoint+clear, event emission shape, end-to-end persist→load→seed→continue cycle.

**Naming note (2026-05-23):** D27's architecture text uses the `tasks` names that D23 was going to introduce. With 1.7.e deferred, this phase ships using the **current** names — `PlanCapability`, `plan`/`update_plan`/`get_plan` tools, file `<session>/plan.json`. The rename comes back when Plan Mode actually lands (v2).

### Phase 1.7.h — Tavily web search backend 🚧

**Why:** Tavily is becoming the standard search backend for agent harnesses; DDG works but is the fallback, not the lead. Wire Tavily as primary when an API key is present, DDG otherwise. Provider-native search (Anthropic/OpenAI/Google) was considered and rejected — it would force per-provider code paths and break portability. One client-side `web_search` tool, two backends.

- [ ] Add `tavily-python` to optional extras (or hit the HTTP API directly — TBD when implementing)
- [ ] `TAVILY_API_KEY` added to provider catalog as a non-provider-bound key (or to the `web:` block in config)
- [ ] `web_search` tool picks backend based on key presence; signature unchanged
- [ ] `jac init` prompts for Tavily key as optional
- [ ] `gru_system.md` unchanged (tool surface identical)

---

## Phase 3 — Skills (D21) ⏸

**Goal:** adopt the Anthropic community skills format so JAC isn't an island — community-maintained skills install as-is, and our own minions (Phase 5) are built on the same substrate.

**Why:** D21. The previously-planned bespoke YAML AgentSpec format was reinventing what's now a community standard. Skills are markdown-with-frontmatter (`name:`, `description:`, body) loaded from `~/.jac/skills/<name>/SKILL.md` and `<repo>/.agents/skills/<name>/SKILL.md`. Default `mode: inline` injects the body into Gru's context when the skill's description matches the user's request (description-based triggering, same as Claude Code and Anthropic's own skill ecosystem). Optional `mode: minion` extends the same file for sub-agent spawning — but the *runtime* for that lives in Phase 5 (needs grooming).

- [ ] Skill loader walks `~/.jac/skills/` and `<repo>/.agents/skills/` (project shadows user)
- [ ] Frontmatter validator (community spec compliance)
- [ ] Description-based triggering — inject skill body into Gru's system prompt when relevant
- [ ] `/skill NAME` slash to force-load a skill
- [ ] `/skill list` shows available skills and their trigger conditions
- [ ] Ship 2-3 reference skills in `src/jac/data/skills/` (a `code-review` skill is a good first candidate)
- [ ] Documentation: how to write a skill (point at the Anthropic spec)
- [ ] architecture.md §11 D21 recorded ✅ (done in this change)

---

## Phase 4 — A2A (D24) ⏸

**Goal:** speak the A2A protocol both ways so JAC can talk to other A2A-compatible agents (other JAC instances *or* third-party deployed agents — D24 explicitly covers the case of a cloud-deployed data-science agent exposing A2A).

**Why now (not v2):** D24. A2A has no hard dependency on minions or skills, and it's the project's headline differentiator. Continuing to defer it makes the differentiator vapor.

**Inbound (server):**
- [ ] `fasta2a` server wrapped as a `Capability`, spawned on a background asyncio task by `/a2a serve` or `jac a2a serve`
- [ ] Generated bearer token printed at start; `Authorization: Bearer <token>` enforced unless `--unsafe`
- [ ] **Guest Gru** — each inbound request spawns a *fresh* Gru with empty session memory, read-only toolset by default (configurable per profile via `a2a.guest_capabilities:`), separate Logfire span tagged with caller identity
- [ ] Server survives until REPL exits or `/a2a stop`
- [ ] Headless mode (`jac a2a serve --port N`) for running A2A without an interactive REPL

**Outbound (client):**
- [ ] `a2a_call(reason, url, message)` tool — read-only (no approval needed for the *call*, but the model's *use* of the returned data may trigger approval on downstream tools)
- [ ] Follow the [A2A protocol spec](https://a2a-protocol.org/latest/)
- [ ] Auth header support — config block for known peers' tokens
- [ ] Gru may delegate A2A back-and-forth to a minion (once minions exist) to keep its own context clean

- [ ] architecture.md §11 D24 recorded ✅ (done in this change)

---

## Phase 5 — Minions ⏸ (grooming pending)

**Goal:** runtime for skills with `mode: minion` — spawn a sub-agent with isolated context, scoped toolset, structured output, return to Gru.

**⚠️ Needs a grooming session before any code lands.** D21 locks the *file format* (skills with `mode: minion`). The *runtime* is not yet designed: output schema enforcement, tool scoping rules, factory orchestration, parallelism (one at a time? many?), failure handling, structured output validation, retry policy. Owner of next grooming session: TBD.

- [ ] Grooming session: lock the runtime design, add D-numbers
- [ ] Minion factory capability + `spawn_minion(reason, skill_name, task_packet)` tool
- [ ] Task packet schema (already locked in §5a — `objective` / `success_criteria` / `relevant_files` / `forbidden_actions` / `expected_output`)
- [ ] Tier-based model selection from `model_tier:` field
- [ ] Structured output validation against the skill's `output_schema:` block
- [ ] First 2-3 reference minion-mode skills

---

## Phase 6 — MCP ⏸

**Goal:** consume external MCP servers so JAC's tool surface scales without us writing every tool by hand.

**`reason:` tension resolved (D28):** MCP tools don't carry `reason: str`. We accept loose enforcement — render `reason: (mcp tool — no reason captured)` in the approval UI. Honest about the gap, community-compatible. See architecture.md §11 D28.

- [x] `reason:` tension resolved as D28 (2026-05-22)
- [ ] `~/.jac/mcp.yaml` schema + loader
- [ ] `MCPServerStdio` / `MCPServerHTTP` wiring via pydantic-ai
- [ ] `/mcp list` and `/mcp reload` slash commands
- [ ] Per-server enable/disable

---

## Phase 7 — Quality ⏸

- [ ] CodeMode integration moved to v2 (no concrete pain yet — D9 in idea.md notes)
- [ ] Stuck-loop detection moved to v2 (low value in HITL where human catches loops)
- [x] Provider registry tests (`tests/test_provider_registry.py`, `just test`)
- [ ] Broader test suite (pytest) — capability-level coverage for the Phase 1.7 additions
- [ ] Ruff / mypy config
- [ ] User docs (publish under `docs/user-guide/` via the existing Zensical site)

---

## v2 ⏸

- [ ] **Plan Mode + `ModeCapability` base (D23 + D29 YOLO sketch)** — deferred from 1.7.e on 2026-05-23. Carries the bundled `plan`→`tasks` rename: tools (`plan`/`update_plan`/`get_plan` → `tasks`/`update_task`/`get_tasks`), capability (`PlanCapability` → `TaskListCapability`), events (`PlanReplaced`/`PlanStepUpdated` → `TaskListReplaced`/`TaskStepUpdated`), session file (`<session>/plan.json` → `<session>/tasks.json`). Build the base + Plan Mode together so the abstraction is exercised on day one; YOLO follows when sandboxing lands.
- [ ] YOLO mode + sandboxing (Monty + sandbox-exec / bwrap + Git-Clean Guard) — uses `ModeCapability`'s `approval_override` knob (D29 sketch)
- [ ] CodeMode integration (`pydantic-ai-harness`)
- [ ] Stuck-loop detection
- [ ] Night Shift / cron scheduling
- [ ] User-tier memory + predict-calibrate extraction
- [ ] Browser / API / SDK surfaces

---

## How to use this file

- When you start a task, change `- [ ]` to `- [~]` (in flight).
- When you finish, `- [x]` and a one-line note if anything deviated from the plan.
- When a new task surfaces, add it to the relevant phase or "v2" — don't let it float.
- Architectural decisions go in `architecture.md §11`, not here. This file is *what*, not *why*.
