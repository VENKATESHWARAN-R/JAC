"""Search tools — grep and glob.

Read-only (no approval required). Both anchor to the project root and skip
the usual noise directories (``.git``, ``node_modules``, ``.venv``, etc.).

``grep`` prefers ``ripgrep`` (``rg``) when it's on ``PATH``: it's faster,
honors ``.gitignore`` for free, and handles include/exclude globs natively.
We fall back to a Python regex walker when ``rg`` isn't available so the
tool stays portable. Output shape is identical either way.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
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
def grep(
    reason: str,
    pattern: str,
    path: str = ".",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[str]:
    """Search for ``pattern`` (a regex) in text files under ``path``.

    Uses ``ripgrep`` when available for speed and ``.gitignore`` awareness;
    falls back to a Python walker that uses :func:`re.search` and skips
    common noise directories.

    Args:
        reason: One-sentence justification.
        pattern: Regex pattern. With the ripgrep backend this is rg's
            default regex flavor; with the Python fallback it's
            :mod:`re` syntax. Stick to the common subset to be safe.
        path: Search root, absolute or project-relative. Default: project root.
        include: Optional list of glob filters to keep (e.g.
            ``["*.py", "*.pyi"]``). When set, only files matching at least
            one glob are searched.
        exclude: Optional list of glob filters to skip (e.g.
            ``["**/*.lock", "**/migrations/*"]``).

    Returns:
        Up to 100 matches as ``"relpath:lineno:line"`` strings.
    """
    root = resolve_under_project(path)
    if not root.exists():
        raise FileNotFoundError(f"no such path: {root}")
    project_root = find_project_root()
    include_globs = list(include) if include else []
    exclude_globs = list(exclude) if exclude else []

    if shutil.which("rg"):
        return _grep_ripgrep(pattern, root, project_root, include_globs, exclude_globs)
    return _grep_python(pattern, root, project_root, include_globs, exclude_globs)


def _grep_ripgrep(
    pattern: str,
    root: Path,
    project_root: Path,
    include_globs: list[str],
    exclude_globs: list[str],
) -> list[str]:
    cmd: list[str] = [
        "rg",
        "--with-filename",
        "--line-number",
        "--no-heading",
        "--color=never",
        "--max-count",
        str(_GREP_MATCH_LIMIT),
    ]
    for g in include_globs:
        cmd.extend(["--glob", g])
    for g in exclude_globs:
        cmd.extend(["--glob", f"!{g}"])
    cmd.extend(["--regexp", pattern, "--", str(root)])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    # rg exits 1 when there are no matches — that's not an error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"rg exited {proc.returncode}: {proc.stderr.strip() or 'no stderr'}"
        )
    results: list[str] = []
    for line in proc.stdout.splitlines():
        # rg format: PATH:LINENO:CONTENT
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        fp_str, lineno, content = parts
        try:
            rel = Path(fp_str).resolve().relative_to(project_root)
        except ValueError:
            rel = Path(fp_str)
        snippet = (
            content
            if len(content) <= _GREP_LINE_LIMIT_CHARS
            else content[:_GREP_LINE_LIMIT_CHARS] + "…"
        )
        results.append(f"{rel}:{lineno}:{snippet}")
        if len(results) >= _GREP_MATCH_LIMIT:
            break
    return results


def _grep_python(
    pattern: str,
    root: Path,
    project_root: Path,
    include_globs: list[str],
    exclude_globs: list[str],
) -> list[str]:
    rx = re.compile(pattern)
    results: list[str] = []
    targets: Iterator[Path] = iter([root]) if root.is_file() else _walk_files(root)
    for fp in targets:
        if len(results) >= _GREP_MATCH_LIMIT:
            break
        try:
            rel = fp.relative_to(project_root)
        except ValueError:
            rel = fp
        rel_str = str(rel)
        if include_globs and not any(
            fnmatch.fnmatch(rel_str, g) or fnmatch.fnmatch(fp.name, g)
            for g in include_globs
        ):
            continue
        if exclude_globs and any(
            fnmatch.fnmatch(rel_str, g) or fnmatch.fnmatch(fp.name, g)
            for g in exclude_globs
        ):
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                snippet = (
                    line
                    if len(line) <= _GREP_LINE_LIMIT_CHARS
                    else line[:_GREP_LINE_LIMIT_CHARS] + "…"
                )
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
