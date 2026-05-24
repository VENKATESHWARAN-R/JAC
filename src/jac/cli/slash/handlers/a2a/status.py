"""``/a2a status`` — show A2A guest server running state."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.result import Handled, SlashResult


def handle(ctx: SlashContext) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    if cap.server is None or not cap.server.is_running or cap.server.info is None:
        ctx.console.print("[dim]A2A server: (not running)[/dim]")
        ctx.console.print("[dim]start with: /a2a serve[/dim]")
        return Handled()

    from jac.capabilities.a2a.auth import redact_token

    info = cap.server.info
    auth_line = (
        "[red]disabled (--unsafe)[/red]"
        if info.unsafe
        else f"bearer [dim]({redact_token(info.token)})[/dim]"
    )
    ctx.console.print("[bold]A2A server:[/bold] running")
    ctx.console.print(f"  url:   [bold]{info.url}[/bold]")
    ctx.console.print(f"  bind:  {info.bind_host}:{info.port}")
    ctx.console.print(f"  auth:  {auth_line}")
    ctx.console.print(f"  card:  [dim]{info.url}/.well-known/agent-card.json[/dim]")
    return Handled()
