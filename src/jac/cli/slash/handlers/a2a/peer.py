"""``/a2a peer add|remove`` — manage session-scoped A2A peers (D31).

Secrets (bearer token / api-key value / OAuth2 client_secret) are NEVER
passed on the command line — they're prompted for via :func:`getpass.getpass`
so they don't appear in shell or prompt-toolkit history.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a._args import parse_peer_add
from jac.cli.slash.handlers.a2a._shared import prompt_secret
from jac.cli.slash.result import Handled, SlashResult


def handle(ctx: SlashContext, rest: str) -> SlashResult:
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
    """``/a2a peer add NAME URL [--bearer | --api-key HEADER | --oauth2 ...]``."""
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
        name, url, auth_spec = parse_peer_add(args)
    except ValueError as exc:
        ctx.console.print(f"[red]invalid /a2a peer add args:[/red] {exc}")
        return Handled()

    try:
        validate_profile_name(name)
    except Exception as exc:
        ctx.console.print(f"[red]invalid peer name:[/red] {exc}")
        return Handled()

    auth = None
    try:
        if auth_spec is None:
            ctx.console.print(
                "[yellow]no auth flag given;[/yellow] peer will be added "
                "without authentication (works only against --unsafe peers)."
            )
        elif auth_spec["kind"] == "bearer":
            token = prompt_secret("bearer token", ctx)
            if token is None:
                return Handled()
            auth = BearerAuth(token=token)
        elif auth_spec["kind"] == "api_key":
            value = prompt_secret(f"value for header {auth_spec['header']!r}", ctx)
            if value is None:
                return Handled()
            auth = ApiKeyAuth(header=auth_spec["header"], value=value)
        elif auth_spec["kind"] == "oauth2":
            client_secret = prompt_secret(f"client_secret for {auth_spec['client_id']!r}", ctx)
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
