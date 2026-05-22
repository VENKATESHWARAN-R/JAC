"""Workspace path resolution.

**All** filesystem paths used by JAC are derived here. No path string should
appear hardcoded anywhere else in the codebase — see CLAUDE.md
"Fail-first, no hardcoding".

Layout:

- User workspace: ``~/.jac/`` (JAC-private)
- Project workspace: ``<project_root>/.agents/`` (community-neutral)
- Project context: ``<project_root>/AGENTS.md`` (auto-loaded)
- User context: ``~/.jac/AGENTS.md`` (auto-loaded)
- Package data: shipped under ``jac/data/`` in the installed package (defaults + provider catalog)
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from pathlib import Path

# --- User workspace -----------------------------------------------

USER_WORKSPACE: Path = Path.home() / ".jac"
USER_CONFIG_FILE: Path = USER_WORKSPACE / "config.yaml"
USER_PROVIDERS_FILE: Path = USER_WORKSPACE / "providers.yaml"
USER_PROVIDERS_EXAMPLE_FILE: Path = USER_WORKSPACE / "providers.yaml.example"
USER_CONTEXT_FILE: Path = USER_WORKSPACE / "AGENTS.md"
USER_MEMORY_FILE: Path = USER_WORKSPACE / "memory.md"
USER_PROMPTS_DIR: Path = USER_WORKSPACE / "prompts"
USER_MINIONS_DIR: Path = USER_WORKSPACE / "minions" / "templates"
USER_SKILLS_DIR: Path = USER_WORKSPACE / "skills"
USER_HISTORY_FILE: Path = USER_WORKSPACE / "history"

# --- Project workspace --------------------------------------------

PROJECT_WORKSPACE_DIRNAME = ".agents"
PROJECT_CONTEXT_FILENAME = "AGENTS.md"


@cache
def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: CWD) looking for a ``.git`` directory.

    Returns the directory containing ``.git`` as the project root. Falls back
    to ``start`` (resolved) if no ``.git`` is found in any ancestor.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return here


def is_in_project_repo(start: Path | None = None) -> bool:
    """``True`` iff a ``.git`` directory exists at or above ``start`` (default: CWD).

    Used by scope-aware code paths (e.g. project-memory writes) that need to
    refuse to run outside a tracked repo, rather than silently anchoring to
    whatever CWD happens to be.
    """
    here = (start or Path.cwd()).resolve()
    return any((candidate / ".git").exists() for candidate in (here, *here.parents))


def project_workspace() -> Path:
    return find_project_root() / PROJECT_WORKSPACE_DIRNAME


def project_config_file() -> Path:
    return project_workspace() / "config.yaml"


def project_context_file() -> Path:
    """``<project_root>/AGENTS.md`` — at repo root, NOT inside ``.agents/``."""
    return find_project_root() / PROJECT_CONTEXT_FILENAME


def project_memory_file() -> Path:
    """``<project_root>/.agents/memory.md`` — JAC-managed project memory.

    Distinct from ``AGENTS.md``: this file is written by Gru via the
    ``remember`` tool, with HITL approval. We never mutate ``AGENTS.md``.
    """
    return project_workspace() / "memory.md"


def project_prompts_dir() -> Path:
    return project_workspace() / "prompts"


def project_minions_dir() -> Path:
    return project_workspace() / "minions" / "templates"


def project_skills_dir() -> Path:
    return project_workspace() / "skills"


def project_sessions_dir() -> Path:
    return project_workspace() / "sessions"


def resolve_under_project(path: str | Path) -> Path:
    """Resolve ``path`` to an absolute path.

    Absolute paths are returned as-is. Relative paths are anchored to the
    project root (``<repo>/``), so an agent can say ``"src/foo.py"`` without
    needing to know the absolute path or even the current working directory.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    return find_project_root() / p


# --- Package data (shipped defaults) ------------------------------


@cache
def package_root() -> Path:
    """Filesystem path to the installed JAC package directory."""
    return Path(str(files("jac")))


def package_data_dir() -> Path:
    """Shipped YAML defaults and provider catalog (not Python modules)."""
    return package_root() / "data"


def package_defaults_file() -> Path:
    return package_data_dir() / "defaults.yaml"


def package_providers_file() -> Path:
    return package_data_dir() / "providers.yaml"


def package_prompts_dir() -> Path:
    return package_root() / "prompts"


def package_minions_dir() -> Path:
    return package_root() / "minions" / "templates"
