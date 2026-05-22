# JAC — common dev tasks
#
# Install just: https://just.systems  (e.g. `brew install just`)
# Everything goes through `uv run`, so an activated venv is optional.

# default recipe = list all recipes
default:
    @just --list

# install / refresh dependencies (including dev group)
sync:
    uv sync

# install with dev group (alias to `sync` for clarity)
sync-dev:
    uv sync --group dev

# ---------------------------------------------------------------------------
# Run the app
# ---------------------------------------------------------------------------

# run jac; load .env when present; forward args (e.g. `just run --help`, `just run --profile claude`)
run *ARGS:
    @if [ -f .env ]; then uv run --env-file .env jac {{ARGS}}; else uv run jac {{ARGS}}; fi

# launch the REPL with .env loaded
repl:
    uv run --env-file .env jac

# resume the latest session in this project
resume:
    uv run --env-file .env jac --resume

# ---------------------------------------------------------------------------
# Lint + format + typecheck
# ---------------------------------------------------------------------------

# ruff lint check (no fixes)
lint:
    uv run ruff check .

# ruff lint with autofix
lint-fix:
    uv run ruff check --fix .

# ruff format check
fmt:
    uv run ruff format --check .

# ruff format (rewrite)
fmt-fix:
    uv run ruff format .

# ty typechecker
typecheck:
    uv run ty check src

# pytest
test:
    uv run pytest tests/ -q

# aggregate: format check + lint + typecheck (CI-style, no writes)
check: fmt lint typecheck test

# aggregate: format + lint --fix (typecheck still read-only)
fix: fmt-fix lint-fix typecheck

# ---------------------------------------------------------------------------
# Documentation site (Zensical)
# ---------------------------------------------------------------------------

# serve docs with live reload at http://127.0.0.1:8000
docs-serve:
    uv run zensical serve

# build the static site to ./site
docs-build:
    uv run zensical build

# build with strict mode (fail on warnings)
docs-build-strict:
    uv run zensical build --strict

# remove the built site directory
docs-clean:
    rm -rf site

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

# show the jac version
version:
    uv run jac --help | head -1

# remove caches and build artefacts
clean: docs-clean
    rm -rf .ruff_cache .pytest_cache build dist .cache
