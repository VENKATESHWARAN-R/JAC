"""Async event bus for JAC runtime events.

A thin wrapper around :class:`asyncio.Queue` that delivers
:mod:`jac.runtime.events` instances from emitters (Pydantic AI hooks) to
consumers (the CLI renderer; later, TUI / web surfaces).

The bus is **session-long**: one bus per REPL session, reused across every
turn. A terminal event (:class:`RunCompleted` / :class:`RunFailed`)
signals the end of a turn; consumers should stop iterating then and wait
for the next turn to push fresh events.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .events import JacEventT


class EventBus:
    """Single-producer / single-consumer event channel.

    Phase 1 assumption: only one renderer at a time. Multi-renderer support
    (fan-out) is straightforward to add later; the public API would not change.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[JacEventT] = asyncio.Queue()

    async def emit(self, event: JacEventT) -> None:
        """Put ``event`` onto the queue. Never blocks meaningfully."""
        await self._queue.put(event)

    async def stream(self) -> AsyncIterator[JacEventT]:
        """Yield events as they arrive. Consumer decides when to stop."""
        while True:
            event = await self._queue.get()
            yield event
