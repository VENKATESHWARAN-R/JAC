"""``jac.sdk`` — the documented entry point for embedding JAC (R5d).

JAC's turn pipeline is surface-agnostic: the CLI is just one consumer of the
same primitives. This module is the thin, stable facade an external embedder
(a browser backend, a notebook, another service) imports instead of reaching
into ``jac.runtime.*`` internals.

Minimal embedding sketch::

    from jac.sdk import build_gru, EventBus, SessionDriver, make_approval_handler

    bus = EventBus()
    gru = build_gru(model_override="anthropic:claude-sonnet-4-6", bus=bus)
    driver = SessionDriver(gru=gru, bus=bus)

    history: list = []
    # Run a turn; consume `bus` concurrently to render events as they arrive.
    result = await driver.run_turn("hello", history)
    history = result.message_history

The driver only *emits* lifecycle events onto the bus and returns data —
rendering is entirely the caller's job (the CLI's ``CliRenderer`` is one
implementation). Pass ``stream=True`` to ``run_turn`` for token-level
:class:`TextDelta` events.

Stability note: this re-export surface is the supported API. The underlying
modules may move; this facade is what we keep backwards-compatible.
"""

from __future__ import annotations

from jac.runtime.approval import make_approval_handler
from jac.runtime.driver import SessionDriver, TurnResult
from jac.runtime.events import EventBus, is_terminal
from jac.runtime.gru import build_gru
from jac.runtime.hooks import make_hooks
from jac.runtime.usage import make_usage_tracker

__all__ = [
    "EventBus",
    "SessionDriver",
    "TurnResult",
    "build_gru",
    "is_terminal",
    "make_approval_handler",
    "make_hooks",
    "make_usage_tracker",
]
