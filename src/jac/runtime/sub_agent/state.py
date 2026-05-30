"""Session-scoped sub-agent state and the capability factory handle.

This module holds the bits the spawn tools reach through module-level
setters (so the runtime stays decoupled from the REPL construction site):

- :class:`SubAgentCapability` + ``set/get_sub_agent_capability`` — the
  factory that builds a worker's capability list, installed by the REPL.
- the event-bus indirection (``set_sub_agent_event_bus`` / ``_emit_*``) —
  the REPL installs a bus; the runtime emits ``SubAgent*`` lifecycle events.
- ``_current_agent_label`` + :func:`get_current_agent_label` — the contextvar
  the approval handler reads to stamp ``minion-N`` onto HITL prompts.
- the ``minion-N`` spawn counter.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from jac.profiles import Profile
from jac.runtime.events import EventBus, JacEventT


@dataclass
class SubAgentCapability:
    """Holds the bits needed to build a sub-agent Agent on demand.

    Not a Pydantic AI ``AbstractCapability`` — it isn't registered on
    the main agent's capability list. It's a factory the
    ``spawn_sub_agent`` tool reaches through a module-level setter.
    """

    profile: Profile
    """Active profile, source of tier → model mapping."""

    base_prompt: str
    """The shipped ``sub_agent_system.md`` body, loaded once at setup."""

    capability_factory: Any
    """Callable returning the list of capabilities a sub-agent gets.
    Default = main agent's capabilities minus the spawn tool itself.
    Provided by the REPL at setup so we don't import upward."""


# Module-level singleton — set once at REPL session start by
# ``set_sub_agent_capability``. Mirrors the pattern used by
# ``set_summarizer_model`` in ``tool_summarize`` — keeps the tool
# implementation decoupled from the construction site.
_capability: SubAgentCapability | None = None


def set_sub_agent_capability(cap: SubAgentCapability | None) -> None:
    """Install the active sub-agent factory. ``None`` disables spawning."""
    global _capability
    _capability = cap


def get_sub_agent_capability() -> SubAgentCapability | None:
    """Return the active sub-agent factory, or ``None`` if disabled."""
    return _capability


# --- event bus indirection (renderer hooks) ---
#
# The bus belongs to the REPL session; the runtime can't import upward
# to grab it without a cycle. Same pattern as ``set_sub_agent_usage_recorder``:
# REPL installs at session start, runtime emits through this hook.
_event_bus: EventBus | None = None


def set_sub_agent_event_bus(bus: EventBus | None) -> None:
    """Install (or clear) the bus used to emit ``SubAgent*`` lifecycle
    events for the renderer. ``None`` silences emission — useful in
    headless / test contexts where no consumer is wired."""
    global _event_bus
    _event_bus = bus


async def _emit_sub_agent_event(event: JacEventT) -> None:
    """Fire-and-forget emission. Silent when no bus is installed."""
    if _event_bus is not None:
        await _event_bus.emit(event)


# Human-readable label for whoever is currently asking for approval.
# Defaults to ``"Gru"`` (the main agent). Set to the spawn_id during a
# sub-agent's ``Agent.run()`` so the approval handler — shared between
# main and sub-agents — can stamp the right name onto the HITL prompt.
# Per-task copy (asyncio.Task contextvar semantics), so parallel spawns
# each see their own label.
_current_agent_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_agent_label", default="Gru"
)


def get_current_agent_label() -> str:
    """Return the label of the agent currently executing — ``"Gru"`` on
    the main loop, ``"minion-N"`` inside a sub-agent's run. Read by
    :func:`jac.runtime.approval.make_approval_handler` to stamp the
    label onto every emitted :class:`ApprovalRequest`."""
    return _current_agent_label.get()


# Session-scoped monotonic counter. ``minion-1``, ``minion-2``, … give the
# user a stable label they can correlate across the approval panel, the
# spawn lifecycle events, ``/spawns``, and the question/answer panels. The
# label leans into JAC's Gru-and-minions theme; "sub-agent", "minion", and
# "worker" all refer to the same thing in this codebase (the prompt teaches
# Gru this so casual user phrasing routes correctly). Resets when the
# session ends (via ``_reset_pending_spawns``) so each new session starts at
# ``minion-1``.
_spawn_counter: int = 0


def _mint_spawn_id() -> str:
    """Return the next ``minion-N`` ID and bump the counter. Not thread-safe;
    the REPL runs single-threaded and concurrent spawn calls inside one
    ``asyncio.gather`` still serialise through the event loop."""
    global _spawn_counter
    _spawn_counter += 1
    return f"minion-{_spawn_counter}"


def _reset_spawn_counter() -> None:
    """Reset the spawn counter. Called by ``_reset_pending_spawns`` so
    /exit + REPL teardown + per-test cleanup all reset together."""
    global _spawn_counter
    _spawn_counter = 0
