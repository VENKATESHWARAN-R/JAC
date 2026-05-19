"""JAC runtime event types.

Events flow:

    Pydantic AI hooks (jac.capabilities.hooks)
        → EventBus (jac.runtime.bus)
            → CLI renderer / other surfaces (jac.cli.renderer)

This is the architectural inversion: the CLI does not poll the agent. It
consumes events the runtime emits. Adding a new surface (TUI, web) means
adding a new consumer of :class:`jac.runtime.bus.EventBus` — nothing in the
runtime changes.

Per-turn boundaries: every turn ends with a terminal event,
:class:`RunCompleted` or :class:`RunFailed`. Consumers should treat the
arrival of either as "stop reading until the next turn starts."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class JacEvent:
    """Base class. Don't instantiate directly; use one of the subclasses."""


@dataclass(frozen=True, slots=True)
class ModelRequestStarted(JacEvent):
    """Gru is sending a request to the model."""


@dataclass(frozen=True, slots=True)
class ModelRequestCompleted(JacEvent):
    """The model has responded."""


@dataclass(frozen=True, slots=True)
class ToolCallStarted(JacEvent):
    """A tool call is about to execute (validated args, pre-run)."""

    tool_name: str
    reason: str | None
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallCompleted(JacEvent):
    """A tool call returned successfully."""

    tool_name: str
    result_preview: str


@dataclass(frozen=True, slots=True)
class ToolCallFailed(JacEvent):
    """A tool call raised. The agent loop may still recover."""

    tool_name: str
    error: str


@dataclass(frozen=True, slots=True)
class RunCompleted(JacEvent):
    """Terminal: ``agent.run()`` completed normally. Carries the final output."""

    output: str


@dataclass(frozen=True, slots=True)
class RunFailed(JacEvent):
    """Terminal: ``agent.run()`` raised. The error is rendered to the user."""

    error: str


type JacEventT = (
    ModelRequestStarted
    | ModelRequestCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | ToolCallFailed
    | RunCompleted
    | RunFailed
)
"""Discriminated union of all event types. Use ``isinstance`` to dispatch."""


def is_terminal(event: JacEvent) -> bool:
    """True if ``event`` ends the current turn."""
    return isinstance(event, (RunCompleted, RunFailed))
