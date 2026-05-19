"""JAC CLI entry point.

Multi-command Typer app:

- ``jac``           — start an interactive REPL with Gru.
- ``jac init``      — interactive setup wizard (provider, model, config write).

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

    # Default action: start the REPL.
    from jac.cli.repl import run_repl

    run_repl(model_override=model)


@app.command("init")
def init_command() -> None:
    """Run the interactive setup wizard (provider + model + config write)."""
    from jac.cli.init import run_init

    run_init()


def main() -> None:
    """Console-script entry point. See ``pyproject.toml`` ``[project.scripts]``."""
    app()


if __name__ == "__main__":
    main()
