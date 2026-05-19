"""Interactive REPL loop for JAC.

Phase 0: plain non-streaming send/receive against the bare Gru agent. The
hooks-driven event bus (ARCHITECTURE.md §7) lands in Phase 1 — at which point
this file becomes a *renderer* that consumes events rather than directly
calling ``gru.run``. Keep that future shape in mind: nothing in this file
should grow runtime logic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from jac.config import settings
from jac.errors import JacConfigError
from jac.runtime.gru import build_gru

_HISTORY_PATH = Path.home() / ".jac" / "history"
_EXIT_WORDS = {"exit", "quit", ":q", ":quit"}

console = Console()


def _make_session() -> PromptSession[str]:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(_HISTORY_PATH)),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=False,
    )


def _greet(model_id: str) -> None:
    console.print(
        Panel.fit(
            "[bold cyan]JAC[/bold cyan] — phase 0 shell\n"
            f"[dim]model:[/dim] {model_id}\n"
            "[dim]type 'exit' or Ctrl-D to quit[/dim]",
            border_style="cyan",
        )
    )


async def _repl_loop(model_override: str | None = None) -> None:
    try:
        gru = build_gru(model_override=model_override)
    except JacConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        return

    # build_gru guarantees a model was resolved if we got here.
    model_id = model_override or settings.model or "unknown"
    session = _make_session()
    _greet(model_id)

    # Phase 0: in-memory only. Session persistence is Phase 1.
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

        try:
            result = await gru.run(text, message_history=message_history)
        except Exception as exc:  # noqa: BLE001 — surface model/transport errors to the user
            console.print(f"[red]error[/red]: {exc}")
            continue

        message_history = result.all_messages()
        console.print(Markdown(str(result.output)))


def run_repl(model_override: str | None = None) -> None:
    """Synchronous wrapper around the async REPL loop."""
    asyncio.run(_repl_loop(model_override=model_override))
