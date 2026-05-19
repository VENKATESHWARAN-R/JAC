"""Workspace path resolution.

**All** filesystem paths used by JAC are derived here. No path string should
appear hardcoded anywhere else in the codebase — see CLAUDE.md
"Fail-first, no hardcoding".

Layout:

- User workspace: ``~/.jac/`` (JAC-private)
- Project workspace: ``<project_root>/.agents/`` (community-neutral)
- Project context: ``<project_root>/AGENTS.md`` (auto-loaded)
- User context: ``~/.jac/AGENTS.md`` (auto-loaded)
- Package defaults: shipped under ``jac/`` in the installed package
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from pathlib import Path

# --- User workspace -----------------------------------------------

USER_WORKSPACE: Path = Path.home() / ".jac"
USER_CONFIG_FILE: Path = USER_WORKSPACE / "config.yaml"
USER_CONTEXT_FILE: Path = USER_WORKSPACE / "AGENTS.md"
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


def project_workspace() -> Path:
    return find_project_root() / PROJECT_WORKSPACE_DIRNAME


def project_config_file() -> Path:
    return project_workspace() / "config.yaml"


def project_context_file() -> Path:
    """``<project_root>/AGENTS.md`` — at repo root, NOT inside ``.agents/``."""
    return find_project_root() / PROJECT_CONTEXT_FILENAME


def project_prompts_dir() -> Path:
    return project_workspace() / "prompts"


def project_minions_dir() -> Path:
    return project_workspace() / "minions" / "templates"


def project_skills_dir() -> Path:
    return project_workspace() / "skills"


def project_sessions_dir() -> Path:
    return project_workspace() / "sessions"


# --- Package defaults ---------------------------------------------

@cache
def package_root() -> Path:
    """Filesystem path to the installed JAC package directory."""
    return Path(str(files("jac")))


def package_defaults_file() -> Path:
    return package_root() / "defaults.yaml"


def package_prompts_dir() -> Path:
    return package_root() / "prompts"


def package_minions_dir() -> Path:
    return package_root() / "minions" / "templates"
