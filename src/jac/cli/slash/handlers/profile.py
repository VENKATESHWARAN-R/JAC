"""``/profile`` — list or switch profiles.

- ``/profile`` (no arg) — render every configured profile with the active
  one marked. Re-uses :func:`jac.cli.profile_view.render_profile_listing`
  so output matches ``jac profiles list``.
- ``/profile NAME`` — drive the control plane's profile switch (which owns the
  env-snapshot/rollback so a missing credential leaves Gru on the previous
  profile), then render the outcome. On unknown profile name the handler
  reports the error in-band and returns :class:`Handled`.
"""

from __future__ import annotations

from jac.cli.profile_view import render_profile_listing
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.render import render_switch
from jac.cli.slash.result import Handled, SlashResult
from jac.errors import JacConfigError
from jac.profiles_crud import get_default_profile_name, get_profile, list_profiles


@register(
    "profile",
    summary="List or switch the active profile (fail-safe — keeps current on error)",
    usage="/profile [NAME]",
)
def profile_handler(ctx: SlashContext, args: str) -> SlashResult:
    target = args.strip()
    if not target:
        return _list(ctx)
    return _switch(ctx, target)


def _list(ctx: SlashContext) -> SlashResult:
    try:
        profiles = list_profiles()
    except JacConfigError as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()
    render_profile_listing(
        ctx.console,
        profiles,
        default_name=get_default_profile_name(),
        active_name=ctx.profile_name,
    )
    return Handled()


def _switch(ctx: SlashContext, name: str) -> SlashResult:
    if name == ctx.profile_name:
        ctx.console.print(f"[dim]already on profile {name!r}[/dim]")
        return Handled()
    # Resolve up front so an unknown name reports cleanly without touching env.
    try:
        get_profile(name)
    except JacConfigError as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()
    if ctx.controller is None:  # pragma: no cover - always wired in the REPL
        ctx.console.print("[yellow]control plane is not wired into this session[/yellow]")
        return Handled()
    # The controller owns the env-snapshot/rollback so a missing credential
    # surfaces as a warning and leaves Gru on the previous profile.
    render_switch(ctx, ctx.controller.switch_profile(name))
    return Handled()
