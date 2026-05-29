"""Shared session-list rendering used by both ``jac sessions`` and ``/sessions``.

Single source of truth for the "oldest → newest" listing format so the CLI
subcommand and the in-REPL slash command can't drift apart. Each row shows
the session id, its message count, and a human-readable creation time
derived from the timestamp id — enough to tell sessions apart at a glance.
"""

from __future__ import annotations

from rich.console import Console

from jac.runtime.session import Session, SessionSummary

_HUMAN_FMT = "%b %d, %Y %H:%M"


def _format_count(count: int | None) -> str:
    if count is None:
        return "unreadable"
    return f"{count} msg" if count == 1 else f"{count} msgs"


def _format_created(summary: SessionSummary) -> str:
    if summary.created is None:
        return ""
    return summary.created.strftime(_HUMAN_FMT)


def render_session_listing(console: Console, *, in_repl: bool = False) -> None:
    """Print all sessions in this project, oldest → newest.

    Args:
        console: where to render.
        in_repl: when ``True`` (slash-command context), the footer hints at the
            ``/resume`` form instead of the ``--resume`` CLI flag.
    """
    summaries = Session.list_summaries()
    if not summaries:
        console.print(
            "[dim]no sessions yet in this project. Start one with [bold]jac[/bold].[/dim]"
        )
        return

    console.print("[bold]Sessions[/bold] (oldest → newest):")
    latest_id = summaries[-1].session_id
    # Right-pad the (plain-text) count column so the date column lines up.
    count_strs = {s.session_id: _format_count(s.message_count) for s in summaries}
    count_width = max(len(c) for c in count_strs.values())
    for summary in summaries:
        marker = " [green](latest)[/green]" if summary.session_id == latest_id else ""
        count = count_strs[summary.session_id]
        count_style = "red" if summary.message_count is None else "dim"
        pad = " " * (count_width - len(count))
        created = _format_created(summary)
        date_part = f"  [dim]{created}[/dim]" if created else ""
        console.print(
            f"  {summary.session_id}  [{count_style}]{count}[/{count_style}]{pad}"
            f"{date_part}{marker}"
        )

    if in_repl:
        console.print(
            "\n[dim]resume the latest:[/dim] [bold]/resume[/bold]"
            "  [dim]· resume by id:[/dim] [bold]/resume <id>[/bold]"
        )
    else:
        console.print(
            "\n[dim]resume the latest:[/dim] [bold]jac --resume[/bold]"
            "  [dim]· resume by id:[/dim] [bold]jac --session <id>[/bold]"
        )
