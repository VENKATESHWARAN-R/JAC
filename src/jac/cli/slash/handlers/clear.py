"""``/clear`` — start a fresh session in place (current session is preserved on disk)."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import SlashResult, SwitchSession
from jac.runtime.session import Session


@register(
    "clear",
    summary="Start a fresh session in place (current session is preserved on disk)",
    usage="/clear",
)
def clear_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args:
        ctx.console.print("[dim]/clear takes no arguments[/dim]")
    return SwitchSession(session=Session.new())
