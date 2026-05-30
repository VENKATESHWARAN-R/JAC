"""``jac web`` — local-first web UI subcommand (D48).

Boots a Starlette app (see :func:`jac.web.server.create_app`) under uvicorn in
the foreground. Mirrors :mod:`jac.cli.a2a` in shape: ensure the workspace, set
up observability, parse bind flags, run.

Unlike the REPL path, this command does **not** activate a profile or fail-first
on missing credentials — the panel exists precisely to *set those up*, so it
must boot on a fresh workspace. Same posture as ``jac keys`` / ``jac profiles``.

**Security model:** binds ``127.0.0.1`` by default. The loopback boundary is the
only access control — there are no accounts. Binding a non-loopback address is
allowed but warns loudly, because the settings panel reads and writes API keys.
"""

from __future__ import annotations

import threading
import webbrowser
from typing import Annotated

import typer
from rich.console import Console

from jac.runtime.observability import setup_observability
from jac.workspace import paths
from jac.workspace.bootstrap import ensure_user_workspace

app = typer.Typer(
    name="web",
    help="Local-first web UI: chat + a settings panel for profiles, keys, and sessions (D48).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8770
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


@app.command("serve")
def serve_command(
    host: Annotated[
        str,
        typer.Option("--host", help="Bind address. Defaults to 127.0.0.1 (loopback)."),
    ] = _DEFAULT_HOST,
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Bind port."),
    ] = _DEFAULT_PORT,
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the UI in your browser on start."),
    ] = True,
) -> None:
    """Start the JAC web UI in the foreground. Ctrl-C to stop.

    The panel manages this workspace's configuration and sessions. Which
    sessions appear depends on where you launch it: inside a project (a ``.git``
    or ``.agents`` marker) it shows that project's sessions; in a loose folder it
    shows the global ``~/.jac`` pool.
    """
    ensure_user_workspace()
    setup_observability()

    if host not in _LOOPBACK_HOSTS:
        console.print(
            f"[red bold]⚠ binding {host} (non-loopback):[/red bold] the JAC web UI is "
            "single-user and unauthenticated, and its settings panel reads and writes "
            "API keys [dim]in the clear over HTTP[/dim]. Anyone who can reach this "
            "address can drive your agent and read your credentials. Use only on a "
            "network you fully trust.",
            highlight=False,
        )

    scope = (
        f"project [bold]{paths.project_root()}[/bold]"
        if paths.in_project()
        else f"[yellow]global[/yellow] ([dim]{paths.USER_WORKSPACE}[/dim] — no project here)"
    )
    url = f"http://{host}:{port}"
    console.print(f"[bold yellow]JAC web[/bold yellow] — {url}", highlight=False)
    console.print(f"[dim]workspace:[/dim] {scope}", highlight=False)
    console.print("[dim]Ctrl-C to stop[/dim]", highlight=False)

    if open_browser:
        # Open after a short delay so uvicorn is listening; localhost retries
        # are cheap if we're slightly early. Daemon thread so it never blocks
        # shutdown.
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    import uvicorn

    from jac.web.server import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
