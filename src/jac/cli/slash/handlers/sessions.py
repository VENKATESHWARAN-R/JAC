"""``/sessions`` — list sessions in this project, oldest → newest."""

from __future__ import annotations

from jac.cli.session_view import render_session_listing
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult


@register(
    "sessions",
    summary="List sessions in this project, oldest → newest",
    usage="/sessions",
)
def sessions_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args:
        ctx.console.print("[dim]/sessions takes no arguments[/dim]")
    render_session_listing(ctx.console, in_repl=True)
    return Handled()
