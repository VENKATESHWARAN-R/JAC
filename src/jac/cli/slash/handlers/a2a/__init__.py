"""``/a2a`` — manage outbound A2A peers (D24, Phase 4).

Subcommands (one Python module each in this subpackage):

- ``/a2a peers`` — ``peers.py``
- ``/a2a peer add|remove`` — ``peer.py``

Shared helpers live in ``_args.py`` (parser) and ``_shared.py`` (rendering
+ secret prompt). This ``__init__`` is the thin dispatcher — it registers
the single top-level ``/a2a`` slash and routes by subcommand name.

**The inbound guest server is no longer started from the REPL.** Its
lifecycle (``serve`` / ``stop`` / ``status`` / ``token``) lives only in the
headless ``jac a2a serve`` typer command (``jac.cli.a2a``), mirroring how the
web surface is started via ``jac web serve``. What remains here is purely
*outbound* peer configuration for Gru's ``a2a_call`` / ``a2a_discover`` tools.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a import peer, peers
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult


@register(
    "a2a",
    summary="Manage outbound A2A peers (peers / peer add|remove)",
    usage=(
        "/a2a {peers | peer add NAME URL [--bearer | --api-key HEADER | "
        "--oauth2 ...] | peer remove NAME}"
    ),
)
def a2a_handler(ctx: SlashContext, args: str) -> SlashResult:
    if ctx.a2a is None:
        ctx.console.print(
            "[yellow]A2A subsystem is not wired into this session[/yellow] "
            "[dim](this shouldn't happen in the REPL; report as a bug)[/dim]"
        )
        return Handled()

    sub, _, rest = args.partition(" ")
    sub = sub.strip().lower()
    rest = rest.strip()

    if not sub:
        ctx.console.print(
            "[dim]usage:[/dim] /a2a peers | peer add|remove\n"
            "[dim]      /a2a peer add NAME URL [--bearer | --api-key HEADER | "
            "--oauth2 TOKEN_URL CLIENT_ID [--scope X]][/dim]\n"
            "[dim]      /a2a peer remove NAME[/dim]\n"
            "[dim]start the inbound server with [bold]jac a2a serve[/bold] (not from the REPL).[/dim]"
        )
        return Handled()

    if sub == "peers":
        return peers.handle(ctx)
    if sub == "peer":
        return peer.handle(ctx, rest)

    if sub in {"serve", "stop", "status", "token"}:
        ctx.console.print(
            f"[yellow]/a2a {sub} was removed from the REPL.[/yellow] "
            "Start the inbound A2A server with [bold]jac a2a serve[/bold] "
            "[dim](like [bold]jac web serve[/bold]).[/dim]"
        )
        return Handled()

    ctx.console.print(
        f"[red]unknown /a2a subcommand:[/red] {sub!r}  "
        "[dim](try /a2a peers | peer add|remove)[/dim]"
    )
    return Handled()
