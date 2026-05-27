# Changelog

All notable changes to JAC are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### In flight

- Phase 4 A2A PR4–PR5 (guest token budgets, richer `/a2a status`, retention timer, OIDC/GCP ID token auth strategies)
- Phase C — Deterministic post-flight hooks for sub-agents (D37)

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

[Unreleased]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.0...v0.1.2
[0.1.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.1.0
