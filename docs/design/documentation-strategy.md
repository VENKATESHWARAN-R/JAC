# Documentation strategy

> **Audience:** maintainers and contributors deciding *where* a fact should live.

JAC's docs site is built with [Zensical](https://zensical.org/) (`just docs-serve`). Navigation is declared in `zensical.toml` at the repo root. This page is the contract for audiences, single sources of truth (SSOT), writing rules, and how we migrate legacy paths.

## Audiences

| Audience | Goal | Primary pages | Tone |
| --- | --- | --- | --- |
| **End user** | Install, configure, run sessions, use slash commands and tools safely | [Getting started](../user-guide/getting-started.md) | Task-oriented, copy-paste commands, minimal internals |
| **Operator** | Expose or call JAC over A2A, manage peers and tokens | [`user-guide/a2a-operator.md`](../user-guide/a2a-operator.md) | Security-first, explicit defaults, no hand-waving on auth |
| **Contributor** | Change code without breaking conventions | [Contributing](../developer/contributing.md), root `CLAUDE.md` | Prescriptive: fail-first, capabilities, paths |
| **Designer / maintainer** | Understand vision, locked decisions, roadmap, doc drift | [`idea.md`](../idea.md), [`architecture.md`](../architecture.md), [`progress.md`](../progress.md), [`design/`](.) | Decisions stated plainly; open questions marked |

**Rule:** If a paragraph serves two audiences, split it or link out. User-guide pages must not require reading `architecture.md` to complete a task.

## Single source of truth (SSOT)

| Topic | SSOT (authoritative) | User guide summarizes? | Notes |
| --- | --- | --- | --- |
| Product vision & scope | [`idea.md`](../idea.md) | No | What JAC is and is not |
| Locked design & decisions | [`architecture.md`](../architecture.md) §11 | No | Decision IDs (D14, D20, …) live here |
| Implementation status | [`progress.md`](../progress.md) | No | Live dashboard; update when work lands. Detailed history lives in `progress-history.md`, `progress-a2a.md`, and `progress-roadmap.md` |
| Released behavior (versions) | [`changelog.md`](../changelog.md) | Optional one-liner | User-facing releases |
| Install & first run | [`user-guide/getting-started.md`](../user-guide/getting-started.md) | — | README duplicates quickstart; link to docs |
| CLI commands & slash | [`user-guide/cli-reference.md`](../user-guide/cli-reference.md) | — | Must match `jac.cli` and `jac.cli.slash` |
| Config (profiles, budgets, compaction) | [`user-guide/configuration.md`](../user-guide/configuration.md) | — | Schema detail in code (`jac.config`, `jac.profiles`) |
| Sessions & memory | [`user-guide/sessions-and-memory.md`](../user-guide/sessions-and-memory.md) | — | Paths from `jac.workspace.paths` |
| A2A operations | [`user-guide/a2a-operator.md`](../user-guide/a2a-operator.md) | — | Partial Phase 4; mark in-flight features |
| Module layout & features | [`developer/codebase-map.md`](../developer/codebase-map.md) | No | As-built tree; update when packages move |
| Capability pattern | [`developer/capabilities.md`](../developer/capabilities.md) | No | How to add tools/capabilities |
| Contributing workflow | [`developer/contributing.md`](../developer/contributing.md) | No | `just` recipes, CI, doc discipline |
| Path constants | `src/jac/workspace/paths.py` | User guide lists outcomes only | Never duplicate path logic in prose |
| Agent instructions | `src/jac/prompts/gru_system.md` | No | Gru behavior; not end-user docs |
| AI assistant rules (repo root) | `CLAUDE.md` (repo root) | No | Mirrors architecture non-negotiables |

When code and docs disagree, **code wins until the doc is fixed** — track gaps in [`audit/drift-matrix.md`](audit/drift-matrix.md).

## Writing rules

1. **No placeholders** — no `TODO`, `TBD`, or "coming soon" without a link to `progress.md` and a phase name.
2. **Relative links** — use paths under `docs/` (e.g. `[Configuration](../user-guide/configuration.md)`), not absolute GitHub URLs, except for external specs (A2A protocol, Pydantic AI).
3. **Version honesty** — state what ships in **v0.2.0** (Phase 1.7 complete). Mark Phase 4 A2A as partial where PR4/PR5 are still queued (`progress.md`).
4. **Commands are verified** — every `jac` / slash example must exist in `src/jac/cli/`. Prefer `just run -- …` in contributor docs when `.env` matters.
5. **One format per category** — YAML for human config, JSON/JSONL for machine state, Markdown for prose (see [`architecture.md`](../architecture.md) format table).
6. **Tables for reference** — CLI flags, tools, env vars, and file locations belong in tables, not bullet lists of identifiers.
7. **Admonitions for danger** — `--unsafe` A2A, approval-gated writes, budget hard-stops: use Zensical admonitions (`!!! warning`).
8. **Same change, same PR** — user-visible behavior → user-guide + `progress.md`; structural decision → `architecture.md` §11; release → `changelog.md`.

## Site structure (nav)

Defined in `zensical.toml` at the repo root:

- **Overview** — `index.md` (routing table only)
- **User Guide** — getting started, CLI, configuration, sessions & memory, examples, A2A operator
- **Developer** — contributing, codebase map, capabilities
- **Design** — idea, architecture, progress dashboard + archives, changelog, documentation strategy, drift matrix

The old `docs/usage/` placeholder stubs have been removed. All user-facing content is under `docs/user-guide/`.

## Migration phases

| Phase | Scope | Status |
| --- | --- | --- |
| **M0 — Scaffold** | `zensical.toml`, `index.md`, empty `user-guide/`, `developer/`, `design/` | Done |
| **M1 — User guide** | All `user-guide/*.md` pages with shipped behavior | This batch |
| **M2 — Developer** | `contributing.md`, `codebase-map.md`, `capabilities.md` | This batch |
| **M3 — Design ops** | `documentation-strategy.md`, `drift-matrix.md` | This batch |
| **M4 — Redirect cleanup** | `docs/usage/*` proxy stubs deleted; directory removed | Done |
| **M5 — Strict build** | `just docs-build-strict` in CI; fix warnings | Done (`.github/workflows/ci.yml`) |
| **M6 — ADR split** | Optional `docs/design/decisions/Dnn-*.md` extracted from architecture §11 | Future |

## Drift control

After any release or large feature merge:

1. Update [`audit/drift-matrix.md`](audit/drift-matrix.md) rows touched by the change.
2. Run `just docs-build-strict` locally before opening a PR that edits docs.
3. If a feature is removed, delete or rewrite the user-guide section in the same PR — do not leave stale instructions.
