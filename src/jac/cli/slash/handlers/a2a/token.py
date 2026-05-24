"""``/a2a token`` — re-print the current bearer token."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.result import Handled, SlashResult


def handle(ctx: SlashContext) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    if cap.server is None or not cap.server.is_running or cap.server.info is None:
        ctx.console.print("[dim]A2A server is not running — no token to re-print.[/dim]")
        return Handled()
    info = cap.server.info
    if info.unsafe:
        ctx.console.print("[dim]server is running with --unsafe; there is no token.[/dim]")
        return Handled()
    ctx.console.print(f"[bold]{info.token}[/bold]")
    return Handled()
