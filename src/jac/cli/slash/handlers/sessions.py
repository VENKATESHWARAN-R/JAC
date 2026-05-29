"""``/sessions`` — list / delete / prune sessions in this project.

    /sessions                       list, oldest → newest
    /sessions delete <id>           delete one session (not the active one)
    /sessions prune <dur>           preview sessions older than <dur> (e.g. 30d)
    /sessions prune <dur> yes       actually delete them

Destructive subcommands take an explicit confirmation token (the id you
type, or a trailing ``yes``) rather than a blocking prompt — the REPL input
loop is owned by prompt-toolkit, so we don't pop a second reader under it.
Deleting leaves ``usage.jsonl`` intact, matching ``jac sessions``.
"""

from __future__ import annotations

from jac.cli.session_view import render_session_listing
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.errors import JacConfigError
from jac.runtime.session import Session, parse_duration


@register(
    "sessions",
    summary="List / delete / prune project sessions",
    usage="/sessions [delete <id> | prune <dur> [yes]]",
)
def sessions_handler(ctx: SlashContext, args: str) -> SlashResult:
    parts = args.split()
    if not parts:
        render_session_listing(ctx.console, in_repl=True)
        return Handled()

    sub, rest = parts[0].lower(), parts[1:]
    if sub == "delete":
        return _handle_delete(ctx, rest)
    if sub == "prune":
        return _handle_prune(ctx, rest)

    ctx.console.print(
        f"[red]unknown:[/red] /sessions {sub}  "
        "[dim](use [bold]/sessions[/bold], [bold]/sessions delete <id>[/bold], "
        "or [bold]/sessions prune <dur>[/bold])[/dim]"
    )
    return Handled()


def _handle_delete(ctx: SlashContext, rest: list[str]) -> SlashResult:
    if not rest:
        ctx.console.print("[dim]usage: /sessions delete <id>[/dim]")
        return Handled()
    sid = rest[0]
    if sid == ctx.session.session_id:
        ctx.console.print(
            "[yellow]can't delete the active session.[/yellow] "
            "[dim]Switch away first with [bold]/clear[/bold] or [bold]/resume <id>[/bold].[/dim]"
        )
        return Handled()
    try:
        Session.delete(sid)
    except JacConfigError as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()
    ctx.console.print(f"[green]✓[/green] deleted session [bold]{sid}[/bold]")
    return Handled()


def _handle_prune(ctx: SlashContext, rest: list[str]) -> SlashResult:
    if not rest:
        ctx.console.print(
            "[dim]usage: /sessions prune <dur> [yes]  — e.g. /sessions prune 30d[/dim]"
        )
        return Handled()
    try:
        max_age = parse_duration(rest[0])
    except ValueError as exc:
        ctx.console.print(f"[red]error:[/red] {exc}")
        return Handled()

    from datetime import datetime

    confirmed = len(rest) > 1 and rest[1].lower() == "yes"
    cutoff = datetime.now() - max_age
    # Never prune the active session, even if it's old.
    doomed = [
        s.session_id
        for s in Session.list_summaries()
        if s.created and s.created < cutoff and s.session_id != ctx.session.session_id
    ]
    if not doomed:
        ctx.console.print(f"[dim]no sessions older than {rest[0]} — nothing to prune.[/dim]")
        return Handled()

    if not confirmed:
        ctx.console.print(f"[bold]{len(doomed)}[/bold] session(s) older than {rest[0]}:")
        for sid in doomed:
            ctx.console.print(f"  {sid}")
        ctx.console.print(
            f"[dim]re-run [bold]/sessions prune {rest[0]} yes[/bold] to delete them.[/dim]"
        )
        return Handled()

    for sid in doomed:
        Session.delete(sid)
    ctx.console.print(f"[green]✓[/green] pruned [bold]{len(doomed)}[/bold] session(s)")
    return Handled()
