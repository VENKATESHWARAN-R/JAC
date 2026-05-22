"""``jac profiles`` subcommand group — list / use / remove."""

from __future__ import annotations

import typer
from rich.console import Console

from jac.errors import JacConfigError
from jac.profiles import (
    get_default_profile_name,
    list_profiles,
    remove_profile,
    set_default_profile,
)

app = typer.Typer(
    name="profiles",
    help="Manage JAC profiles.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """`jac profiles` with no subcommand lists them."""
    if ctx.invoked_subcommand is not None:
        return
    _list_profiles()


@app.command("list")
def list_cmd() -> None:
    """List all profiles, marking the default."""
    _list_profiles()


@app.command("use")
def use_cmd(
    name: str = typer.Argument(..., help="Profile to set as default."),
) -> None:
    """Set NAME as the default profile."""
    try:
        set_default_profile(name)
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] default profile is now [bold]{name}[/bold]")


@app.command("remove")
def remove_cmd(
    name: str = typer.Argument(..., help="Profile to remove."),
) -> None:
    """Remove a profile from config.yaml. Stored secrets are kept."""
    try:
        remove_profile(name)
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None
    console.print(
        f"[green]✓[/green] removed profile [bold]{name}[/bold] "
        "[dim](any stored keys are kept — use `jac keys unset` if you want them gone)[/dim]"
    )


# ---------- helpers ----------


def _list_profiles() -> None:
    try:
        profiles = list_profiles()
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None
    from jac.cli.profile_view import render_profile_listing

    render_profile_listing(console, profiles, default_name=get_default_profile_name())
    if profiles:
        console.print(
            "\n[dim]switch default:[/dim] [bold]jac profiles use <name>[/bold]"
            "  [dim]· one-shot:[/dim] [bold]jac --profile <name>[/bold]"
        )
