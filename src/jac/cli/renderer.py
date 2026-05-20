"""CLI renderer — consumes JAC events and draws to the terminal.

The renderer owns no runtime state and makes no decisions about what the
agent should do. It only observes events and renders them. This separation
is what lets us add a TUI or web surface later without changing the
runtime: drop in a different renderer with the same bus interface.

Approval flow: when an :class:`ApprovalRequest` arrives, the renderer
pauses the status spinner, prompts the user via ``rich``'s Confirm, and
resolves the request's future. The runtime's approval handler awaits that
future before continuing the agent loop.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from jac.runtime.bus import EventBus
from jac.runtime.events import (
    ApprovalRequest,
    ApprovalResponse,
    JacEvent,
    ModelRequestCompleted,
    ModelRequestStarted,
    RunCompleted,
    RunFailed,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    is_terminal,
)

# Status-line easter eggs — minion-adjacent gibberish, not always "thinking…"
_THINKING_LABELS: tuple[str, ...] = (
    "thinking…",
    "banana…",
    "bello…",
    "papoi…",
    "poopaye…",
    "gelato…",
    "bee do bee do…",
    "tank yu…",
    "la boda…",
    "underpants…",
    "poulet tikka masala…",
    "fluffay stuffay…",
    "kanpai…",
    "chasy…",
    "stuarting…",
    "kevin mode…",
    "bob mode…",
    "illumination…",
    "gru says wait…",
    "minionize…",
    "honk honk…",
    "bi-do…",
    "me want banana…",
    "para tú…",
)

_ARG_VALUE_TRUNCATE_AT = 300


def _thinking_label() -> str:
    return f"[dim]{random.choice(_THINKING_LABELS)}[/dim]"


class CliRenderer:
    """Consume events for one turn; draw to a :class:`Console`.

    Usage::

        renderer = CliRenderer(console)
        await renderer.consume(bus)   # returns when terminal event arrives
        renderer.print_final()         # prints final output or error
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.final_output: str | None = None
        self.error: str | None = None

    async def consume(self, bus: EventBus) -> None:
        """Render events until a terminal event arrives, then return."""
        with self.console.status(_thinking_label(), spinner="dots") as status:
            async for event in bus.stream():
                if isinstance(event, ApprovalRequest):
                    status.stop()
                    response = await self._prompt_approval(event)
                    event.response_future.set_result(response)
                    status.start()
                    continue
                self._handle(event, status)
                if is_terminal(event):
                    return

    def _handle(self, event: JacEvent, status: Any) -> None:
        if isinstance(event, ModelRequestStarted):
            status.update(_thinking_label())
        elif isinstance(event, ModelRequestCompleted):
            # No status change — wait for the next ModelRequestStarted or a tool call.
            pass
        elif isinstance(event, ToolCallStarted):
            label = event.tool_name
            if event.reason:
                label = f"{label} — {event.reason}"
            status.update(f"[dim]→ {label}[/dim]")
        elif isinstance(event, ToolCallCompleted):
            status.update(_thinking_label())
        elif isinstance(event, ToolCallFailed):
            # Print errors inline; the agent loop may recover.
            self.console.print(
                f"[yellow]tool {event.tool_name} failed:[/yellow] {event.error}"
            )
        elif isinstance(event, RunCompleted):
            self.final_output = event.output
        elif isinstance(event, RunFailed):
            self.error = event.error

    async def _prompt_approval(self, event: ApprovalRequest) -> ApprovalResponse:
        """Render the approval panel and ask the user."""
        body: list[str] = [f"[bold]{event.tool_name}[/bold]"]
        if event.reason:
            body.append(f"[dim]reason:[/dim] {event.reason}")
        for key, value in event.args.items():
            if key == "reason":
                continue
            value_str = str(value)
            if len(value_str) > _ARG_VALUE_TRUNCATE_AT:
                value_str = value_str[: _ARG_VALUE_TRUNCATE_AT - 1] + "…"
            body.append(f"[dim]{key}:[/dim] {value_str}")

        self.console.print()
        self.console.print(
            Panel("\n".join(body), title="approval needed", border_style="yellow")
        )
        approved: bool = await asyncio.to_thread(
            Confirm.ask, "Approve?", default=False, console=self.console
        )
        return ApprovalResponse(approved=approved)

    def print_final(self) -> None:
        """Render the final turn output (or error) after :meth:`consume` returns."""
        if self.final_output is not None:
            self.console.print(Markdown(self.final_output))
        if self.error is not None:
            self.console.print(f"[red]error:[/red] {self.error}")
