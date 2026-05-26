# JAC — Progress History

> Archived implementation detail for completed and superseded phases. Start with [`progress.md`](progress.md) for current status.

This page keeps the detailed landed-phase notes out of the live progress dashboard while preserving the journey for future agents and maintainers.

## v0.3.0 — Phase A: Context-cost foundation + Phase B: Sub-agent tool ✅

Released 2026-05-27. First two milestones of the post-reframe cost-efficiency roadmap in one cut — A makes individual tool calls cheaper, B makes the whole *strategy* of doing context-heavy work cheaper.

### Phase B — `spawn_sub_agent` (delegation)

New tool the main agent calls when a task would otherwise consume tens of thousands of tokens of intermediate file reads / shell output / web fetches: spawn an isolated sub-agent, let it do the work in *its* context, return only the final answer.

- **`SubAgentTaskPacket`** + `SubAgentResult` + `HookSpec` + `HookResult` Pydantic models in `jac.runtime.sub_agent`.
- **Tier cascade** (`small → medium → large`, never down) so requesting `"small"` against a profile that only has `"medium"` cascades up; the cascade note appears in the result header so the main agent knows what actually ran.
- **Depth cap = 1** enforced structurally: `sub_agent_capabilities()` factory excludes `SubAgentToolCapability` so a sub-agent literally has no spawn tool in its toolset. No runtime check, no way to recurse.
- **HITL on every spawn** — reuses the existing approval flow; renderer shows reason / task_summary / tier / packet.
- **Budget rollup** — `UsageTracker.add_sub_agent(in, out, tier)` lands tokens in `session_total` and writes a JSONL row tagged `kind: sub_agent:<tier>`. `/tokens` shows a `sub_agents:` line with spawns + per-tier breakdown when count > 0.
- **Logfire span** `spawn_sub_agent` with tier / requested_tier / cascaded / model / objective / max_turns / allowed_tools / hook_count / turns_used / exit_status / hook_failures.
- **`gru_system.md` updated** — new "When to call `spawn_sub_agent`" section telling Gru spawn criteria, tier guidance, and packet schema.

Deferred: counter-tier deny flow (Phase E), reference example (`examples/sub-agent-summarize/`), hook *runner* implementation (Phase C — the surface is locked, the implementation lands next).

17 new tests in `test_sub_agent.py` cover tier cascade (up / never down / unknown / no-tier-available), depth cap structural, packet rendering, budget rollup with JSONL tag, fail-fast when no capability, happy path tagged output + stats, cascade visible in header, error path, recorder forwarding, /tokens line.

### Phase A — Context-cost foundation

Three parallel wins, all in one cut:

**A.1 — Tool result post-processor.** Outputs from `run_shell`, `web_search`, and `fetch_url` over `cost.tool_result_threshold_tokens` (default 8000) get routed through the active profile's `small`-tier model and replaced with a summary; raw output stays on disk under `<project>/.agents/cache/tool-results/<session>/<call-id>.txt` so the agent can re-read via `read_file`. Skip rules are conservative (decorator opt-in, pricing must be known on both sides, small tier must be *strictly* cheaper) — JAC never guesses. New `SummarizingToolset(WrapperToolset)` composes cleanly with existing `ApprovalRequiredToolset` (final stack: `ApprovalRequired → Summarizing → Function`). `@jac_tool` gained an optional `summarizable=True` flag; bare form still works. `run_shell`'s old 10KB hard-truncate removed — the summarizer now handles large output instead of throwing it away. Pricing for Anthropic / OpenAI / Google models seeded into `providers.yaml`. New `docs/user-guide/cost-controls.md`.

**A.2 — Prompt-cache fix.** Audit of every `get_instructions()` site found one bug, but it was a doozy: `format_session_datetime()` baked **second-precision time** into the system prompt, so the cached prefix changed every turn and Anthropic's prompt cache never hit. Dropped to day granularity (`Wednesday, May 27, 2026`); now one cache miss per midnight rollover instead of one per turn. Added a `=== Prompt cache boundary ===` comment in `ContextCapability.get_instructions` documenting what must NOT be added there. Three regression-net tests in `test_prompt_cache_stability.py` (no clock, byte-stable across calls, but DOES invalidate when memory.md changes — confirming we didn't over-cache).

**A.3 — `/tokens` visibility.** `UsageTracker.record()` gained `cache_read_tokens` / `cache_write_tokens` kwargs (Anthropic populates these in `RunUsage`; REPL passes them through). `/tokens` now shows a `cache:` line with read / write / hit-rate when provider reports stats, and a `summarize:` line with calls / original / summary / saved / small-tier in+out whenever post-processor activity > 0. Both lines hide when not applicable.

Net effect: the two biggest single-session cost levers on Anthropic are live. Long sessions with heavy tool use should see meaningful drops in turn cost; `/tokens` makes the savings observable in-session.

### Release totals

Tests: **408 passing** (+39 from v0.2). New files: `jac.runtime.tool_summarize`, `jac.runtime.sub_agent`, `jac.runtime.sub_agent_usage`, `jac.capabilities.sub_agent`, `jac.providers.registry.ModelPricing`, `src/jac/prompts/sub_agent_system.md`, `docs/user-guide/cost-controls.md`, `tests/test_tool_decorator.py`, `tests/test_tool_summarize.py`, `tests/test_prompt_cache_stability.py`, `tests/test_sub_agent.py`. The two phases compose: a spawned sub-agent inherits the tool-result summarizer, so it sees less of its *own* tool output too.

## v0.2 Source Restructuring ✅

Released as v0.2.0 on 2026-05-24. Moved misplaced runtime files, trimmed renderer and label-list dead weight, folded prompt path helpers into `workspace.paths`, merged `EventBus` into `runtime.events`, collapsed process/cache indirections, extracted the shared A2A banner, split slash handlers by command, split profile schema/YAML/CRUD modules, added `ContextCapability`, and adopted Pydantic AI's `Instrumentation` capability pattern. The rulebook for future placement is [`developer/module-strategy.md`](developer/module-strategy.md).

## Phase 0 — Skeleton ✅

**Goal:** smallest possible working JAC. `uv run jac` opens a REPL and chats with a bare Gru.

- [x] Project scaffold + `uv` + `pyproject.toml` (build system + console script entry)
- [x] Logfire instrumentation (`logfire.configure(send_to_logfire="if-token-present")` in `jac.runtime.observability` + PAI `Instrumentation()` on Gru via `jac.runtime.gru`)
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
- [x] Layered prompt loader (`jac.workspace.paths.load_prompt`) — project → user → package, first hit wins
- [x] AGENTS.md auto-loader (`jac.workspace.context`) — concatenates user + project context into Gru's instructions
- [x] First-run silent bootstrap (`jac.workspace.bootstrap.ensure_user_workspace`) — idempotent, creates skeleton + template files
- [x] Workspace path resolver (`jac.workspace.paths`) — one source of truth for every path
- [x] `jac init` interactive wizard (`jac.cli.init`) — provider + model + config write with confirmation
- [x] Multi-command Typer app (`jac`, `jac init`)
- [x] Settings made lazy via `get_settings()` so bootstrap can run first
- [x] History file moved under `~/.jac/history` (was already there via prompt-toolkit; now path-resolved)
- [x] Docs updated: CLAUDE.md, architecture.md §11 (D4, D10, D11), progress.md

---

## Phase 1 — Solo Gru ✅

**Recommended order:** event bus **before** tools. The bus is the architectural inversion; every later piece slots into it cleanly.

### Step 1: event bus + tool guard ✅

- [x] `Hooks` capability emitting `JacEvent`s onto an `asyncio.Queue` (`jac.runtime.hooks.make_hooks`)
- [x] `EventBus` (`jac.runtime.events.EventBus`) + typed event dataclasses (`jac.runtime.events`)
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
- [x] `HandleDeferredToolCalls`-based approval handler that emits `ApprovalRequest` events with embedded futures (`jac.runtime.approval`)
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
- [x] History capability via `make_history_capability` (`jac.capabilities.history`) — `ProcessHistory` wrapper with token-aware compaction (D20, Phase 1.7.a); slices on user-prompt boundaries so tool-call/return pairs stay paired
- [x] History capability included in `_default_tool_capabilities` so every session gets it for free (supersedes the original 40-exchange cap planned here)
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
- [x] Session-id stamping: `jac.workspace.session_ctx` ContextVar-backed `set_current_session_id` / `get_current_session_id`; REPL sets it once per session; audit comment becomes `<!-- jac: <ts> session: <sid> -->`
- [x] Soft size warning surfaced through the tool result when a section crosses 25 entries — loud, no automation
- [x] Context loader refactored into per-source loaders (`load_user_context`, `load_user_memory`, `load_project_context`, `load_project_memory`); `load_session_context` concatenates in the order user-AGENTS → user-memory → project-AGENTS → project-memory
- [x] `gru_system.md` extended with `scope` semantics, `forget` discipline, and a "Picking scope" heuristic table (preference→user, conv/gotcha/decision→project, fact case-by-case)

## Phase 1.6 — Tool surface polish ✅

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

## Phase 1.7 — Coworker experience ✅

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
- [x] `jac.cli.statusbar` — `StatusState` dataclass (mutable bag the REPL keeps fresh), module-level branch cache (5s-debounced git shellout, fails-quiet on no-git), `tier_for_model` / `short_model` pure helpers, `format_toolbar(state)` returning a prompt-toolkit `HTML`.
- [x] Ctx % is measured against `compaction.max_context_tokens` (the user-configurable budget from 1.7.a) and color-flips through the same ladder — neutral / yellow at warn_pct / orange at auto_compact_pct / red at refuse_pct. Single source of truth.
- [x] Wired into `_make_prompt_session` via `bottom_toolbar=lambda: format_toolbar(state)`. State updates on every turn (`message_history`), `/clear` + `/resume` (`session_id`), and `/model` + `/profile` rebuilds (`model_id`, `profile_name`, `profile`).
- [x] Branch shown only when we're in a git repo; `*` suffix when the working tree is dirty.
- [x] When the running model isn't in any tier of the active profile (ad-hoc `/model PROVIDER:ID`), the segment becomes `model:short-name` instead of `tier:NAME (...)` so what's running is always visible at a glance.
- [x] Profile/tier fields hidden entirely when the REPL is started with `--model` (no profile).
- [x] 23 tests (`tests/test_statusbar.py`): helper coverage (tier lookup, short-model splits), branch-cache debounce + no-git + dirty, ctx-color thresholds, toolbar rendering across profile/ad-hoc/no-git/dirty branches.

**Deferred:** `BudgetWarning` / `BudgetHardStop` re-render hooks live with their owning phase (1.7.f token budgets) — those events don't exist yet. The ctx-color path already covers what we can compute today.

### Phase 1.7.c — Slash commands + tiered profile schema (D22) ✅

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

### Phase 1.7.f — Token budgets (D25) ✅

**Why:** running a learning project against paid providers without a stop button is asking for a surprise bill. Token-based (not dollar-based — D25) budgets give us a provider-agnostic guardrail.

**Landed (2026-05-23):**
- [x] `jac.runtime.usage.UsageTracker` — accumulates input/output deltas from `AgentRunResult.usage()` after every successful turn. Holds `BudgetLimits` (the three knobs plus warn/hardstop pcts) and a dedup set so each `(kind, threshold)` event fires at most once per session.
- [x] `<repo>/.agents/usage.jsonl` append-on-turn — one line `{session_id, ts, input_tokens, output_tokens}` per completed turn. Crash-safe (mirrors 1.7.g's "crash-recovery is first-class" stance — kills mid-session don't lose prior turns from `project_total`).
- [x] `load_project_baseline(usage_file, exclude_session_id)` sums all input+output across prior sessions on REPL startup; running session contributes via in-memory counters. Malformed JSONL lines are skipped silently (per the 1.7.g discipline).
- [x] Three independent knobs in new `BudgetSettings`: `session_input_tokens`, `session_total_tokens`, `project_total_tokens` (all default `None` — opt-in only). Pulled via the existing layered `JAC_BUDGET__*` env override path for free.
- [x] `BudgetWarning(kind, used, budget, pct)` + `BudgetHardStop(kind, used, budget)` events added to `JacEventT`. Renderer prints the warning as an inline yellow notice; the hardstop is silent at renderer level because the REPL prints the actionable refusal message.
- [x] Pre-turn refusal helper `_refuse_if_over_token_budget` mirrors the context-budget refusal — strict check (`session_total >= limit`), per the locked decision. Refused turns never reach `agent.run`.
- [x] Status bar `bud:` segment (`bud:42%`) appears only when at least one budget is configured. Color follows the warn (yellow at ≥80%) / hardstop (red at ≥100%) thresholds. Hidden by default so opt-out users see no clutter.
- [x] `UsageTracker.status_pct()` returns the highest-used percent across configured budgets (drives `bud:`). `is_over_hardstop()` returns `(kind, used, budget)` or `None` (drives refusal). `extend(kind, n)` raises the limit in memory + resets the dedup set so warn/hardstop can fire again at the new threshold.
- [x] Session-switch path (`/clear`, `/resume`) rebuilds the tracker against the new session id so the project baseline excludes the right session.
- [x] `/budget` slash — no-arg view (table of all three knobs with used / limit / pct, color-coded); `extend N` adds to `session_total` by default; `extend KIND N` for precision. Commas and underscores in the amount are accepted (`50,000`, `1_000_000`).
- [x] `/tokens` slash — detailed counters (session input / output / total + project total with baseline split).
- [x] `gru_system.md` slash-commands section grew `/budget` and `/tokens` entries.
- [x] 31 tests: 20 in `tests/test_usage.py` (tracker mechanics, JSONL persistence, baseline loading, threshold dedup, status_pct, is_over_hardstop, extend), 11 in `tests/test_budget_slash.py` (handler view / extend / error paths), plus 3 in `tests/test_statusbar.py` for the `bud:` segment.

**Deliberately absent (D25):** `/cost` — Pydantic AI exposes tokens but not cost, and a per-model price table goes stale fast. Users map tokens to whatever pricing they have.

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

### Phase 1.7.h — Tavily web search backend ✅

**Why:** Tavily is becoming the standard search backend for agent harnesses; DDG works but is the fallback, not the lead. Wire Tavily as primary when an API key is present, DDG otherwise. Provider-native search (Anthropic/OpenAI/Google) was considered and rejected — it would force per-provider code paths and break portability. One client-side `web_search` tool, two backends.

**Landed (2026-05-23):**
- [x] `tavily` extra added to the `pydantic-ai-slim` extras list — pulls in `tavily-python>=0.5.0`. Lock + sync refreshed.
- [x] `web_search` refactored into a thin dispatcher with two helpers: `_search_tavily(query, max_results, api_key)` uses `AsyncTavilyClient` directly (we keep our own `@jac_tool` shape with `reason:` rather than going through pydantic-ai's `tavily_search_tool()` factory, which would skip the discipline); `_search_ddg(query, max_results)` is the existing DDG path. Tool signature + return shape (`{title, url, snippet}`) unchanged across both backends — Tavily's `content` field is mapped to `snippet` to match DDG's `body`.
- [x] Backend selection is a single `os.environ.get("TAVILY_API_KEY")` check inside the tool. No config block, no `web:` namespace — keeping the surface flat.
- [x] `resolve_optional_keys(keys)` helper in `jac.secrets` — best-effort resolves keys from the configured secrets backend (keyring/dotenv) into `os.environ` without raising on missing values. The REPL calls it once at startup with `["TAVILY_API_KEY"]` so users who stored the key via `jac keys set TAVILY_API_KEY` get auto-injection before any tool fires.
- [x] **Tavily errors surface, never silently fall back to DDG.** A failed Tavily call (network / quota / auth) raises; the user explicitly opted in by setting the key, so masking a failure would be misleading.
- [x] `.env.template` gained an optional `TAVILY_API_KEY=xxxx` entry pointing at app.tavily.com for sign-up.
- [x] 5 tests in `tests/test_web_backends.py`: DDG runs when no key, Tavily runs when key set (constructed with the right key, query forwarded, result shape mapped), validation errors don't construct any client, Tavily errors don't silently fall back to DDG.
- [x] `gru_system.md` — tool surface unchanged (deliberate), no edits.
- [x] `jac init` prompt — **deferred**, out of scope for MVP. Users `export TAVILY_API_KEY=...`, drop it in `.env`, or store via `jac keys set TAVILY_API_KEY` (works today because the keys CLI is backend-generic). Will revisit if onboarding feedback demands it.

---
