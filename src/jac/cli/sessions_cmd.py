"""``jac sessions`` subcommand group — list / delete / prune.

``jac sessions`` with no subcommand lists; ``jac sessions delete <id>`` and
``jac sessions prune --older-than <dur>`` manage retention. Deletion removes
the session directory (messages, plan, compacted slices) but leaves
``usage.jsonl`` intact — those tokens were spent and still count toward
``project_total``.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.prompt import Confirm

from jac.errors import JacConfigError
from jac.runtime.session import Session, parse_duration

app = typer.Typer(
    name="sessions",
    help="List and manage project sessions.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """`jac sessions` with no subcommand lists them."""
    if ctx.invoked_subcommand is not None:
        return
    from jac.cli.session_view import render_session_listing

    render_session_listing(console, in_repl=False)


@app.command("list")
def list_cmd() -> None:
    """List all sessions for this project, oldest → newest."""
    from jac.cli.session_view import render_session_listing

    render_session_listing(console, in_repl=False)


@app.command("delete")
def delete_cmd(
    session_id: str = typer.Argument(..., help="Session id to delete (see `jac sessions`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete one session. The token-usage ledger is left untouched."""
    if not yes and not Confirm.ask(f"Delete session [bold]{session_id}[/bold]?", default=False):
        console.print("[dim]cancelled[/dim]")
        return
    try:
        Session.delete(session_id)
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] deleted session [bold]{session_id}[/bold]")


@app.command("prune")
def prune_cmd(
    older_than: str = typer.Option(
        ...,
        "--older-than",
        help="Delete sessions older than this (e.g. 30d, 12h, 2w).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete sessions created more than the given age ago.

    Age is read from the session's timestamp id; hand-renamed sessions whose
    id isn't a timestamp are skipped, never deleted.
    """
    try:
        max_age = parse_duration(older_than)
    except ValueError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None

    # Preview what would go before touching anything.
    from datetime import datetime

    cutoff = datetime.now() - max_age
    doomed = [s.session_id for s in Session.list_summaries() if s.created and s.created < cutoff]
    if not doomed:
        console.print(f"[dim]no sessions older than {older_than} — nothing to prune.[/dim]")
        return

    console.print(f"[bold]{len(doomed)}[/bold] session(s) older than {older_than}:")
    for sid in doomed:
        console.print(f"  {sid}")
    if not yes and not Confirm.ask("Delete these?", default=False):
        console.print("[dim]cancelled[/dim]")
        return

    deleted = Session.prune_older_than(max_age)
    console.print(f"[green]✓[/green] pruned [bold]{len(deleted)}[/bold] session(s)")
