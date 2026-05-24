"""``/a2a stop`` — shut down the A2A guest server."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.result import Handled, SlashResult, StopA2AServer


def handle(ctx: SlashContext) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    if cap.server is None or not cap.server.is_running:
        ctx.console.print("[dim]A2A server is not running[/dim]")
        return Handled()
    return StopA2AServer()
