"""Layered prompt loading.

Resolution order (first hit wins):

1. ``<project_root>/.agents/prompts/<name>.md``
2. ``~/.jac/prompts/<name>.md``
3. ``<package>/prompts/<name>.md`` (shipped default; must always exist)

Raises ``FileNotFoundError`` if even the package default is missing — that's
a packaging bug, not a user error.
"""

from __future__ import annotations

from pathlib import Path

from . import paths


def load_prompt(name: str) -> str:
    """Return the prompt body for ``name`` (no extension). First hit wins."""
    for candidate in _prompt_candidates(name):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"Prompt '{name}.md' not found in project, user, or package locations. "
        "This is a packaging bug — the shipped default appears to be missing."
    )


def _prompt_candidates(name: str) -> list[Path]:
    filename = f"{name}.md"
    return [
        paths.project_prompts_dir() / filename,
        paths.USER_PROMPTS_DIR / filename,
        paths.package_prompts_dir() / filename,
    ]
