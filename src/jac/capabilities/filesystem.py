"""Filesystem tools — read/write/edit/list.

Read and list are direct-call. **Write and edit are approval-required** —
they mutate the workspace and must surface through the HITL approval flow
(see :mod:`jac.capabilities.approval`).

Paths may be absolute or project-relative; relative paths anchor to the
project root via :func:`jac.workspace.paths.resolve_under_project`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition

from jac.tools import jac_function_toolset, jac_tool
from jac.workspace.paths import resolve_under_project

_MAX_READ_BYTES = 1_000_000  # 1 MB — refuse to read anything larger
_RISKY_TOOLS = {"write_file", "edit_file"}


@jac_tool
def read_file(reason: str, path: str) -> str:
    """Read the text contents of a file.

    Args:
        reason: One-sentence justification for this call.
        path: File path, absolute or project-relative.
    """
    p = resolve_under_project(path)
    if not p.exists():
        raise FileNotFoundError(f"no such path: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"not a file: {p}")
    size = p.stat().st_size
    if size > _MAX_READ_BYTES:
        raise ValueError(f"file too large: {size} bytes (max {_MAX_READ_BYTES})")
    return p.read_text(encoding="utf-8")


@jac_tool
def write_file(reason: str, path: str, content: str) -> str:
    """Write ``content`` to ``path``, overwriting any existing file.

    Creates parent directories if needed. **Approval-required.**
    """
    p = resolve_under_project(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


@jac_tool
def edit_file(reason: str, path: str, old: str, new: str) -> str:
    """Replace exactly one occurrence of ``old`` with ``new`` in the file.

    Errors if ``old`` is missing or appears more than once. Add surrounding
    context to make the match unique. **Approval-required.**
    """
    p = resolve_under_project(path)
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {p}")
    text = p.read_text(encoding="utf-8")
    occurrences = text.count(old)
    if occurrences == 0:
        raise ValueError(f"`old` not found in {p}")
    if occurrences > 1:
        raise ValueError(
            f"`old` appears {occurrences} times in {p} (must be unique). "
            "Add more surrounding context to make the match unique."
        )
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"edited {p} (1 replacement)"


@jac_tool
def list_dir(reason: str, path: str = ".") -> list[str]:
    """List entries in a directory.

    Directories are returned with a trailing ``/``. Default is project root.
    """
    p = resolve_under_project(path)
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {p}")
    entries: list[str] = []
    for child in sorted(p.iterdir()):
        entries.append(f"{child.name}/" if child.is_dir() else child.name)
    return entries


def _is_mutating(ctx: Any, tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    return tool_def.name in _RISKY_TOOLS


@dataclass
class FilesystemCapability(AbstractCapability[Any]):
    """Read/write filesystem tools. ``write_file`` and ``edit_file`` are HITL-gated."""

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(read_file, write_file, edit_file, list_dir)
        return toolset.approval_required(_is_mutating)
