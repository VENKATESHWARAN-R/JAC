"""Search tools — grep and glob.

Read-only (no approval required). Both anchor to the project root and skip
the usual noise directories (``.git``, ``node_modules``, ``.venv``, etc.).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.tools import jac_function_toolset, jac_tool
from jac.workspace.paths import find_project_root, resolve_under_project

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".next",
        "target",
    }
)
_SKIP_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".o",
        ".a",
        ".class",
        ".jar",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".mp3",
        ".mp4",
        ".mov",
    }
)

_GREP_MATCH_LIMIT = 100
_GREP_LINE_LIMIT_CHARS = 200
_GLOB_RESULT_LIMIT = 200


def _walk_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in _SKIP_SUFFIXES:
            continue
        yield p


@jac_tool
def grep(reason: str, pattern: str, path: str = ".") -> list[str]:
    """Search for ``pattern`` (a Python regex) in text files under ``path``.

    Returns up to 100 matches as ``"relpath:lineno:line"`` strings. Binary
    files and common noise directories are skipped.
    """
    root = resolve_under_project(path)
    if not root.exists():
        raise FileNotFoundError(f"no such path: {root}")

    rx = re.compile(pattern)
    project_root = find_project_root()
    results: list[str] = []

    targets: Iterator[Path] = iter([root]) if root.is_file() else _walk_files(root)
    for fp in targets:
        if len(results) >= _GREP_MATCH_LIMIT:
            break
        try:
            text = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                try:
                    rel = fp.relative_to(project_root)
                except ValueError:
                    rel = fp
                snippet = line if len(line) <= _GREP_LINE_LIMIT_CHARS else line[:_GREP_LINE_LIMIT_CHARS] + "…"
                results.append(f"{rel}:{lineno}:{snippet}")
                if len(results) >= _GREP_MATCH_LIMIT:
                    break
    return results


@jac_tool
def glob(reason: str, pattern: str) -> list[str]:
    """Find files matching glob ``pattern``, relative to project root.

    Supports ``**`` for recursive matching. Returns up to 200 paths, sorted.
    """
    root = find_project_root()
    cleaned = pattern.lstrip("/")
    results: list[str] = []
    for p in root.glob(cleaned):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        results.append(str(p.relative_to(root)))
        if len(results) >= _GLOB_RESULT_LIMIT:
            break
    return sorted(results)


@dataclass
class SearchCapability(AbstractCapability[Any]):
    """Read-only search tools: ``grep`` and ``glob``."""

    def get_toolset(self) -> Any:
        return jac_function_toolset(grep, glob)
