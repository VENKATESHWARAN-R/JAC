"""Session context loading — date + AGENTS.md + memory.md (user + project).

Injected into Gru's system instructions, in this order:

1. Current local date + weekday (changes at most once per day — see note
   on prompt caching below).
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

**Prompt-cache discipline (Phase A.2).** Everything here is re-evaluated
per model request via :class:`jac.capabilities.context.ContextCapability`'s
callable instructions. Anything that changes turn-to-turn invalidates
Anthropic's prompt cache (10% input price → full price). So:

- The date line uses **day granularity** — no hours/minutes/seconds. One
  cache miss per midnight rollover is acceptable; one per turn is not.
- ``AGENTS.md`` and ``memory.md`` are re-read each turn so fresh
  ``remember()`` writes land immediately; cache invalidates only when the
  files actually change, not on a clock tick.
- File paths in provenance comments are stable per-project, so they cache
  fine.

If you add a new piece of context here, keep it stable across turns or
move it to the per-turn user prompt slot.
"""

from __future__ import annotations

from datetime import datetime

from . import paths

_DATE_HEADER = "<!-- current local date -->"
_USER_CTX_HEADER = "<!-- user AGENTS.md (from ~/.jac/AGENTS.md) -->"
_USER_MEM_HEADER = "<!-- user memory (from ~/.jac/memory.md) -->"
_PROJECT_CTX_HEADER_FMT = "<!-- project AGENTS.md (from {}) -->"
_PROJECT_MEM_HEADER_FMT = "<!-- project memory (from {}) -->"


def format_session_datetime() -> str:
    """Human-readable local date + weekday for Gru's instructions.

    **Day granularity only** — see the module docstring on prompt-cache
    discipline. Hours / minutes / seconds were dropped in Phase A.2
    because second-precision in the system prompt busts the prompt cache
    every turn for negligible model benefit.
    """
    now = datetime.now().astimezone()
    date = f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}"
    return f"{_DATE_HEADER}\n{date}"


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


def load_agents_context() -> str | None:
    """User + project ``AGENTS.md`` only — for sub-agent context injection.

    Deliberately narrower than :func:`load_session_context`. A spawned
    sub-agent (minion) gets the repo's conventions and safety rules so it
    acts correctly when it touches the project — but **not** the JAC-managed
    ``memory.md`` files, the date line, or any conversation history:

    - ``memory.md`` (user + project) is excluded because it grows unbounded
      and is often irrelevant to a bounded delegated task; re-paying it on
      every (frequently cheap, frequently parallel) spawn cuts against the
      whole reason sub-agents are context-isolated. If a specific memory
      matters, Gru puts it in the task packet, where it's scoped on purpose.
    - The date line is dropped — minions are short-lived and the packet
      carries any time-sensitivity. Omitting it also keeps the rendered
      packet maximally cache-stable across sibling spawns.
    - Conversation history never crosses into a sub-agent — that isolation
      is the point. ``ask_main_agent`` covers "I need something only Gru
      knows".

    Returns ``user AGENTS.md`` then ``project AGENTS.md`` (each wrapped in
    its provenance header), joined by blank lines, or ``None`` when neither
    file is present.
    """
    parts: list[str] = []
    for loader in (load_user_context, load_project_context):
        chunk = loader()
        if chunk:
            parts.append(chunk)
    return "\n\n".join(parts) if parts else None


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
