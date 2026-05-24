"""``/a2a`` — manage the A2A guest server (D24, Phase 4.a).

Subcommands:

- ``/a2a serve [--port N] [--host ADDR] [--unsafe]`` — start the server.
  Defaults come from the active profile's ``a2a.host`` / ``a2a.port``;
  flags override.
- ``/a2a stop`` — shut down the server.
- ``/a2a status`` — show running state (URL, host, port, token preview,
  unsafe flag) or "(not running)".
- ``/a2a token`` — re-print the full bearer in case it scrolled past
  the startup banner. No-op (with a hint) when no server is running.
- ``/a2a peers`` — list peers configured under the active profile's
  ``a2a.peers`` block. Shows name, URL, auth (has-token / none), and
  description (truncated). Read-only.

Sync ``status`` + ``token`` return :class:`Handled` directly (no I/O).
Async ``serve`` + ``stop`` return :class:`StartA2AServer` /
:class:`StopA2AServer` so the REPL drives the async capability method
in its own event loop — mirrors how ``/model`` returns
:class:`RebuildGru`. Spinning a thread + nested loop here would break
the server's lifecycle because uvicorn's task would die with the
helper thread's loop.

The headless ``jac a2a serve`` typer command (``jac.cli.a2a``) shares
the same start logic by going through :meth:`A2ACapability.start_server`
directly — no duplicated lifecycle code.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import (
    Handled,
    SlashResult,
    StartA2AServer,
    StopA2AServer,
)


@register(
    "a2a",
    summary="Manage the A2A guest server + peers (serve / stop / status / token / peers)",
    usage="/a2a {serve [--port N] [--host ADDR] [--unsafe] | stop | status | token | peers}",
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
            "[dim]usage:[/dim] /a2a serve | stop | status | token | peers\n"
            "[dim]      /a2a serve [--port N] [--host ADDR] [--unsafe][/dim]"
        )
        return Handled()

    if sub == "serve":
        return _serve(ctx, rest)
    if sub == "stop":
        return _stop(ctx)
    if sub == "status":
        return _status(ctx)
    if sub == "token":
        return _token(ctx)
    if sub == "peers":
        return _peers(ctx)

    ctx.console.print(
        f"[red]unknown /a2a subcommand:[/red] {sub!r}  "
        "[dim](try /a2a serve | stop | status | token | peers)[/dim]"
    )
    return Handled()


# ---------- subcommand implementations ----------


def _serve(ctx: SlashContext, rest: str) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    if cap.server is not None and cap.server.is_running:
        ctx.console.print(
            "[yellow]A2A server is already running.[/yellow] "
            "[dim](use /a2a stop first, or /a2a status to inspect)[/dim]"
        )
        return Handled()

    profile_host, profile_port = _profile_defaults(ctx)
    try:
        host, port, unsafe = _parse_serve_args(
            rest, default_host=profile_host, default_port=profile_port
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


def _stop(ctx: SlashContext) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    if cap.server is None or not cap.server.is_running:
        ctx.console.print("[dim]A2A server is not running[/dim]")
        return Handled()
    return StopA2AServer()


def _status(ctx: SlashContext) -> SlashResult:
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


def _token(ctx: SlashContext) -> SlashResult:
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


_DESCRIPTION_TRUNCATE_AT = 60


def _peers(ctx: SlashContext) -> SlashResult:
    """Render the active profile's ``a2a.peers`` block.

    Pulls from the capability's live ``peers`` dict (which the REPL
    keeps in sync with the active profile via ``/profile`` rebuilds)
    rather than re-reading the profile object — that way what we show
    is what ``a2a_call`` will actually resolve against.
    """
    cap = ctx.a2a
    assert cap is not None
    peers = cap.peers
    if not peers:
        ctx.console.print("[dim]A2A peers: (none configured)[/dim]")
        ctx.console.print(
            "[dim]add some under [bold]a2a.peers.<name>[/bold] in your profile YAML "
            "([bold]jac profiles edit NAME[/bold]).[/dim]"
        )
        return Handled()

    ctx.console.print(f"[bold]A2A peers[/bold] [dim](profile: {ctx.profile_name})[/dim]")
    name_col = max(len(n) for n in peers)
    url_col = max(len(p.url) for p in peers.values())
    for name in sorted(peers):
        peer = peers[name]
        auth = "[green]bearer[/green]" if peer.token else "[yellow]none[/yellow]"
        desc = peer.description or ""
        if len(desc) > _DESCRIPTION_TRUNCATE_AT:
            desc = desc[: _DESCRIPTION_TRUNCATE_AT - 1] + "…"
        desc_tail = f"  [dim]— {desc}[/dim]" if desc else ""
        ctx.console.print(
            f"  [bold]{name:<{name_col}}[/bold]  {peer.url:<{url_col}}  auth: {auth}{desc_tail}"
        )
    return Handled()


# ---------- helpers ----------


def _profile_defaults(ctx: SlashContext) -> tuple[str, int]:
    """Pull bind defaults from the active profile, fall back to schema defaults."""
    if ctx.profile is not None:
        return ctx.profile.a2a.host, ctx.profile.a2a.port
    # No profile (--model REPL session) — fall back to A2AProfileConfig defaults.
    from jac.profiles import A2AProfileConfig

    default = A2AProfileConfig()
    return default.host, default.port


def _parse_serve_args(rest: str, *, default_host: str, default_port: int) -> tuple[str, int, bool]:
    """Mini parser for ``[--port N] [--host ADDR] [--unsafe]``.

    Order doesn't matter; unknown args raise ``ValueError`` with a
    helpful message. The headless typer command parses its own flags
    upstream and never needs this.
    """
    if not rest:
        return default_host, default_port, False

    host = default_host
    port = default_port
    unsafe = False

    tokens = rest.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--unsafe":
            unsafe = True
            i += 1
            continue
        if tok in {"--host", "--port"}:
            if i + 1 >= len(tokens):
                raise ValueError(f"{tok} requires a value")
            value = tokens[i + 1]
            if tok == "--host":
                host = value
            else:
                try:
                    port = int(value)
                except ValueError as exc:
                    raise ValueError(f"--port must be an integer; got {value!r}") from exc
                if not (1 <= port <= 65535):
                    raise ValueError(f"--port must be 1-65535; got {port}")
            i += 2
            continue
        raise ValueError(f"unknown arg {tok!r}; expected --host ADDR | --port N | --unsafe")

    return host, port, unsafe
