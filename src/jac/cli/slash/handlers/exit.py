"""``/exit`` — leave the REPL."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Exit, SlashResult


@register("exit", summary="Leave the REPL", usage="/exit")
def exit_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args:
        ctx.console.print("[dim]/exit takes no arguments[/dim]")
    return Exit()
