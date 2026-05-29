"""``/context`` — view or set the context-window budget for this session.

The budget is what compaction's percentage ladder is measured against (NOT
the model's published window). Resolution order is session override →
per-model config (``compaction.model_context_tokens``) → the
``compaction.max_context_tokens`` default.

- ``/context`` — show the resolved budget and where it comes from.
- ``/context 400k`` / ``/context 400000`` — set a session override (clamped
  to the 512k ceiling, reported if clamped). Survives until the session ends
  or ``/context reset``.
- ``/context reset`` (or ``off``) — drop the session override.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.config import (
    MAX_CONTEXT_CEILING,
    get_session_context_override,
    get_settings,
    resolve_context_budget,
    set_session_context_budget,
)


def _parse_tokens(raw: str) -> int | None:
    """Parse ``256000`` / ``256k`` / ``0.5m`` into an int token count.

    Returns ``None`` if the string isn't a recognizable token count."""
    s = raw.strip().lower().replace(",", "").replace("_", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    try:
        value = float(s) * mult
    except ValueError:
        return None
    return int(value) if value > 0 else None


def _source_note(ctx: SlashContext) -> str:
    """Describe where the currently-resolved budget comes from."""
    if get_session_context_override() is not None:
        return "session override (/context)"
    per_model = get_settings().compaction.model_context_tokens.get(ctx.model_id)
    if per_model:
        return f"per-model config for {ctx.model_id}"
    return "compaction.max_context_tokens default"


@register(
    "context",
    summary="View or set this session's context-window budget",
    usage="/context [N | reset]",
)
def context_handler(ctx: SlashContext, args: str) -> SlashResult:
    arg = args.strip()

    if not arg:
        budget = resolve_context_budget(ctx.model_id)
        ctx.console.print(
            f"[bold]context budget:[/bold] {budget:,} tokens "
            f"[dim]({_source_note(ctx)})[/dim]\n"
            f"[dim]set with [bold]/context <N>[/bold] (e.g. 400k), "
            f"ceiling {MAX_CONTEXT_CEILING:,}[/dim]"
        )
        return Handled()

    if arg.lower() in {"reset", "off", "clear", "default"}:
        set_session_context_budget(None)
        ctx.console.print(
            f"[green]✓[/green] session context override cleared — "
            f"now {resolve_context_budget(ctx.model_id):,} "
            f"[dim]({_source_note(ctx)})[/dim]"
        )
        return Handled()

    tokens = _parse_tokens(arg)
    if tokens is None:
        ctx.console.print(
            f"[red]could not parse[/red] '{arg}' — try [bold]/context 400k[/bold], "
            "[bold]/context 256000[/bold], or [bold]/context reset[/bold]"
        )
        return Handled()

    stored = set_session_context_budget(tokens)
    if stored != tokens:
        ctx.console.print(
            f"[yellow]clamped to the {MAX_CONTEXT_CEILING:,} ceiling[/yellow] "
            f"(asked for {tokens:,})"
        )
    ctx.console.print(f"[green]✓[/green] context budget for this session: {stored:,} tokens")
    return Handled()
