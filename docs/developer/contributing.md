# Contributing

> **Audience:** developers working in the JAC repository.

## Prerequisites

- **Python 3.13+**
- [`uv`](https://docs.astral.sh/uv/) — dependency and run management
- [`just`](https://just.systems) — optional but recommended (`brew install just`)

```bash
git clone https://github.com/VENKATESHWARAN-R/JAC.git
cd JAC
uv sync
```

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

See [Codebase map](codebase-map.md) for the full module inventory.

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
