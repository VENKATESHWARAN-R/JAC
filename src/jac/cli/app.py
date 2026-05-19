"""JAC CLI entry point.

Usage::

    jac
    jac --model anthropic:claude-opus-4-6

A single-command Typer app. We use ``typer.run`` rather than a multi-command
app because the surface is intentionally narrow in v1: one command,
interactive.
"""

from __future__ import annotations

from typing import Annotated

import typer

from jac.capabilities.observability import setup_observability
from jac.cli.repl import run_repl


def cli(
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Override the default model (e.g. 'anthropic:claude-opus-4-6').",
        ),
    ] = None,
) -> None:
    """JAC — local-first AI coworker. Starts an interactive session."""
    setup_observability()
    run_repl(model_override=model)


def main() -> None:
    """Console-script entry point. See ``pyproject.toml`` ``[project.scripts]``."""
    typer.run(cli)


if __name__ == "__main__":
    main()
