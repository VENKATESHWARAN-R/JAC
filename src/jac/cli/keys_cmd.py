"""``jac keys`` subcommand group — inspect / set / unset stored credentials."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.prompt import Prompt

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles_crud import list_profiles
from jac.secrets import get_backend, resolve

app = typer.Typer(
    name="keys",
    help="Inspect and manage credentials JAC's profiles need.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """`jac keys` with no subcommand shows status."""
    if ctx.invoked_subcommand is not None:
        return
    _status()


@app.command("list")
def list_cmd() -> None:
    """Show status of credentials required by configured profiles."""
    _status()


@app.command("set")
def set_cmd(
    key: str = typer.Argument(..., help="Env var name, e.g. ANTHROPIC_API_KEY"),
) -> None:
    """Prompt for KEY's value and store it in the configured backend."""
    backend = get_backend()
    if backend.name == "env-only":
        console.print(
            "[red]error:[/red] secrets backend is [bold]env-only[/bold]; JAC won't store "
            "credentials. Set in your shell, or run `jac init` to change backend."
        )
        raise typer.Exit(1)
    value = Prompt.ask(f"Enter [bold]{key}[/bold]", password=True)
    if not value:
        console.print("[dim]aborted (empty input)[/dim]")
        return
    backend.set(key, value)
    console.print(f"[green]✓[/green] stored [bold]{key}[/bold] in {backend.name}")


@app.command("unset")
def unset_cmd(
    key: str = typer.Argument(..., help="Env var name to delete from the backend."),
) -> None:
    """Delete KEY from the configured backend."""
    backend = get_backend()
    if backend.name == "env-only":
        console.print(
            "[red]error:[/red] secrets backend is [bold]env-only[/bold]; nothing to unset."
        )
        raise typer.Exit(1)
    try:
        backend.unset(key)
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] unset [bold]{key}[/bold] in {backend.name}")


# ---------- helpers ----------


def _status() -> None:
    try:
        profiles = list_profiles()
    except JacConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from None

    if not profiles:
        console.print("[dim]no profiles configured. Run [bold]jac init[/bold].[/dim]")
        return

    # Collect required keys across profiles.
    required_by: dict[str, list[str]] = {}
    for name, profile in profiles.items():
        for key in profile.required_env_keys():
            required_by.setdefault(key, []).append(name)

    backend_name = get_settings().secrets.backend
    console.print(f"[bold]Keys[/bold] [dim](backend: {backend_name})[/dim]\n")

    if not required_by:
        console.print(
            "[dim]configured profiles don't require any secrets (e.g. Ollama-only).[/dim]"
        )
        return

    for key in sorted(required_by):
        value, source = resolve(key)
        if value is None:
            marker = "[red]missing[/red]"
        else:
            marker = f"[green]set[/green] [dim]({source})[/dim]"
        users = ", ".join(required_by[key])
        console.print(f"  [bold]{key}[/bold] — {marker}  [dim]used by: {users}[/dim]")
