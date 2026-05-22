"""Shared session-list rendering used by both ``jac sessions`` and ``/sessions``.

Single source of truth for the "oldest → newest" listing format so the CLI
subcommand and the in-REPL slash command can't drift apart.
"""

from __future__ import annotations

from rich.console import Console

from jac.runtime.session import Session


def render_session_listing(console: Console, *, in_repl: bool = False) -> None:
    """Print all sessions in this project, oldest → newest.

    Args:
        console: where to render.
        in_repl: when ``True`` (slash-command context), the footer hints at the
            ``/resume`` form instead of the ``--resume`` CLI flag.
    """
    ids = Session.list_ids()
    if not ids:
        console.print(
            "[dim]no sessions yet in this project. Start one with [bold]jac[/bold].[/dim]"
        )
        return

    console.print("[bold]Sessions[/bold] (oldest → newest):")
    latest = ids[-1]
    for sid in ids:
        marker = " [green](latest)[/green]" if sid == latest else ""
        console.print(f"  {sid}{marker}")

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
