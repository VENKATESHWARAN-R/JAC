"""``/remember`` and ``/forget`` — user-driven memory edits.

The same durable-memory store Gru curates via its ``remember`` / ``forget``
tools, but driven by *you* directly — no model tokens, no waiting for Gru to
decide. Typing the command **is** the approval (these bypass the HITL panel
that gates the agent-initiated tools, because the human is already in the
loop). Writes are still one audited bullet at a time, de-duplicated, in the
fixed five-section schema.

    /remember <scope> <category> <text...>
    /forget   <scope> <text...>

    scope     : user | project
    category  : convention | fact | preference | gotcha | decision

Examples:

    /remember project convention uses uv, not pip
    /remember user preference prefers terse output
    /forget project uses uv, not pip
"""

from __future__ import annotations

from typing import cast, get_args

from jac.capabilities.memory import MemoryCategory, MemoryScope
from jac.capabilities.memory import forget as _forget_memory
from jac.capabilities.memory import remember as _remember_memory
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.errors import JacConfigError

_SCOPES: tuple[str, ...] = get_args(MemoryScope)
_CATEGORIES: tuple[str, ...] = get_args(MemoryCategory)
_REASON = "user-initiated via slash command"


@register(
    "remember",
    summary="Store a memory entry yourself (no model call)",
    usage="/remember <user|project> <category> <text>",
)
def remember_handler(ctx: SlashContext, args: str) -> SlashResult:
    parts = args.split(maxsplit=2)
    if len(parts) < 3:
        _usage(ctx, "remember")
        return Handled()
    scope, category, content = parts[0].lower(), parts[1].lower(), parts[2]
    if scope not in _SCOPES:
        ctx.console.print(f"[red]bad scope[/red] {parts[0]!r} — use {' or '.join(_SCOPES)}.")
        return Handled()
    if category not in _CATEGORIES:
        ctx.console.print(
            f"[red]bad category[/red] {parts[1]!r} — one of {', '.join(_CATEGORIES)}."
        )
        return Handled()
    try:
        result = _remember_memory(
            reason=_REASON,
            content=content,
            category=cast(MemoryCategory, category),
            scope=cast(MemoryScope, scope),
        )
    except (ValueError, JacConfigError) as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()
    ctx.console.print(f"[green]✓[/green] {result}")
    return Handled()


@register(
    "forget",
    summary="Remove a memory entry yourself (no model call)",
    usage="/forget <user|project> <exact text>",
)
def forget_handler(ctx: SlashContext, args: str) -> SlashResult:
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        _usage(ctx, "forget")
        return Handled()
    scope, content = parts[0].lower(), parts[1]
    if scope not in _SCOPES:
        ctx.console.print(f"[red]bad scope[/red] {parts[0]!r} — use {' or '.join(_SCOPES)}.")
        return Handled()
    try:
        result = _forget_memory(reason=_REASON, content=content, scope=cast(MemoryScope, scope))
    except (ValueError, JacConfigError) as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()
    ctx.console.print(f"[green]✓[/green] {result}")
    return Handled()


def _usage(ctx: SlashContext, which: str) -> None:
    if which == "remember":
        ctx.console.print(
            "[dim]usage: [bold]/remember <user|project> <category> <text>[/bold]\n"
            f"categories: {', '.join(_CATEGORIES)}\n"
            "e.g. /remember project convention uses uv, not pip[/dim]"
        )
    else:
        ctx.console.print(
            "[dim]usage: [bold]/forget <user|project> <exact text>[/bold]\n"
            "see [bold]/memory[/bold] for the exact text of stored entries.[/dim]"
        )
