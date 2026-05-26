"""``jac a2a`` — headless A2A server subcommand (D24, Phase 4.a).

Runs the same server as ``/a2a serve`` does inside the REPL, but
without a REPL. Useful for:

- Long-running A2A endpoints in tmux / systemd / a CI environment.
- Exposing a project to peers when you don't need an interactive
  session yourself.
- Testing — peers can hit a known port without you babysitting a REPL.

Lifecycle:

1. Activate the resolved profile so :func:`build_guest_gru` has the
   right model and credentials in ``os.environ`` (same path the REPL
   uses — :func:`apply_profile_env`).
2. Build an :class:`A2ACapability` with no event bus (no renderer to
   feed) and start the server with the parsed flags.
3. Print the bearer token + serving URL to stdout (the headless
   "startup banner" — operators capture this for peer config).
4. Sleep on an asyncio Event until Ctrl-C / SIGTERM, then call
   :meth:`A2ACapability.shutdown` for clean teardown.

The renderer / hooks / approval surface is NOT installed — there's no
human to prompt and the guest toolset is auto-approve anyway. Logfire
captures everything via the spans fasta2a already emits.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console

from jac.capabilities.a2a import make_a2a_capability
from jac.cli._a2a_banner import print_server_started_banner
from jac.errors import JacConfigError
from jac.profiles_crud import get_profile, resolve_active_profile_name
from jac.runtime.events import (
    A2AInboundCall,
    A2AInboundCompleted,
    A2AServerStopped,
    EventBus,
)
from jac.runtime.observability import setup_observability
from jac.secrets import apply_profile_env
from jac.workspace.bootstrap import ensure_user_workspace

app = typer.Typer(
    name="a2a",
    help="Headless A2A server (D24).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command("serve")
def serve_command(
    host: Annotated[
        str | None,
        typer.Option(
            "--host",
            help="Bind address. Defaults to the active profile's a2a.host (127.0.0.1).",
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            "-p",
            help="Bind port. Defaults to the active profile's a2a.port (8001).",
        ),
    ] = None,
    unsafe: Annotated[
        bool,
        typer.Option(
            "--unsafe",
            help="Skip bearer auth. Card omits securitySchemes. Use only on trusted networks.",
        ),
    ] = False,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="Profile to run under. Defaults to default_profile.",
        ),
    ] = None,
) -> None:
    """Start the A2A guest server in the foreground (no REPL).

    Same behavior as ``/a2a serve`` but without an interactive session.
    Prints the bearer token once, then sleeps until SIGINT/SIGTERM.
    """
    ensure_user_workspace()
    setup_observability()

    try:
        active_profile_name = resolve_active_profile_name(profile)
        active_profile = get_profile(active_profile_name)
        apply_profile_env(active_profile_name, active_profile)
    except JacConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from None

    # Resolve bind defaults: CLI flag > profile default > schema default.
    effective_host = host if host is not None else active_profile.a2a.host
    effective_port = port if port is not None else active_profile.a2a.port

    # Pull JAC_MODEL after apply_profile_env so the guest gets the
    # profile's active-tier default model.
    model_id = os.environ.get("JAC_MODEL")
    if not model_id:
        console.print(
            "[red]config error:[/red] profile activation didn't set JAC_MODEL "
            "(profile is malformed?)"
        )
        raise typer.Exit(1)

    if unsafe:
        console.print(
            "[red bold]⚠ --unsafe:[/red bold] starting A2A server with no authentication. "
            "[dim]Any client that can reach the port can drive the guest Gru.[/dim]"
        )

    try:
        asyncio.run(
            _serve(
                profile_name=active_profile_name,
                model_id=model_id,
                host=effective_host,
                port=effective_port,
                unsafe=unsafe,
                retention_days=active_profile.a2a.context_retention_days,
            )
        )
    except KeyboardInterrupt:
        # asyncio.run propagates the cancellation; we land here on Ctrl-C
        # after the shutdown path has already run.
        console.print("\n[dim]bye[/dim]")


async def _serve(
    *,
    profile_name: str,
    model_id: str,
    host: str,
    port: int,
    unsafe: bool,
    retention_days: int,
) -> None:
    """The actual async lifecycle. Boots the server, waits, tears down."""
    # Headless still wants per-call feedback in the terminal — give it a
    # bus + a small printer task so operators see who connected and how
    # the call resolved. Inside the REPL the renderer owns this; here we
    # roll a stripped-down equivalent.
    bus = EventBus()
    cap = make_a2a_capability(
        bus=bus,
        model=model_id,
        profile_name=profile_name,
        retention_days=retention_days,
    )
    try:
        info = await cap.start_server(host=host, port=port, unsafe=unsafe)
    except (JacConfigError, OSError, RuntimeError) as exc:
        console.print(f"[red]A2A serve failed:[/red] {exc}")
        return

    print_server_started_banner(
        info,
        console,
        profile_name=profile_name,
        token_hint="paste below into peer config; rotates on every restart",
    )
    console.print("[dim]Ctrl-C or SIGTERM to stop[/dim]")

    printer_task = asyncio.create_task(_print_events(bus), name="jac.a2a.headless_printer")

    # Block until a signal arrives. asyncio.Event ensures we yield to
    # the uvicorn task while we wait, instead of busy-spinning.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows / restricted envs don't allow add_signal_handler; the
        # KeyboardInterrupt path in serve_command catches Ctrl-C anyway.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    try:
        await stop_event.wait()
    finally:
        await cap.shutdown()
        printer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await printer_task
        console.print("[green]✓ A2A server stopped[/green]")


async def _print_events(bus: EventBus) -> None:
    """Stream A2A lifecycle events to the headless console.

    Mirrors the REPL renderer's formatting so scrollback looks the same
    whether the server is run via ``jac a2a serve`` or ``/a2a serve``
    inside the REPL. Cancellation is the normal exit path.
    """
    async for event in bus.stream():
        if isinstance(event, A2AInboundCall):
            unsafe_tag = " [red](unsafe)[/red]" if event.peer_id == "unsafe" else ""
            console.print(
                f"[dim]{_ts()}[/dim] [cyan][a2a in ←][/cyan] [bold]{event.peer_id}[/bold]"
                f"{unsafe_tag} [dim](task {event.task_id[:8]})[/dim]: {event.message_preview}",
                highlight=False,
            )
        elif isinstance(event, A2AInboundCompleted):
            state_color = "green" if event.state == "completed" else "red"
            console.print(
                f"[dim]{_ts()}[/dim] [cyan][a2a in ✓][/cyan] "
                f"[{state_color}]{event.state}[/{state_color}] "
                f"[dim]{event.peer_id} (task {event.task_id[:8]}, "
                f"{event.duration_ms}ms, {event.tokens_used} tok)[/dim]",
                highlight=False,
            )
        elif isinstance(event, A2AServerStopped):
            # The shutdown banner is already printed; nothing to add.
            pass


def _ts() -> str:
    """``HH:MM:SS`` for the headless event log (local time)."""
    return datetime.now().strftime("%H:%M:%S")
