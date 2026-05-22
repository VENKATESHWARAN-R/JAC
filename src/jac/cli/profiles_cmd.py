"""``jac profiles`` subcommand group — list / use / remove."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.prompt import Confirm

from jac.errors import JacConfigError
from jac.profiles import (
    add_or_update_profile,
    get_default_profile_name,
    get_profile,
    list_profiles,
    load_profile_from_yaml,
    profile_to_yaml,
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


@app.command("edit")
def edit_cmd(
    name: str = typer.Argument(..., help="Profile to edit."),
) -> None:
    """Open NAME in your $EDITOR — hand-edit YAML, validate on save.

    On invalid YAML or schema errors you'll be offered to re-open the editor
    or abort; the profile on disk is only overwritten after a clean validate.
    """
    try:
        current = get_profile(name)
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None

    from jac.cli.editor import edit_text

    header = _edit_header(name)
    initial = header + profile_to_yaml(current)
    text = initial

    while True:
        edited = edit_text(text)
        if edited is None:
            console.print("[dim]no changes — profile left as-is[/dim]")
            return
        try:
            new_profile = load_profile_from_yaml(_strip_header_comments(edited))
        except JacConfigError as exc:
            console.print(f"\n[red]invalid profile:[/red] {exc}")
            if not Confirm.ask("Re-open editor to fix?", default=True):
                console.print("[dim]aborted — profile on disk is unchanged[/dim]")
                return
            text = edited  # preserve their attempt so they don't lose work
            continue
        add_or_update_profile(name, new_profile)
        console.print(f"[green]✓[/green] saved profile [bold]{name}[/bold]")
        return


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


def _edit_header(name: str) -> str:
    """The instructional comment block prepended to the YAML in $EDITOR.

    Stripped before parsing — but the user can leave it in, the YAML parser
    treats ``#`` lines as comments anyway. We strip it on save so re-edits
    don't accumulate stale headers if we ever change the wording.
    """
    return (
        f"# Editing profile {name!r}.\n"
        "#\n"
        "# tiers: ordered lists per tier; first entry is the tier's default.\n"
        "# active_tier: tier Gru starts on (must be a key in tiers).\n"
        "# env (optional): non-secret env vars set when this profile activates.\n"
        "# requires_env (optional): override automatic secret inference.\n"
        "#\n"
        "# Save and exit to apply. Exit without saving (or :cq in vim) to abort.\n"
        "# If validation fails you'll be offered to re-open and fix it.\n"
        "\n"
    )


def _strip_header_comments(text: str) -> str:
    """Drop leading comment / blank lines so YAML round-trips cleanly.

    Only touches the *prefix* — comments interleaved with content stay put
    (YAML treats them as comments anyway; this is purely cosmetic).
    """
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    return "".join(lines[i:])


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
