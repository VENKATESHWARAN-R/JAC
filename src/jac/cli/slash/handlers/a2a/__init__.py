"""``/a2a`` — manage the A2A guest server (D24, Phase 4.a).

Subcommands (one Python module each in this subpackage):

- ``/a2a serve [--port N] [--host ADDR] [--unsafe]`` — ``serve.py``
- ``/a2a stop`` — ``stop.py``
- ``/a2a status`` — ``status.py``
- ``/a2a token`` — ``token.py``
- ``/a2a peers`` — ``peers.py``
- ``/a2a peer add|remove`` — ``peer.py``

Shared helpers live in ``_args.py`` (parsers) and ``_shared.py`` (rendering
+ secret prompt). This ``__init__`` is the thin dispatcher — it registers
the single top-level ``/a2a`` slash and routes by subcommand name.

The headless ``jac a2a serve`` typer command (``jac.cli.a2a``) shares the
same start logic by going through :meth:`A2ACapability.start_server`
directly — no duplicated lifecycle code.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a import peer, peers, serve, status, stop, token
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult


@register(
    "a2a",
    summary="Manage the A2A guest server + peers (serve / stop / status / token / peer / peers)",
    usage=(
        "/a2a {serve [--port N] [--host ADDR] [--unsafe] | stop | status | token | "
        "peers | peer add NAME URL [--bearer | --api-key HEADER | --oauth2 ...] | "
        "peer remove NAME}"
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
            "[dim]usage:[/dim] /a2a serve | stop | status | token | peers | peer add|remove\n"
            "[dim]      /a2a serve [--port N] [--host ADDR] [--unsafe][/dim]\n"
            "[dim]      /a2a peer add NAME URL [--bearer | --api-key HEADER | "
            "--oauth2 TOKEN_URL CLIENT_ID [--scope X]][/dim]\n"
            "[dim]      /a2a peer remove NAME[/dim]"
        )
        return Handled()

    if sub == "serve":
        return serve.handle(ctx, rest)
    if sub == "stop":
        return stop.handle(ctx)
    if sub == "status":
        return status.handle(ctx)
    if sub == "token":
        return token.handle(ctx)
    if sub == "peers":
        return peers.handle(ctx)
    if sub == "peer":
        return peer.handle(ctx, rest)

    ctx.console.print(
        f"[red]unknown /a2a subcommand:[/red] {sub!r}  "
        "[dim](try /a2a serve | stop | status | token | peers | peer add|remove)[/dim]"
    )
    return Handled()
