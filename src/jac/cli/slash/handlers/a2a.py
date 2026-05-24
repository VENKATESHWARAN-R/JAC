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
            "[dim]      /a2a peer add NAME URL [--bearer | --api-key HEADER | --oauth2 TOKEN_URL CLIENT_ID [--scope X]][/dim]\n"
            "[dim]      /a2a peer remove NAME[/dim]"
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
    if sub == "peer":
        return _peer(ctx, rest)

    ctx.console.print(
        f"[red]unknown /a2a subcommand:[/red] {sub!r}  "
        "[dim](try /a2a serve | stop | status | token | peers | peer add|remove)[/dim]"
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
    """Render the merged A2A peers view (profile + session) with provenance tags.

    Shows session-scoped peers (from ``/a2a peer add``) with a
    ``[session]`` tag and profile-defined peers with ``[profile]``.
    When a session peer shadows a profile peer of the same name, the
    profile one is rendered greyed-out so the operator sees the
    override is intentional.
    """
    cap = ctx.a2a
    assert cap is not None
    profile_peers = cap.profile_peers
    session_peers = cap.session_peers

    if not profile_peers and not session_peers:
        ctx.console.print("[dim]A2A peers: (none configured)[/dim]")
        ctx.console.print(
            "[dim]for stable peers: add under [bold]a2a.peers.<name>[/bold] in "
            "your profile YAML ([bold]jac profiles edit NAME[/bold]).[/dim]"
        )
        ctx.console.print(
            "[dim]for ephemeral peers: [bold]/a2a peer add NAME URL ...[/bold] "
            "(this session only).[/dim]"
        )
        return Handled()

    ctx.console.print(f"[bold]A2A peers[/bold] [dim](profile: {ctx.profile_name})[/dim]")

    all_names = sorted(set(profile_peers) | set(session_peers))
    name_col = max(len(n) for n in all_names)
    url_col = max(
        max((len(p.url) for p in profile_peers.values()), default=0),
        max((len(p.url) for p in session_peers.values()), default=0),
    )

    for name in all_names:
        in_session = name in session_peers
        in_profile = name in profile_peers
        # Session peer is the *effective* one when both exist.
        peer = session_peers[name] if in_session else profile_peers[name]
        # Escape the brackets so Rich doesn't interpret e.g. ``[session]`` as
        # a markup tag and silently strip it.
        provenance = r"[cyan]\[session][/cyan]" if in_session else r"[blue]\[profile][/blue]"
        ctx.console.print(
            f"  [bold]{name:<{name_col}}[/bold]  {peer.url:<{url_col}}  "
            f"auth: {_auth_label(peer)}  {provenance}{_desc_tail(peer)}"
        )
        # When a session peer shadows a profile peer, show the shadowed
        # one greyed-out underneath so the operator knows it's still in
        # their profile config (just not active right now).
        if in_session and in_profile:
            shadowed = profile_peers[name]
            ctx.console.print(
                f"  [dim]{'':<{name_col}}  {shadowed.url:<{url_col}}  "
                f"auth: {_auth_label_dim(shadowed)}  [blue](shadowed profile)[/blue]"
                f"{_desc_tail(shadowed)}[/dim]"
            )
    return Handled()


def _auth_label(peer) -> str:
    """One-word colored auth tag for the peers listing."""
    from jac.profiles import ApiKeyAuth, BearerAuth, OAuth2ClientCredentialsAuth

    if peer.auth is None:
        return "[yellow]none[/yellow]"
    if isinstance(peer.auth, BearerAuth):
        return "[green]bearer[/green]"
    if isinstance(peer.auth, ApiKeyAuth):
        return "[green]api_key[/green]"
    if isinstance(peer.auth, OAuth2ClientCredentialsAuth):
        return "[green]oauth2[/green]"
    return "[red]unknown[/red]"  # pragma: no cover - new auth type without label


def _auth_label_dim(peer) -> str:
    """Like :func:`_auth_label` but uncolored (for shadowed-row rendering)."""
    from jac.profiles import ApiKeyAuth, BearerAuth, OAuth2ClientCredentialsAuth

    if peer.auth is None:
        return "none"
    if isinstance(peer.auth, BearerAuth):
        return "bearer"
    if isinstance(peer.auth, ApiKeyAuth):
        return "api_key"
    if isinstance(peer.auth, OAuth2ClientCredentialsAuth):
        return "oauth2"
    return "unknown"  # pragma: no cover


def _desc_tail(peer) -> str:
    desc = peer.description or ""
    if not desc:
        return ""
    if len(desc) > _DESCRIPTION_TRUNCATE_AT:
        desc = desc[: _DESCRIPTION_TRUNCATE_AT - 1] + "…"
    return f"  [dim]— {desc}[/dim]"


def _peer(ctx: SlashContext, rest: str) -> SlashResult:
    """Dispatch ``/a2a peer add ...`` / ``/a2a peer remove ...``."""
    cap = ctx.a2a
    assert cap is not None
    sub, _, args = rest.partition(" ")
    sub = sub.strip().lower()
    args = args.strip()

    if sub == "add":
        return _peer_add(ctx, args)
    if sub == "remove":
        return _peer_remove(ctx, args)

    ctx.console.print(
        f"[red]unknown /a2a peer subcommand:[/red] {sub!r}  "
        "[dim](try /a2a peer add NAME URL ... | /a2a peer remove NAME)[/dim]"
    )
    return Handled()


def _peer_add(ctx: SlashContext, args: str) -> SlashResult:
    """``/a2a peer add NAME URL [--bearer | --api-key HEADER | --oauth2 ...]``.

    Secrets (bearer token / api-key value / OAuth2 client_secret) are
    NEVER passed on the command line — they're prompted for via
    :func:`getpass.getpass` so they don't appear in shell or
    prompt-toolkit history. ``--bearer`` / ``--api-key HEADER`` /
    ``--oauth2 TOKEN_URL CLIENT_ID [--scope X]`` select the auth shape;
    the secret values are entered interactively.

    With no auth flag, the peer is added unauthenticated (talks to a
    peer running ``--unsafe`` only).
    """
    from jac.profiles import (
        A2APeerConfig,
        ApiKeyAuth,
        BearerAuth,
        OAuth2ClientCredentialsAuth,
        validate_profile_name,
    )

    cap = ctx.a2a
    assert cap is not None

    try:
        name, url, auth_spec = _parse_peer_add(args)
    except ValueError as exc:
        ctx.console.print(f"[red]invalid /a2a peer add args:[/red] {exc}")
        return Handled()

    try:
        validate_profile_name(name)
    except Exception as exc:
        ctx.console.print(f"[red]invalid peer name:[/red] {exc}")
        return Handled()

    # Build auth config from the parsed shape + interactive prompts.
    auth = None
    try:
        if auth_spec is None:
            ctx.console.print(
                "[yellow]no auth flag given;[/yellow] peer will be added "
                "without authentication (works only against --unsafe peers)."
            )
        elif auth_spec["kind"] == "bearer":
            token = _prompt_secret("bearer token", ctx)
            if token is None:
                return Handled()
            auth = BearerAuth(token=token)
        elif auth_spec["kind"] == "api_key":
            value = _prompt_secret(f"value for header {auth_spec['header']!r}", ctx)
            if value is None:
                return Handled()
            auth = ApiKeyAuth(header=auth_spec["header"], value=value)
        elif auth_spec["kind"] == "oauth2":
            client_secret = _prompt_secret(f"client_secret for {auth_spec['client_id']!r}", ctx)
            if client_secret is None:
                return Handled()
            auth = OAuth2ClientCredentialsAuth(
                token_url=auth_spec["token_url"],
                client_id=auth_spec["client_id"],
                client_secret=client_secret,
                scope=auth_spec.get("scope", ""),
            )
    except Exception as exc:
        ctx.console.print(f"[red]invalid auth config:[/red] {exc}")
        return Handled()

    peer = A2APeerConfig(
        url=url,
        auth=auth,
        description="(added via /a2a peer add — session-scoped)",
    )
    previous = cap.add_session_peer(name, peer)

    note = ""
    if previous is not None:
        note = " [yellow](replaced existing session entry)[/yellow]"
    elif name in cap.profile_peers:
        note = " [yellow](shadows profile entry)[/yellow]"

    auth_name = "none" if auth is None else auth.type
    ctx.console.print(
        f"[green]✓ session peer added:[/green] [bold]{name}[/bold] → {url}  "
        f"[dim](auth: {auth_name})[/dim]{note}"
    )
    return Handled()


def _peer_remove(ctx: SlashContext, args: str) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None
    name = args.strip()
    if not name:
        ctx.console.print("[red]usage:[/red] /a2a peer remove NAME")
        return Handled()
    removed = cap.remove_session_peer(name)
    if not removed:
        ctx.console.print(
            f"[dim]no session peer named {name!r}[/dim] "
            "(profile peers can only be edited in YAML — `jac profiles edit NAME`)"
        )
        return Handled()
    reverted = " [dim](reverted to profile entry)[/dim]" if name in cap.profile_peers else ""
    ctx.console.print(f"[green]✓ session peer removed:[/green] {name}{reverted}")
    return Handled()


def _prompt_secret(label: str, ctx: SlashContext) -> str | None:
    """Read a secret from stdin with no echo. Empty input → cancel.

    Uses ``getpass.getpass`` rather than :class:`rich.prompt.Prompt`
    so the value never echoes back and never lands in scrollback. The
    REPL is in async-land; ``getpass`` is blocking — slash dispatch
    is synchronous so this is fine.
    """
    import getpass

    try:
        value = getpass.getpass(f"  {label}: ")
    except (KeyboardInterrupt, EOFError):
        ctx.console.print("[dim]cancelled[/dim]")
        return None
    value = value.strip()
    if not value:
        ctx.console.print("[dim]cancelled (empty input)[/dim]")
        return None
    return value


def _parse_peer_add(args: str) -> tuple[str, str, dict | None]:
    """Parse ``NAME URL [--bearer | --api-key HEADER | --oauth2 ...]``.

    Returns ``(name, url, auth_spec_or_None)`` where ``auth_spec`` is
    a dict with a ``kind`` key plus the non-secret parameters parsed
    from the command line. Secret values (token, client_secret) are
    NOT in the spec — they come from the interactive prompt.

    Raises:
        ValueError: bad arg shape.
    """
    tokens = args.split()
    if len(tokens) < 2:
        raise ValueError("expected at least NAME URL")
    name, url = tokens[0], tokens[1]
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL must start with http:// or https://; got {url!r}")
    rest = tokens[2:]

    if not rest:
        return name, url, None

    flag = rest[0]
    if flag == "--bearer":
        if len(rest) != 1:
            raise ValueError("--bearer takes no positional args (token is prompted)")
        return name, url, {"kind": "bearer"}

    if flag == "--api-key":
        if len(rest) != 2:
            raise ValueError("--api-key takes one arg: the HEADER name (value is prompted)")
        return name, url, {"kind": "api_key", "header": rest[1]}

    if flag == "--oauth2":
        if len(rest) < 3:
            raise ValueError(
                "--oauth2 expects TOKEN_URL CLIENT_ID [--scope SCOPE] (client_secret is prompted)"
            )
        spec: dict = {
            "kind": "oauth2",
            "token_url": rest[1],
            "client_id": rest[2],
        }
        remaining = rest[3:]
        if remaining:
            if len(remaining) != 2 or remaining[0] != "--scope":
                raise ValueError(
                    f"unexpected trailing args {remaining!r}; supported: [--scope SCOPE]"
                )
            spec["scope"] = remaining[1]
        return name, url, spec

    raise ValueError(
        f"unknown auth flag {flag!r}; expected --bearer | --api-key HEADER | "
        "--oauth2 TOKEN_URL CLIENT_ID [--scope SCOPE]"
    )


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
