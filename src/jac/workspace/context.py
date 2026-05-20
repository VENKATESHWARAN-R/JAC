"""Session context loading — clock + AGENTS.md + memory.md (user + project).

At session start we inject, in this order:

1. Current local date, weekday, and time (always).
2. ``~/.jac/AGENTS.md`` — user-level context (user-authored), if present.
3. ``~/.jac/memory.md`` — user-level JAC-managed memory, if present.
4. ``<repo>/AGENTS.md`` — project context (user-authored, community
   convention), if present.
5. ``<repo>/.agents/memory.md`` — project-level JAC-managed memory, if
   present.

User-scope content comes first, project-scope second — so project
specifics dominate when guidance overlaps. Within each scope,
user-authored ``AGENTS.md`` comes before JAC-managed ``memory.md`` so the
freshest learned facts are the latest thing the model sees.
"""

from __future__ import annotations

from datetime import datetime

from . import paths

_DATETIME_HEADER = "<!-- session start: current local date/time -->"
_USER_CTX_HEADER = "<!-- user AGENTS.md (from ~/.jac/AGENTS.md) -->"
_USER_MEM_HEADER = "<!-- user memory (from ~/.jac/memory.md) -->"
_PROJECT_CTX_HEADER_FMT = "<!-- project AGENTS.md (from {}) -->"
_PROJECT_MEM_HEADER_FMT = "<!-- project memory (from {}) -->"


def format_session_datetime() -> str:
    """Human-readable local date, weekday, and time for Gru's instructions."""
    now = datetime.now().astimezone()
    tz_label = now.tzname() or "local"
    hour12 = now.hour % 12 or 12
    clock = f"{hour12}:{now.minute:02d}:{now.second:02d} {now.strftime('%p')}"
    date = f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}"
    return f"{_DATETIME_HEADER}\n{date} at {clock} ({tz_label})"


def _read_or_none(path, header: str) -> str | None:
    """Return ``header\\nbody`` if the file exists and is non-empty, else ``None``."""
    if not path.is_file():
        return None
    body = path.read_text(encoding="utf-8").strip()
    if not body:
        return None
    return f"{header}\n{body}"


def load_user_context() -> str | None:
    """``~/.jac/AGENTS.md`` body wrapped in its provenance header, or ``None``."""
    return _read_or_none(paths.USER_CONTEXT_FILE, _USER_CTX_HEADER)


def load_user_memory() -> str | None:
    """``~/.jac/memory.md`` body wrapped in its provenance header, or ``None``."""
    return _read_or_none(paths.USER_MEMORY_FILE, _USER_MEM_HEADER)


def load_project_context() -> str | None:
    """``<repo>/AGENTS.md`` body wrapped in its provenance header, or ``None``."""
    project_ctx = paths.project_context_file()
    return _read_or_none(project_ctx, _PROJECT_CTX_HEADER_FMT.format(project_ctx))


def load_project_memory() -> str | None:
    """``<repo>/.agents/memory.md`` body wrapped in its provenance header, or ``None``."""
    mem = paths.project_memory_file()
    return _read_or_none(mem, _PROJECT_MEM_HEADER_FMT.format(mem))


def load_session_context() -> str:
    """All context injected at session start.

    Order: clock → user AGENTS.md → user memory.md → project AGENTS.md →
    project memory.md. Each piece is optional and silently skipped when
    absent or empty.
    """
    parts: list[str] = [format_session_datetime()]
    for loader in (
        load_user_context,
        load_user_memory,
        load_project_context,
        load_project_memory,
    ):
        chunk = loader()
        if chunk:
            parts.append(chunk)
    return "\n\n".join(parts)
