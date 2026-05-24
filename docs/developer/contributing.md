# Contributing

> **Audience:** developers working in the JAC repository — including first-time open-source contributors.

Thank you for considering a contribution. JAC is an early-stage project; clear PRs with passing CI are the fastest path to a merge.

## Prerequisites

- **Python 3.13+**
- [`uv`](https://docs.astral.sh/uv/) — dependency and run management
- [`just`](https://just.systems) — optional but recommended (`brew install just`)
- **Git** — fork/clone workflow below

```bash
git clone https://github.com/VENKATESHWARAN-R/JAC.git
cd JAC
uv sync
```

## How to contribute (open source workflow)

You do **not** need commit access to the repo. The usual flow:

1. **Fork** the repository on GitHub (your account gets a copy under `github.com/<you>/JAC`).
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/<your-username>/JAC.git
   cd JAC
   git remote add upstream https://github.com/VENKATESHWARAN-R/JAC.git
   ```
3. **Create a branch** from up-to-date `main`:
   ```bash
   git fetch upstream
   git checkout main
   git merge upstream/main
   git checkout -b my-feature-or-fix
   ```
4. **Make changes** — read the [required reading](#required-reading-before-your-first-pr) section if this is your first PR.
5. **Run checks locally** (see [Continuous integration](#continuous-integration-ci)) — CI will run the same gates; fixing failures locally is faster than push-and-wait.
6. **Commit** with a clear message (what changed and why in one or two sentences).
7. **Push** to your fork and open a **pull request** against `VENKATESHWARAN-R/JAC` → `main`.
8. **Respond to review** — address comments, push new commits to the same branch; CI re-runs automatically.

### Pull request checklist

Before you mark a PR ready for review:

- [ ] `just check` passes (or the equivalent `uv run` commands from CI).
- [ ] If you edited docs: `just docs-build-strict` passes.
- [ ] New behavior has tests in `tests/` where practical.
- [ ] User-visible changes update the relevant `docs/user-guide/` page.
- [ ] Structural or placement changes follow [Module strategy](module-strategy.md).
- [ ] No secrets in the diff (`.env`, API keys, tokens). Use `.env.template` patterns only.
- [ ] `docs/progress.md` updated if you completed or started a tracked phase item.

### What maintainers look for

- **Small, focused PRs** — one logical change per PR is easier to review and revert.
- **Tests** for non-trivial behavior (especially capabilities, A2A, config).
- **Docs in the same PR** as code when behavior is user-visible.
- **No drive-by refactors** unrelated to the stated goal.

Issues and discussions are welcome for bugs, design questions, and “is this in scope?” before large implementations. Check [`docs/progress.md`](../progress.md) and [`docs/architecture.md`](../architecture.md) so v2-deferred work (YOLO, minions runtime, etc.) is not re-implemented by accident.

## Required reading before your first PR

Skim these **before** adding files or opening a PR. They prevent the most common review feedback:

| Doc | Why |
| --- | --- |
| [Module strategy](module-strategy.md) | **Where new code lives** — capabilities vs slash vs runtime vs workspace. Read this before creating any file. |
| [Codebase map](codebase-map.md) | Module inventory and import boundaries. |
| [Capabilities & hooks](capabilities.md) | How Pydantic AI capabilities and the event bus work. |
| [Architecture](../architecture.md) | Fail-first config, paths SSOT, HITL, non-negotiables. |
| [Documentation strategy](../design/documentation-strategy.md) | Which doc file to update for which kind of change. |
| [Idea](../idea.md) | Product scope — what JAC is and is not. |

Repo-root `CLAUDE.md` mirrors many architecture rules for AI-assisted editing; humans should prefer the `docs/` pages above.

## Continuous integration (CI)

GitHub Actions runs on every **pull request** and every **push to `main`**. Both jobs must pass before a maintainer should merge.

Workflow file (repository root): `.github/workflows/ci.yml`

| Job | What it runs | Local equivalent |
| --- | --- | --- |
| **Code** | `ruff format --check`, `ruff check`, `ty check src`, `pytest`, `uv lock --check`, `uv build` | `just check` + `uv lock --check` + `uv build` |
| **Docs** | `zensical build --strict` | `just docs-build-strict` |

No API keys or cloud services are required in CI — tests mock network and providers.

```bash
just check
just docs-build-strict   # if you touched docs/ or zensical.toml
uv lock --check          # if you changed pyproject.toml or uv.lock
uv build                 # optional smoke test before pushing
```

### Documentation site (GitHub Pages)

When changes land on **`main`**, `.github/workflows/docs-deploy.yml` builds the Zensical site and publishes to:

**https://venkateshwaran-r.github.io/JAC/**

PRs only **validate** docs (strict build); they do not deploy. Deployment runs after merge.

**One-time repository setup** (repo owner): GitHub → **Settings** → **Pages** → **Build and deployment** → **Source: GitHub Actions**. Without this, the deploy workflow cannot publish.

### Branch protection (recommended for maintainers)

On `main`, enable:

- Require status checks: **Code (format, lint, types, tests)** and **Docs (strict build)**
- Require pull request reviews before merging (optional while solo; useful once others contribute)

## Day-to-day commands

All recipes live in the root `justfile` at the repo root.

| Command | What it does |
| --- | --- |
| `just` | List recipes |
| `just sync` | `uv sync` — install / refresh deps (including dev group) |
| `just run -- <args>` | `uv run jac …` with `.env` loaded when present |
| `just repl` | `uv run --env-file .env jac` |
| `just resume` | `uv run --env-file .env jac --resume` |
| `just check` | `ruff format --check`, `ruff check`, `ty check src`, `pytest` |
| `just fix` | format + `ruff check --fix` + typecheck |
| `just test` | `pytest tests/ -q` |
| `just docs-serve` | Zensical live site at http://127.0.0.1:8000 |
| `just docs-build` | Static site → `./site` |
| `just docs-build-strict` | Build fails on warnings |
| `just clean` | Remove caches, `site/`, build artifacts |

Example:

```bash
just run -- --profile claude --resume
just check
```

## Project layout

See [Codebase map](codebase-map.md) for the full module inventory. **File placement rules** are in [Module strategy](module-strategy.md).

| Path | Responsibility |
| --- | --- |
| `src/jac/cli/` | Typer app, REPL, renderer, slash commands, `jac init` |
| `src/jac/runtime/` | `build_gru`, session persistence, event bus, usage tracking |
| `src/jac/capabilities/` | Tools and subsystems as Pydantic AI capabilities |
| `src/jac/workspace/` | Paths, layered config, context/memory loaders, bootstrap |
| `src/jac/tools/` | `@jac_tool` decorator and `jac_function_toolset` guard |
| `src/jac/data/` | Shipped `defaults.yaml`, `providers.yaml` |
| `src/jac/prompts/` | `gru_system.md` and other packaged prompts |
| `tests/` | Pytest suites (`test_a2a_*.py`, capability tests, CLI tests) |

## Documentation discipline

Read [Documentation strategy](../design/documentation-strategy.md) before adding pages.

When you change behavior:

1. **User-visible** → update the relevant [`docs/user-guide/`](../user-guide/) page.
2. **Implementation status** → update [`docs/progress.md`](../progress.md) checkboxes.
3. **Structural design decision** → update [`docs/architecture.md`](../architecture.md) §11.
4. **Released behavior** → add an entry to [`docs/changelog.md`](../changelog.md).
5. **Doc/code alignment** → touch the row in [`docs/design/audit/drift-matrix.md`](../design/audit/drift-matrix.md).

## Code conventions

These are non-negotiable; full rationale is in [architecture.md](../architecture.md) and repo-root `CLAUDE.md`.

- **Fail-first** — missing required config raises `JacConfigError` with fix instructions. No silent defaults for models, providers, or paid APIs.
- **Paths** — use `jac.workspace.paths`; never hardcode `~/.jac` or `.agents` strings elsewhere.
- **Tools** — every Gru tool uses `@jac_tool` with `reason: str` as the first non-`ctx` parameter (`jac.tools.decorator`).
- **Capabilities** — cross-cutting behavior is a Pydantic AI `Capability`, not ad-hoc lifecycle classes.
- **HITL** — use `ApprovalRequiredToolset` + `make_approval_handler(bus)`; do not build a custom approval system.
- **Event bus** — CLI renders from `Hooks` → `EventBus` → `CliRenderer`; do not `await gru.run` directly in the REPL control path.
- **Tracing** — model/tool/minion spans go through Logfire (`capabilities/observability.py`).

## Tests

```bash
uv run pytest tests/ -q
# or
just check
```

Add tests alongside new capabilities. A2A has dedicated coverage (`tests/test_a2a_*.py`, guest toolset isolation, auth strategies).

## Local docs

```bash
just docs-serve
```

Edit under `docs/`; navigation is in `zensical.toml`. User-guide paths referenced from [`docs/index.md`](../index.md).

## Reference material

Read-only clones for design inspiration (not vendored): see `architecture.md` §12 — pydantic-ai-harness, community skills format, A2A spec links.
