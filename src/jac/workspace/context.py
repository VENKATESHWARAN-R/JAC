"""Session context loading — clock + AGENTS.md.

At session start we inject:

- Current local date, weekday, and time (always)
- ``<project_root>/AGENTS.md`` — project-wide context (community-shared), if present
- ``~/.jac/AGENTS.md`` — user-level context (JAC-private), if present

JAC follows the AGENTS.md community convention. User and project prose are
optional. We concatenate user context **first**, then project context, so
project-specific guidance comes last and tends to dominate.
"""

from __future__ import annotations

from datetime import datetime

from . import paths

_DATETIME_HEADER = "<!-- session start: current local date/time -->"
_USER_HEADER = "<!-- user-level AGENTS.md (from ~/.jac/AGENTS.md) -->"
_PROJECT_HEADER_FMT = "<!-- project AGENTS.md (from {}) -->"


def format_session_datetime() -> str:
    """Human-readable local date, weekday, and time for Gru's instructions."""
    now = datetime.now().astimezone()
    tz_label = now.tzname() or "local"
    hour12 = now.hour % 12 or 12
    clock = f"{hour12}:{now.minute:02d}:{now.second:02d} {now.strftime('%p')}"
    date = f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}"
    return f"{_DATETIME_HEADER}\n{date} at {clock} ({tz_label})"


def load_agent_context() -> str | None:
    """Read AGENTS.md from user + project locations. Returns combined text or ``None``."""
    chunks: list[str] = []

    if paths.USER_CONTEXT_FILE.is_file():
        body = paths.USER_CONTEXT_FILE.read_text(encoding="utf-8").strip()
        if body:
            chunks.append(f"{_USER_HEADER}\n{body}")

    project_ctx = paths.project_context_file()
    if project_ctx.is_file():
        body = project_ctx.read_text(encoding="utf-8").strip()
        if body:
            chunks.append(f"{_PROJECT_HEADER_FMT.format(project_ctx)}\n{body}")

    if not chunks:
        return None
    return "\n\n".join(chunks)


def load_session_context() -> str:
    """All context injected at session start: clock plus optional AGENTS.md."""
    parts = [format_session_datetime()]
    agents = load_agent_context()
    if agents:
        parts.append(agents)
    return "\n\n".join(parts)
