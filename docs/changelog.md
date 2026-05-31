# Changelog

All notable changes to JAC are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-05-31

**v0.9.0** — the local-first **web UI** (`jac web serve`) and the **SDK control plane** that makes every surface a thin adapter over one engine. Pre-1.0 API. 768 tests.

### Added

- **Web surface (D48) — a third surface.** `jac web serve` opens a local-first, single-user browser UI under `src/jac/web/` (Starlette + Jinja2 + HTMX + SSE, **zero new dependencies**): a streaming **chat Console** with HITL approvals resolved in the browser (the same `asyncio.Future` the CLI resolves at a terminal prompt), plus an **activity dashboard** (token/cost meter, minion cards, files-changed, environment). It drives the *same* `build_session_runtime` engine, tools, and approvals as the CLI — a renderer, **not** a new runtime mode. Binds `127.0.0.1`; a non-loopback `--host` warns loudly. Guide: [`user-guide/web-ui.md`](user-guide/web-ui.md).
- **Web UI redesign (R0–R5).** Rebuilt the surface as a chat-first **light full-bleed Console** plus a full **Control Panel** — profiles, keys, scope/precedence-aware **config**, MCP, A2A, skills, context/prompts, providers, memory, dashboard/doctor — opened as htmx drawers *over* the live chat (the chat never reloads), with a top-bar **model/profile switcher** and a HITL-disconnect failsafe (auto-deny after a grace period so a closed tab can't hang a turn). New module `workspace/config_io.py` (scope-aware config writes + per-field precedence).
- **SDK control plane (`SessionController`, D49).** A surface-agnostic Layer-2 class (`runtime/control.py`) owning every runtime mutation — switch model/profile, refresh toolsets, enable/disable/reload MCP, reload skills. CLI and web now drive the identical verbs instead of each re-implementing the rebuild dance. `SessionRuntime` gains a `profile_name` field as the single source of truth.
- **Web parity with the CLI:** the control panel reloads the live MCP/skill catalog into the running chat after an edit (no restart needed), and each skill gets a **"Use in chat"** action (mirrors the CLI `/skill use`).

### Changed

- **The inbound A2A server is started only via `jac a2a serve`** (like `jac web serve`). The `/a2a serve|stop|status|token` REPL slash commands were removed; `/a2a peers` and `/a2a peer add|remove` (outbound peer config) remain.

### Fixed

- **Web MCP toggle now takes effect immediately.** Previously, enabling/disabling an MCP server in the web UI wrote the config file but never rebuilt Gru, so the change did nothing until restart.
- **A2A peer auth discriminator round-trips correctly** — a bearer-auth peer no longer drops its `type` on save/load.

### Removed

- Internal slash-result types `RebuildGru` / `RefreshToolsets` / `StartA2AServer` / `StopA2AServer` and the web's forked `_rebuild` — superseded by the control plane.

### Architecture decisions

- D48 — Web UI as a third surface (local-first, single-user, loopback-bound) · D49 — SDK control plane (`SessionController`); surfaces are thin adapters over one engine

## [0.8.0] - 2026-05-30

**v0.8.0** — end-stage review remediation (R1–R20, see
`docs/design/audit/2026-05-30-review.md`). 697 tests. Pre-1.0 API.

### Added

- **Sub-agent `allowed_tools` enforced** at the Agent layer (R2); **suspend/resume bidirectional comms** via an external `ask_supervisor` tool — the worker run suspends and `respond_to_sub_agent` resumes it (R7b).
- **`SessionDriver`** — a surface-agnostic turn pipeline in `jac.runtime.driver`, plus the **`jac.sdk`** embedding facade, a **`TextDelta`** streaming event, and `suggested_action` on refusal events (R5/R5b/R5c/R5d).
- Budget knobs reject `<= 0` (R11); unknown provider prefixes warn loudly (R12); `forget` finds orphaned bullets (R18).

### Changed

- `runtime/sub_agent.py` split into a `runtime/sub_agent/` package (R7a, imports unchanged); `load_skill` is a capability closure (R14); spawn tools `summarizable=False` (R10).

### Security

- (Landed earlier in the same review: A2A outbound SSRF guard, physically read-only guest toolset, fasta2a fork pin — see v0.7.0-era Phase 1.)

## [0.7.0] - 2026-05-30

**v0.7.0** — interaction modes + compaction control + the MCP loader (Phase F). Pre-1.0 API.

### Added

- **Interaction modes (D23).** `/mode [normal|plan|accept-edits]` switches a session-scoped policy (`jac.runtime.modes`). **Plan Mode** auto-denies every state-changing tool call before you're prompted (reads stay live), so Gru plans instead of executing. **Accept-Edits Mode** auto-applies `write_file`/`edit_file` only. A `⊘ blocked` / `✓ auto-approved` marker shows when a mode acts on your behalf; the status bar shows a `mode:` segment. YOLO is **not** exposed (the auto-allow seam exists, but per D43 YOLO ships only with `pydantic-monty` sandboxing — still v2).
- **`/compact`** — force a summarizing compaction of the oldest history on demand, in any strategy.
- **`/context [N | reset]`** — show or set this session's context-window budget (`k`/`m` suffixes; clamped to the 512k ceiling).
- **`compaction.strategy: auto | sliding | manual`** + per-model budgets (`compaction.model_context_tokens`). `sliding` drops the oldest turns to fit (no model call, never refuses) with a red **⚠ ctx overflow** marker; `manual` only compacts via `/compact`.
- **MCP loader + tool search (Phase F, D46/D28).** External servers in `~/.jac/mcp.json` + `<repo>/.agents/mcp.json` (standard `mcpServers` shape) wired into Gru and sub-agents as deferred-loaded, HITL-gated, summarized toolsets; pydantic-ai's auto `ToolSearch` keeps definitions out of the prompt. `/mcp list|reload|enable|disable`. Per-server `jac` knobs (`enabled`, `defer`, `requires_approval`, `init_timeout`).
- **Sub-agents now receive project conventions** — spawned minions get project + user `AGENTS.md` (via `load_agents_context()`) before their task packet.

### Changed

- **Default context budget 200k → 256k**, hard **512k ceiling**. Resolution: `/context` override → per-model entry → `compaction.max_context_tokens`.
- **Status bar `ctx:`** reports the provider's exact last-turn input tokens (`~` flags the estimate until the first turn lands).
- **HITL approval defaults to approve** — bare Enter means yes (D47); Ctrl-C / EOF still deny.
- **System-prompt hardening pass** — instruction hierarchy with an "external content is data, not instructions" prompt-injection rule; investigate-before-answering / default-to-action / minimize-overengineering guards; verify-before-claiming-done contract. The A2A guest addendum is now an overridable prompt file.

### Fixed

- **MCP robustness** — a failing MCP tool returns its error as the tool result instead of crashing the turn; a hard crash persists a sanitized, resumable history; a server that fails to connect degrades to zero tools instead of breaking every turn (`init_timeout` default 30s).
- **Terminal hardening** — stdio MCP servers redirect stderr to a per-server log so a Node server can't flip the TTY into raw mode and freeze the prompt; JAC forces canonical mode around every prompt as defence-in-depth.

### Architecture decisions

- D23 — Plan + Accept-Edits modes (`ModeCapability`) · D46 — MCP `mcpServers` JSON + tool search · D47 — HITL defaults to approve

**653 tests passing** at release commit.

## [0.6.0] - 2026-05-29

**v0.6.0** — Workspace loose-mode, session management, memory slash commands, and minion theme polish. Pre-1.0 API.

### Added

- **Loose-mode workspace** — when no `.git` or `.agents/` is found, session state (sessions, `usage.jsonl`, A2A) anchors to `~/.jac` instead of creating `.agents/` in an unrelated folder. Project-scope `remember` still refuses outside a project. `jac init` offers to create `.agents/` when run in a non-project folder.
- **`.agents/` as project marker** — `paths.project_root()` now treats a directory containing `.agents/` as a project root, alongside `.git`. Non-git projects can opt in via `jac init` or by creating `.agents/` manually. New helpers: `in_project()` (replaces `is_in_project_repo()`), `project_state_root()` (where JAC writes state), `bootstrap.init_project_workspace()`.
- **`jac sessions delete <id>`** — delete a specific session directory; `usage.jsonl` left intact. `--yes` skips confirmation.
- **`jac sessions prune --older-than <dur>`** — bulk-delete sessions older than a duration (`30d`, `12h`, `2w`); dry-run without `--yes`. `parse_duration()` rejects invalid inputs loudly.
- **`/sessions delete <id>`** and **`/sessions prune <dur> [yes]`** — same from within the REPL; refuses to delete the active session.
- **`/memory [user|project]`** — read-only view of both (or one) memory scopes, section-by-section with audit comments stripped.
- **`/remember <scope> <category> <text>`** and **`/forget <scope> <text>`** — user-driven memory edits without a model call; the typed command is the approval. Same audited single-bullet writes as the agent-initiated tools.
- **`Session.list_summaries()`** — lightweight per-session metadata (message count, creation time) for the session listing; `SessionSummary` dataclass. Listing now shows human-readable creation times and message counts.
- **Atomic session saves** — `Session.save()` writes via a sibling tempfile + rename, matching memory's atomic-write pattern; a kill mid-write can no longer truncate `messages.json`.
- **`@jac_tool` overloads** — bare-decorated tools now keep their original type signature, so slash commands that call the underlying tool function directly (e.g. `/remember` calling `remember()`) type-check correctly.
- **Minion-theme renderer polish** — color grammar now enforced: yellow border = Gru, blue border = minion. Panel labels unified ("sub-agent" → "minion" throughout). Status bar: dark background (`noreverse bg:ansiblack`), yellow key labels, `·` segment separators, `minions:N` counter. Expanded thinking-label pool (20+ minion-adjacent phrases).

### Changed

- `jac sessions` promoted from a single `@app.command` to a full `sessions_cmd` Typer sub-app with `list`, `delete`, and `prune` subcommands.
- `paths.is_in_project_repo()` renamed to `paths.in_project()` (recognises `.agents/` in addition to `.git`); callers updated.
- `paths.project_root()` now returns `Path | None` (was always a `Path`); `paths.find_project_root()` retains the CWD-fallback behaviour for tools.

### Architecture decisions

- `project_state_root()` — one function owns where JAC writes its own state; callers never construct the path themselves.

**Tests** — new `test_workspace_paths.py` (13), `test_memory.py` (42), `test_session.py` (17); `test_slash.py` extended for `/sessions delete|prune`, `/remember`, `/forget`; autouse `_clear_root_caches` fixture in conftest prevents `@cache` cross-test contamination.

## [0.5.0] - 2026-05-28

**v0.5.0** — Phase E: parallel sub-agents + bidirectional comms. Pre-1.0 API.

### Added

- **`spawn_sub_agents(reason, task_summary, spawns)`** — fan out N independent delegations under a single HITL approval; results gathered in parallel; per-spawn tier cascade + depth cap enforced per worker (Phase E.1)
- **Bidirectional sub-agent ↔ main-agent comms (D41)** — sub-agents get `ask_main_agent(reason, question, context)` to pause mid-run and ask one focused clarifying question; the main agent answers with `respond_to_sub_agent(reason, spawn_id, answer)`; hard cap of 5 round-trips per spawn (6th ask returns a graceful "finalize with what you have" directive, not an error). **On by default** since this release — set `cost.sub_agent_bidirectional: false` to opt out.
- **Human-readable spawn IDs** — `minion-1`, `minion-2`, … (session-scoped monotonic counter, resets on REPL teardown) replacing the opaque 8-hex IDs (Phase E.2.2)
- **"Who's asking" on approval panels** — approval panel title shows `approval needed · Gru` or `approval needed · minion-N` so you can tell at a glance which agent is requesting permission during parallel or bidirectional runs (Phase E.2.2)
- **Sub-agent HITL / skills / A2A parity** — destructive tool calls inside a sub-agent (`write_file`, `edit_file`, `delete_file`, `run_shell`, `remember`) now route through the same approval handler as the main agent; skills and A2A capabilities are shared instances so `/skill reload` is visible to sub-agents (Phase E.2.1)
- **Per-spawn lifecycle events in parallel path** — each worker emits `SubAgentSpawned` at start (blue `▶ minion-N` panel) and `SubAgentCompleted` on finish (green `✓ minion-N done · turns=N`) so you see progress without waiting for the full gather (Phase E.3b)
- **Parallel-spawn approval panel** — `spawn_sub_agents` renders a per-spawn summary table (index / label / tier / one-line objective) inside the approval prompt instead of a truncated JSON dump (Phase E.3a)
- **`/spawns`** slash command — lists all parked bidirectional sub-agents with their spawn IDs and pending questions; status bar shows `spawns:N` when anything is in flight
- **Config-change policy** in `CLAUDE.md` — two-path rule for future contributors: default-value flips need no migration (layered fall-through handles it); schema-shape changes add an idempotent migration alongside `migrate_old_profiles`

### Changed

- `_maybe_migrate_old_profiles` (init.py) renamed to `_run_pending_migrations` with the D22 helper as its single current entry — provides an obvious home for future schema migrations

### Architecture decisions

- D41 — Bidirectional sub-agent comms channel (ask/respond tools, round-trip cap, graceful finalize directive)

**487 tests passing** at release commit.

Full changelog: [docs/changelog.md](https://github.com/VENKATESHWARAN-R/JAC/blob/v0.5.0/docs/changelog.md#050---2026-05-28)

## [0.4.0] - 2026-05-27

**v0.4.0** — Phase D: community-format skill loader (D21). Pre-1.0 API.

### Added

- **`SkillsCapability`** — discovers `SKILL.md` files from project (`<repo>/.agents/skills/`), user (`~/.jac/skills/`), and shipped package reference skills; project shadows user shadows package on name collision (shadowed entries visible in `/skill list`)
- **`load_skill(reason, name)`** — on-demand playbook load; skill names + descriptions advertised in the system prompt (2 KB cap with name-only fallback for cache-friendly prompts)
- **`/skill list|use|reload`** — list active and shadowed skills, inject a skill body as the next user turn, or rescan disk without restarting Gru
- **Three reference skills:** `code-review`, `summarize-large-files`, `verify-change` under `src/jac/data/skills/`
- **A2A AgentCard** — each loaded community skill published as an additional `jac-skill-<name>` entry alongside the generic coding-assistant skill
- User guide: [`user-guide/skills.md`](user-guide/skills.md)

### Architecture decisions

- D21 — Community Anthropic skill format (advice only; no `mode: minion`, no hard gating on `tools_required`)

## [0.3.0] - 2026-05-27

**v0.3.0** — Phase A (context-cost foundation) + Phase B (sub-agent tool). Pre-1.0 API.

### Added

- **Phase A — Context-cost foundation:** tool result post-processor (D38) for `@jac_tool(summarizable=True)` tools (`run_shell`, `web_search`, `fetch_url`) above threshold via profile `small` tier when strictly cheaper; originals on disk under `.agents/cache/tool-results/`; prompt-cache stability fix (day-granularity session datetime, not per-second); `/tokens` `cache:` and `summarize:` lines
- **Phase B — Sub-agent tool:** `spawn_sub_agent(reason, task_summary, tier, task_packet)` with tier cascade (small→medium→large, never down), depth cap = 1 (structural), HITL approval, token rollup into session total, `/tokens` `sub_agents:` line; `HookSpec` / `HookResult` models locked for Phase C
- User guide: [`user-guide/cost-controls.md`](user-guide/cost-controls.md)

### Changed

- `run_shell` no longer hard-truncates at 10 KB — large output handled by the summarizer when configured

### Architecture decisions

- D38 — Tool result post-processor · D39 — Sub-agent spawn tier selection · D37 surface locked (hook runner deferred to Phase C)

## [0.2.0] - 2026-05-24

**v0.2.0** — Phase 1.7 coworker experience, partial A2A (PR1–PR3), and a source-tree refactor. Pre-1.0 API.

### Added

- **Phase 1.7 — Coworker experience:** token-aware history compaction (D20), status bar with tier/model/ctx/budget display (D22), slash commands (`/model`, `/profile`, `/budget`, `/tokens`, `/clear`, `/sessions`, `/resume`), tiered model profiles (small/medium/large), approval/clarify deny-with-feedback (D26), token budgets with `/budget extend` (D25), plan checklist persistence on `--resume` (D27), Tavily web search backend when `TAVILY_API_KEY` is set
- **Phase 4 A2A (partial, PR1–PR3):** inbound guest-Gru server (`/a2a serve`, `jac a2a serve`), bearer auth, outbound `a2a_discover` / `a2a_call` tools, pluggable outbound auth strategies (bearer, API key, OAuth2 client credentials), session peers via `/a2a peer add`
- **`ContextCapability`** — mid-session `remember()` / memory.md writes visible on the next turn without rebuilding the agent (`get_instructions()` callable)
- User guide and developer documentation (`docs/user-guide/`, `docs/developer/`, [`module-strategy.md`](developer/module-strategy.md))
- Provider catalog (`src/jac/data/providers.yaml`) with optional `~/.jac/providers.yaml` overlay

### Changed

- **Source restructuring (v0.2):** `hooks`, `approval`, `observability` → `jac.runtime/`; `session_ctx` → `jac.workspace/`; `EventBus` merged into `runtime/events.py`; prompts loader folded into `workspace/paths.py`; slash handlers split one-file-per-command (`handlers/a2a/` subpackage); profiles split into `profiles.py` / `profiles_io.py` / `profiles_crud.py`
- History: exchange-count sliding window replaced by **token-budget-aware** compaction (user-configurable `compaction.max_context_tokens`, default 200k)
- Logfire: PAI **`Instrumentation`** capability on Gru (replaces standalone `instrument_pydantic_ai` call)
- Docs restructure: slimmed `CLAUDE.md`, refreshed `idea.md` and `architecture.md`, Zensical site with user-guide nav
- Docs and metadata: JAC acronym documented as Just Another Companion/CLI
- `.env.template` aligned with the provider catalog (incl. optional `TAVILY_API_KEY`)
- `secrets.backend: keyring` default in shipped `src/jac/data/defaults.yaml`

### Architecture decisions

- D20 — Token-aware compaction · D22 — Tiered profiles + status bar · D24/D30/D31 — A2A · D25 — Token budgets · D26 — HITL feedback · D27 — Plan persistence

## [0.1.2] - 2026-05-22

Alpha release covering the two-scope memory work (Phase 2a.1) and the full
Phase 1.6 tool retrospective. Pre-1.0 API; multiple breaking changes inside.

### Added — memory (Phase 2a.1)

- `~/.jac/memory.md` for user-scope durable facts; project memory remains at `<repo>/.agents/memory.md`
- `remember(reason, content, category, scope)` now requires `scope` explicitly (`"user"` | `"project"`); `scope="project"` outside a git repo raises with an actionable message
- `forget(reason, content, scope)` — symmetric removal with exact-normalized match; ambiguous matches reject loudly
- Session id stamped into every memory entry's audit comment via `jac.runtime.session_ctx` (relocated to `jac.workspace.session_ctx` in v0.2.0)
- Soft "consider pruning" warning surfaced through the tool result once a section exceeds ~25 entries

### Added — tool surface polish (Phase 1.6)

- **Plan tool:** `plan(reason, steps)` / `update_plan(reason, step, status)` / `get_plan(reason)` with a live Rich checklist drawn by the renderer
- **Background processes:** `start_process` / `tail_process` / `kill_process` / `list_processes` (per-process 2000-line ring buffer; auto-reap on session close)
- **`read_file` line ranges:** `start_line` / `end_line` (1-indexed, inclusive); 1000-line cap per call with a `[lines N-M of TOTAL]` header
- **`edit_file` multi-patch:** signature is now `edit_file(reason, path, patches: list[{"old", "new"}])`; sequential application, single atomic write at the end
- **`grep` include/exclude:** glob filters; prefers ripgrep when available, falls back to a Python walker
- **`list_dir` annotated:** sizes for files (B/kB/MB/GB/TB), child counts for dirs; dirs sorted first; optional `show_hidden`
- **Web tools:** `web_search` (DuckDuckGo via `ddgs`) + `fetch_url` (SSRF-protected HTML → Markdown via `markdownify`)
- **`clarify` tool:** structured multi-choice prompt that parallels the HITL approval flow; cancellation surfaces as `RuntimeError`

### Changed

- `edit_file` signature is breaking — single `(old, new)` arguments are gone; pass `patches=[{"old": ..., "new": ...}]` even for one-shot edits
- REPL greet panel shows the JAC version instead of a phase label
- `pydantic-ai-slim` extras now include `web-fetch` (pulls in `markdownify`)

### Architecture decisions

- D15 — Plan tool (visible multi-step intent)
- D16 — Background processes via `ProcessCapability`
- D17 — Structured user prompts via `clarify`
- D18 — Web tools wrap (don't re-export) `pydantic_ai.common_tools` to preserve the `reason:` discipline

## [0.1.0] - 2026-05-21

First **alpha** release (Phase 1 + Phase 1.5). Pre-1.0 API; expect breaking changes.

### Added

- Interactive REPL with rich rendering, status spinner, and Logfire tracing
- Layered config (`~/.jac/config.yaml`, `<repo>/.agents/config.yaml`) and `jac init`
- AGENTS.md auto-loading (repo root + `~/.jac/AGENTS.md`)
- Filesystem, search, and shell tools with `reason:` on every call
- Human-in-the-loop approval for mutating file ops and shell
- Session persistence under `<repo>/.agents/sessions/`; `jac --resume`, `jac sessions`
- Exchange-aware message history sliding window (`ProcessHistory`; superseded by token-aware compaction in v0.2.0)
- Multi-provider profiles and secrets backends (`jac profiles`, `jac keys`)
- `--profile` flag; `--model` override; keyring / dotenv / env-only backends

### Requirements

- Python 3.13+
- Provider API keys (via `jac init` / `jac keys` / env)

[0.9.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.0...v0.1.2
[0.1.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.1.0
