"""Shared A2A server-started banner.

Two surfaces start the A2A server — ``/a2a serve`` inside the REPL
(:func:`jac.cli.repl._handle_start_a2a`) and the headless ``jac a2a serve``
(:func:`jac.cli.a2a.serve_command`). Both print the same multi-line banner
when the server comes up: URL, bind host, bearer token (or unsafe warning),
agent-card URL. Centralizing that here keeps the two surfaces from
drifting apart.

The banner takes the ``ServerInfo``-shaped object returned by
:meth:`A2ACapability.start_server` and a ``rich.Console`` to print to.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console


def print_server_started_banner(
    info: Any,
    console: Console,
    *,
    profile_name: str | None = None,
    token_hint: str = "save it; /a2a token re-prints",
) -> None:
    """Print the "server started" banner used by both REPL and headless paths.

    Args:
        info: return value of :meth:`A2ACapability.start_server`
            (carries ``url``, ``bind_host``, ``port``, ``unsafe``, ``token``).
        console: target console.
        profile_name: when set, appends ``profile=<name>`` to the bind line.
            Used by the headless ``jac a2a serve`` path.
        token_hint: short reminder of how to recover the token later. The
            REPL uses the default; the headless path overrides because
            ``/a2a token`` isn't available outside a REPL.
    """
    bind_tail = f"profile {profile_name!r}" if profile_name else ""
    bind_block = f"bind {info.bind_host}:{info.port}"
    if bind_tail:
        bind_block = f"{bind_block}, {bind_tail}"
    console.print(
        f"[green]✓ A2A server started:[/green] [bold]{info.url}[/bold]  [dim]({bind_block})[/dim]"
    )
    if info.unsafe:
        console.print(
            "[red]auth: disabled (--unsafe)[/red] "
            "[dim]— card omits securitySchemes; any caller accepted[/dim]"
        )
    else:
        console.print(f"[dim]auth: bearer token ({token_hint}):[/dim]")
        console.print(f"  [bold]{info.token}[/bold]")
    console.print(f"[dim]agent card: {info.url}/.well-known/agent-card.json[/dim]")
