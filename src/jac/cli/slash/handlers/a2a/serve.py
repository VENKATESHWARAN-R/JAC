"""``/a2a serve`` — start the A2A guest server."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a._args import parse_serve_args
from jac.cli.slash.handlers.a2a._shared import profile_defaults
from jac.cli.slash.result import Handled, SlashResult, StartA2AServer


def handle(ctx: SlashContext, rest: str) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    if cap.server is not None and cap.server.is_running:
        ctx.console.print(
            "[yellow]A2A server is already running.[/yellow] "
            "[dim](use /a2a stop first, or /a2a status to inspect)[/dim]"
        )
        return Handled()

    default_host, default_port = profile_defaults(ctx)
    try:
        host, port, unsafe = parse_serve_args(
            rest, default_host=default_host, default_port=default_port
        )
    except ValueError as exc:
        ctx.console.print(f"[red]invalid /a2a serve args:[/red] {exc}")
        return Handled()

    if unsafe:
        ctx.console.print(
            "[red bold]⚠ --unsafe:[/red bold] starting A2A server with no authentication. "
            "[dim]Any client that can reach the port can drive the guest Gru.[/dim]"
        )

    return StartA2AServer(host=host, port=port, unsafe=unsafe)
