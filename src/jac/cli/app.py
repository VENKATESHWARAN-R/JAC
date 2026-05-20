"""JAC CLI entry point.

Multi-command Typer app:

- ``jac``                — start a fresh interactive REPL with Gru.
- ``jac --resume``       — resume the latest session in this project.
- ``jac --session ID``   — resume a specific session by id.
- ``jac init``           — interactive setup wizard (provider, model, config).
- ``jac sessions``       — list known sessions for this project.

The root callback runs the silent workspace bootstrap on every invocation
so that ``~/.jac/`` always exists by the time anything tries to read from
it. Interactive setup is a separate, explicit subcommand.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from jac.capabilities.observability import setup_observability
from jac.workspace.bootstrap import ensure_user_workspace

app = typer.Typer(
    name="jac",
    help="JAC — local-first AI coworker harness.",
    no_args_is_help=False,
    add_completion=False,
)

console = Console()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Override the configured model (e.g. 'anthropic:claude-opus-4-6').",
        ),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume",
            "-r",
            help="Resume the latest session in this project.",
        ),
    ] = False,
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            "-s",
            help="Resume a specific session by id (see `jac sessions`).",
        ),
    ] = None,
) -> None:
    """JAC — start an interactive session. Use `jac init` for first-time setup."""
    # Bootstrap on every entry so the workspace exists before anything reads it.
    just_created = ensure_user_workspace()
    setup_observability()

    if just_created:
        console.print(
            "[dim]first run — created skeleton at ~/.jac/. "
            "Run [bold]jac init[/bold] for guided setup.[/dim]"
        )

    if ctx.invoked_subcommand is not None:
        # A subcommand will handle the rest.
        return

    # Default action: start the REPL with optional session resume.
    from jac.cli.repl import run_repl

    run_repl(model_override=model, resume_latest=resume, resume_id=session)


@app.command("init")
def init_command() -> None:
    """Run the interactive setup wizard (provider + model + config write)."""
    from jac.cli.init import run_init

    run_init()


@app.command("sessions")
def sessions_command() -> None:
    """List sessions for this project, oldest → newest."""
    from jac.runtime.session import Session

    ids = Session.list_ids()
    if not ids:
        console.print(
            "[dim]no sessions yet in this project. "
            "Start one with [bold]jac[/bold].[/dim]"
        )
        return

    console.print("[bold]Sessions[/bold] (oldest → newest):")
    latest = ids[-1]
    for sid in ids:
        marker = " [green](latest)[/green]" if sid == latest else ""
        console.print(f"  {sid}{marker}")
    console.print(
        "\n[dim]resume the latest:[/dim] [bold]jac --resume[/bold]"
        "  [dim]· resume by id:[/dim] [bold]jac --session <id>[/bold]"
    )


def main() -> None:
    """Console-script entry point. See ``pyproject.toml`` ``[project.scripts]``."""
    app()


if __name__ == "__main__":
    main()
