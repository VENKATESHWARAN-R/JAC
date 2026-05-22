"""Session-management slash commands: ``/sessions`` / ``/resume`` / ``/clear``."""

from __future__ import annotations

from jac.cli.session_view import render_session_listing
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult, SwitchSession
from jac.errors import JacConfigError
from jac.runtime.session import Session


@register(
    "sessions",
    summary="List sessions in this project, oldest → newest",
    usage="/sessions",
)
def sessions_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args:
        ctx.console.print("[dim]/sessions takes no arguments[/dim]")
    render_session_listing(ctx.console, in_repl=True)
    return Handled()


@register(
    "resume",
    summary="Switch to a different session (latest if no id)",
    usage="/resume [ID]",
)
def resume_handler(ctx: SlashContext, args: str) -> SlashResult:
    target = args.strip()
    try:
        new_session = Session.resume_latest() if not target else Session.resume(target)
    except JacConfigError as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()

    if new_session.session_id == ctx.session.session_id:
        ctx.console.print(f"[dim]already on session {new_session.session_id}[/dim]")
        return Handled()

    return SwitchSession(session=new_session)


@register(
    "clear",
    summary="Start a fresh session in place (current session is preserved on disk)",
    usage="/clear",
)
def clear_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args:
        ctx.console.print("[dim]/clear takes no arguments[/dim]")
    return SwitchSession(session=Session.new())
