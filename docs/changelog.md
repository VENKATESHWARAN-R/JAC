# Changelog

All notable changes to JAC are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Phase 1.7 — Coworker experience:** token-aware history compaction (D20), status bar with tier/model display (D22), slash commands (`/model`, `/profile`, `/budget`, `/tokens`, `/clear`, `/sessions`, `/resume`), tiered model profiles (small/medium/large), approval/clarify deny-with-feedback (D26), token budgets with `/budget extend` (D25), plan checklist persistence on `--resume` (D27), Tavily web search backend when `TAVILY_API_KEY` is set
- **Phase 4 A2A (partial):** inbound guest-Gru server (`/a2a serve`, `jac a2a serve`), bearer auth, outbound `a2a_discover` / `a2a_call` tools, pluggable outbound auth strategies (bearer, API key, OAuth2 client credentials), session peers via `/a2a peer add`
- User guide and developer documentation (`docs/user-guide/`, `docs/developer/`)
- Provider catalog (`src/jac/data/providers.yaml`) with optional `~/.jac/providers.yaml` overlay

### Changed

- Docs restructure: slimmed `CLAUDE.md`, refreshed `idea.md` and `architecture.md`, new Zensical nav
- Docs and metadata: JAC acronym documented as Just Another Companion/CLI
- `.env.template` aligned with the provider catalog
- `secrets.backend: keyring` default moved to shipped `src/jac/data/defaults.yaml`

### In flight

- Phase 4 A2A PR4–PR5 (status enrichment, OIDC/GCP ID token auth strategies)
- Phase 3 Skills (community Anthropic format — D21)

## [0.1.2] - 2026-05-22

Alpha release covering the two-scope memory work (Phase 2a.1) and the full
Phase 1.6 tool retrospective. Pre-1.0 API; multiple breaking changes inside.

### Added — memory (Phase 2a.1)

- `~/.jac/memory.md` for user-scope durable facts; project memory remains at `<repo>/.agents/memory.md`
- `remember(reason, content, category, scope)` now requires `scope` explicitly (`"user"` | `"project"`); `scope="project"` outside a git repo raises with an actionable message
- `forget(reason, content, scope)` — symmetric removal with exact-normalized match; ambiguous matches reject loudly
- Session id stamped into every memory entry's audit comment via `jac.runtime.session_ctx`
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
- Exchange-aware message history sliding window (`ProcessHistory`)
- Multi-provider profiles and secrets backends (`jac profiles`, `jac keys`)
- `--profile` flag; `--model` override; keyring / dotenv / env-only backends

### Requirements

- Python 3.13+
- Provider API keys (via `jac init` / `jac keys` / env)

[Unreleased]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.0...v0.1.2
[0.1.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.1.0
