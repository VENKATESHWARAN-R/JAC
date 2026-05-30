"""Filesystem tools — read/write/edit/list.

Read and list are direct-call. **Write and edit are approval-required** —
they mutate the workspace and must surface through the HITL approval flow
(see :mod:`jac.runtime.approval`).

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

_MAX_READ_BYTES = 1_000_000  # 1 MB — refuse to read whole-file beyond this
_MAX_READ_LINES = 1_000  # hard cap on lines per call, range or not
_RISKY_TOOLS = {"write_file", "edit_file"}


@jac_tool
def read_file(
    reason: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read the text contents of a file, optionally a line range.

    Without ``start_line`` / ``end_line``, returns the whole file (up to
    a 1MB byte cap). With either bound supplied, returns the requested
    slice (1-indexed, inclusive on both ends) capped at 1000 lines so a
    single call can't blow the context window.

    Args:
        reason: One-sentence justification for this call.
        path: File path, absolute or project-relative.
        start_line: First line to include (1-indexed). Defaults to 1.
        end_line: Last line to include (1-indexed, inclusive). Defaults
            to the end of the file.

    Returns:
        The requested text. When a range was supplied or the file
        exceeds the line cap, the result is prefixed with a one-line
        header (``[lines N-M of TOTAL]``) so you know what you got.
    """
    p = resolve_under_project(path)
    if not p.exists():
        raise FileNotFoundError(f"no such path: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"not a file: {p}")

    range_requested = start_line is not None or end_line is not None
    if not range_requested:
        size = p.stat().st_size
        if size > _MAX_READ_BYTES:
            raise ValueError(
                f"file too large: {size} bytes (max {_MAX_READ_BYTES}). "
                "Pass `start_line` / `end_line` to read a slice instead."
            )

    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    total = len(lines)

    if range_requested:
        first = 1 if start_line is None else start_line
        last = total if end_line is None else end_line
        if first < 1:
            raise ValueError(f"`start_line` must be >= 1; got {first}.")
        if first > total:
            raise ValueError(f"`start_line` {first} exceeds file length ({total} lines).")
        if last < first:
            raise ValueError(f"`end_line` ({last}) must be >= `start_line` ({first}).")
        last = min(last, total)
        if last - first + 1 > _MAX_READ_LINES:
            raise ValueError(
                f"requested {last - first + 1} lines; max per call is "
                f"{_MAX_READ_LINES}. Narrow the range and call again."
            )
        body = "".join(lines[first - 1 : last])
        return f"[lines {first}-{last} of {total}]\n{body}"

    # No range requested. If the file is short, return as-is; if long-but-
    # under-1MB, truncate to MAX_READ_LINES with a header so the model knows.
    if total > _MAX_READ_LINES:
        body = "".join(lines[:_MAX_READ_LINES])
        return (
            f"[lines 1-{_MAX_READ_LINES} of {total} — file exceeds the per-call "
            "line cap; pass `start_line` / `end_line` to read the rest]\n"
            f"{body}"
        )
    return text


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
def edit_file(reason: str, path: str, patches: list[dict[str, str]]) -> str:
    """Apply one or more ``(old, new)`` patches to a file atomically.

    Each patch is a dict ``{"old": ..., "new": ...}`` whose ``old`` must
    appear **exactly once** in the file at the time the patch is
    applied. Patches are applied in order against the in-memory text;
    the file is written exactly once at the end. Any patch that fails
    aborts the whole call — the file is never partially written.

    Pass a single-element list for a one-shot replacement. For widespread
    changes (e.g. an import addition *and* a function rename), pass
    multiple patches in one call so the file is only opened and written
    once. **Approval-required.**

    Args:
        reason: One-sentence justification.
        path: File path, absolute or project-relative.
        patches: List of ``{"old": str, "new": str}`` dicts. Must be non-empty.

    Returns:
        Confirmation string ("edited PATH (N replacements)").
    """
    p = resolve_under_project(path)
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {p}")
    if not patches:
        raise ValueError("`patches` must contain at least one patch.")

    text = p.read_text(encoding="utf-8")
    for i, patch in enumerate(patches, start=1):
        try:
            old = patch["old"]
            new = patch["new"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"patch #{i} must be a dict with `old` and `new` string keys."
            ) from exc
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError(f"patch #{i}: `old` and `new` must both be strings.")
        if old == new:
            raise ValueError(f"patch #{i}: `old` and `new` are identical — nothing to do.")
        occurrences = text.count(old)
        if occurrences == 0:
            raise ValueError(
                f"patch #{i}: `old` not found in {p} at this stage. "
                "Earlier patches may have changed the surrounding text — "
                "re-read the file and rebase the patch."
            )
        if occurrences > 1:
            raise ValueError(
                f"patch #{i}: `old` appears {occurrences} times in {p} "
                "(must be unique). Add more surrounding context."
            )
        text = text.replace(old, new, 1)

    p.write_text(text, encoding="utf-8")
    n = len(patches)
    return f"edited {p} ({n} replacement{'s' if n != 1 else ''})"


@jac_tool
def list_dir(reason: str, path: str = ".", show_hidden: bool = False) -> list[str]:
    """List entries in a directory, annotated with size or child count.

    Directories are returned as ``"name/ (N entries)"``; files as
    ``"name (SIZE)"`` where SIZE is human-readable (``"2.3kB"``,
    ``"1.1MB"``). Subdirectories appear before files; within each group
    entries are sorted alphabetically. Default is the project root.

    Hidden entries (``.foo``) are skipped unless ``show_hidden=True``.
    The child-count for a directory is the number of entries one level
    deep — not a recursive walk.

    Args:
        reason: One-sentence justification.
        path: Directory path, absolute or project-relative. Defaults to
            the project root.
        show_hidden: Include dotfiles / dotdirs (default ``False``).

    Returns:
        Annotated entry list.
    """
    p = resolve_under_project(path)
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {p}")
    dirs: list[str] = []
    files: list[str] = []
    for child in sorted(p.iterdir()):
        if not show_hidden and child.name.startswith("."):
            continue
        if child.is_dir():
            try:
                count = sum(
                    1 for sub in child.iterdir() if show_hidden or not sub.name.startswith(".")
                )
                annotation = f"({count} entr{'y' if count == 1 else 'ies'})"
            except (PermissionError, OSError):
                annotation = "(unreadable)"
            dirs.append(f"{child.name}/ {annotation}")
        else:
            try:
                size = child.stat().st_size
                annotation = f"({_format_size(size)})"
            except OSError:
                annotation = "(stat failed)"
            files.append(f"{child.name} {annotation}")
    return dirs + files


def _format_size(size: int) -> str:
    """Format a byte count as a short human-readable string."""
    if size < 1024:
        return f"{size}B"
    value = float(size)
    for unit in ("kB", "MB", "GB"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f}{unit}"
    return f"{value / 1024:.1f}TB"


def _is_mutating(ctx: Any, tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    return tool_def.name in _RISKY_TOOLS


@dataclass
class FilesystemCapability(AbstractCapability[Any]):
    """Read/write filesystem tools. ``write_file`` and ``edit_file`` are HITL-gated.

    ``allowed`` optionally restricts the exposed tools to a subset by name.
    The A2A guest passes ``{"read_file", "list_dir"}`` so write/edit are
    *structurally* absent from its toolset — the model never sees them —
    rather than merely approval-blocked (R3). ``None`` (default) exposes all
    four tools with the usual HITL gating on writes.
    """

    allowed: frozenset[str] | None = None

    def get_toolset(self) -> Any:
        all_tools = {
            "read_file": read_file,
            "write_file": write_file,
            "edit_file": edit_file,
            "list_dir": list_dir,
        }
        if self.allowed is None:
            funcs = list(all_tools.values())
        else:
            # Build the FunctionToolset from only the allowed functions, so
            # excluded tools are genuinely absent (not just hidden by a
            # wrapper). The guest relies on write_file/edit_file being
            # unregistered, not merely approval-blocked (R3).
            funcs = [fn for name, fn in all_tools.items() if name in self.allowed]
        return jac_function_toolset(*funcs).approval_required(_is_mutating)
