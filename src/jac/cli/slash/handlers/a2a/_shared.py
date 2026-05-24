"""Shared rendering / prompt helpers for ``/a2a`` subcommands.

Kept in a sibling module rather than ``__init__.py`` so the dispatcher
stays a thin readable file.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext

DESCRIPTION_TRUNCATE_AT = 60


def auth_label(peer, *, colored: bool = True) -> str:
    """One-word auth tag for the peers listing. Pass ``colored=False`` for the
    shadowed-row variant (uncolored so the dim wrapper isn't fighting markup).
    """
    from jac.profiles import ApiKeyAuth, BearerAuth, OAuth2ClientCredentialsAuth

    if peer.auth is None:
        return "[yellow]none[/yellow]" if colored else "none"
    if isinstance(peer.auth, BearerAuth):
        return "[green]bearer[/green]" if colored else "bearer"
    if isinstance(peer.auth, ApiKeyAuth):
        return "[green]api_key[/green]" if colored else "api_key"
    if isinstance(peer.auth, OAuth2ClientCredentialsAuth):
        return "[green]oauth2[/green]" if colored else "oauth2"
    return "[red]unknown[/red]" if colored else "unknown"  # pragma: no cover


def desc_tail(peer) -> str:
    desc = peer.description or ""
    if not desc:
        return ""
    if len(desc) > DESCRIPTION_TRUNCATE_AT:
        desc = desc[: DESCRIPTION_TRUNCATE_AT - 1] + "…"
    return f"  [dim]— {desc}[/dim]"


def profile_defaults(ctx: SlashContext) -> tuple[str, int]:
    """Pull bind defaults from the active profile, fall back to schema defaults."""
    if ctx.profile is not None:
        return ctx.profile.a2a.host, ctx.profile.a2a.port
    from jac.profiles import A2AProfileConfig

    default = A2AProfileConfig()
    return default.host, default.port


def prompt_secret(label: str, ctx: SlashContext) -> str | None:
    """Read a secret from stdin with no echo. Empty input → cancel.

    Uses ``getpass.getpass`` rather than :class:`rich.prompt.Prompt` so
    the value never echoes back and never lands in scrollback. Slash
    dispatch is synchronous so the blocking call is fine.
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
