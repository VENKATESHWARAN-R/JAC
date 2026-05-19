"""Interactive REPL loop for JAC.

Phase 1 step 1: the CLI is a pure renderer. It installs a ``Hooks``
capability on Gru that pushes lifecycle events to an :class:`EventBus`,
and a :class:`CliRenderer` consumes the bus and draws to the terminal.

The agent run and the renderer run **concurrently** within each turn.
The renderer returns when it sees a terminal event
(:class:`RunCompleted` / :class:`RunFailed`); the REPL then awaits the
agent task to harvest the message history. Errors raised inside
``agent.run`` are emitted as :class:`RunFailed`, rendered, and absorbed
so the REPL continues to the next prompt.
"""

from __future__ import annotations

import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from pydantic_ai import Agent, AgentRunResult

from jac.capabilities.hooks import make_hooks
from jac.cli.renderer import CliRenderer
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.runtime.bus import EventBus
from jac.runtime.events import RunCompleted, RunFailed
from jac.runtime.gru import build_gru
from jac.workspace import paths

_EXIT_WORDS = {"exit", "quit", ":q", ":quit"}

console = Console()


def _make_session() -> PromptSession[str]:
    paths.USER_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(paths.USER_HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=False,
    )


def _greet(model_id: str) -> None:
    console.print(
        Panel.fit(
            "[bold cyan]JAC[/bold cyan] — phase 1 shell\n"
            f"[dim]model:[/dim] {model_id}\n"
            "[dim]type 'exit' or Ctrl-D to quit[/dim]",
            border_style="cyan",
        )
    )


async def _run_turn(gru: Agent, bus: EventBus, text: str, message_history: list) -> list:
    """Run one agent turn through the bus. Returns the updated message history."""
    renderer = CliRenderer(console)

    async def run_agent_and_signal() -> AgentRunResult[str]:
        try:
            result = await gru.run(text, message_history=message_history)
        except Exception as exc:  # noqa: BLE001 — emit and re-raise for the caller
            await bus.emit(RunFailed(error=str(exc)))
            raise
        await bus.emit(RunCompleted(output=str(result.output)))
        return result

    agent_task = asyncio.create_task(run_agent_and_signal())
    await renderer.consume(bus)

    try:
        result = await agent_task
    except Exception:
        # The renderer already captured the error from the bus event.
        renderer.print_final()
        return message_history

    renderer.print_final()
    return result.all_messages()


async def _repl_loop(model_override: str | None = None) -> None:
    try:
        bus = EventBus()
        hooks = make_hooks(bus)
        gru = build_gru(model_override=model_override, extra_capabilities=[hooks])
    except JacConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        return

    model_id = model_override or get_settings().model or "unknown"
    session = _make_session()
    _greet(model_id)

    message_history: list = []
    while True:
        try:
            text = await session.prompt_async("» ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return

        text = text.strip()
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            console.print("[dim]bye[/dim]")
            return

        message_history = await _run_turn(gru, bus, text, message_history)


def run_repl(model_override: str | None = None) -> None:
    """Synchronous wrapper around the async REPL loop."""
    asyncio.run(_repl_loop(model_override=model_override))
