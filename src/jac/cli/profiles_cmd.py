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
    if not profiles:
        console.print("[dim]no profiles configured. Run [bold]jac init[/bold] to create one.[/dim]")
        return
    default = get_default_profile_name()
    console.print("[bold]Profiles:[/bold]")

    # Tier name column is sized to the widest configured tier across all profiles.
    tier_col_width = max(
        (len(t) for p in profiles.values() for t in p.tiers),
        default=6,
    )

    for name, p in profiles.items():
        marker = " [green](default)[/green]" if name == default else ""
        env_part = f"  [dim]env: {', '.join(sorted(p.env))}[/dim]" if p.env else ""
        console.print(f"  [bold]{name}[/bold]{marker}{env_part}")
        for tier_name, models in p.tiers.items():
            active = " [yellow]← active[/yellow]" if tier_name == p.active_tier else ""
            primary = models[0]
            alternates = f" [dim](+{len(models) - 1} alt)[/dim]" if len(models) > 1 else ""
            console.print(
                f"    [bold]{tier_name:<{tier_col_width}}[/bold]  "
                f"[dim]{primary}[/dim]{alternates}{active}"
            )
    console.print(
        "\n[dim]switch default:[/dim] [bold]jac profiles use <name>[/bold]"
        "  [dim]· one-shot:[/dim] [bold]jac --profile <name>[/bold]"
    )
