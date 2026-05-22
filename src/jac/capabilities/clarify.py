"""Structured user-prompt capability — ``clarify``.

Gru currently asks ambiguous routing questions in free-form prose ("should
I use A or B?") and the user replies as a sentence the model then has to
parse. That's lossy. ``clarify`` makes the picker explicit: Gru declares
the question and the discrete options, the renderer shows a numbered
prompt, the user picks one, and the tool returns the chosen string.

Mechanism mirrors the approval flow (see :mod:`jac.capabilities.approval`):

1. The tool creates a per-call ``asyncio.Future``.
2. It emits a :class:`ClarifyRequest` onto the bus, carrying the future.
3. The renderer prompts the user via ``rich`` and resolves the future with
   a :class:`ClarifyResponse`.
4. The tool awaits the future and returns the chosen option text (or
   raises if the user cancels).

The tool is **not approval-gated**: asking the user is its purpose, and
layering HITL on top would mean two prompts for one question. The
*answer* is the side effect that's already user-visible.

Same factory pattern as plan + process capabilities:
``make_clarify_capability(bus)`` with a required bus — without one the
tool would silently block forever.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.runtime.bus import EventBus
from jac.runtime.events import ClarifyRequest, ClarifyResponse
from jac.tools import jac_function_toolset, jac_tool

_MIN_OPTIONS = 2
_MAX_OPTIONS = 8
_MAX_QUESTION_CHARS = 500
_MAX_OPTION_CHARS = 200


@dataclass
class ClarifyCapability(AbstractCapability[Any]):
    """Toolset exposing :func:`clarify`. Requires a bus to function."""

    bus: EventBus

    def get_toolset(self) -> Any:
        return jac_function_toolset(*self._build_tools())

    def _build_tools(self) -> list[Any]:
        bus = self.bus

        @jac_tool
        async def clarify(reason: str, question: str, options: list[str]) -> str:
            """Ask the user to pick exactly one of ``options``.

            Use when you genuinely need a decision the user is best
            placed to make — choosing between two architectures, picking
            which file to edit when several look right, agreeing on a
            convention. Don't use it as a polite alternative to "I'll
            just do X" — the prompt interrupts the user's flow, so make
            each clarify count.

            The renderer shows the question and a numbered list. The
            user selects one and the tool returns the chosen option's
            text verbatim.

            Args:
                reason: One-sentence justification ("about to refactor
                    the auth flow; the two viable shapes have different
                    failure modes for the user").
                question: The question, as one or two sentences.
                options: 2-8 distinct, mutually exclusive choices. Each
                    a short imperative phrase; ≤200 chars.

            Returns:
                The selected option text, verbatim.

            Raises:
                RuntimeError: if the user cancels the prompt (Ctrl-C /
                    empty input). Treat this as "the user declined to
                    choose" and pick a different approach.
            """
            q = question.strip()
            if not q:
                raise ValueError("`question` must not be empty.")
            if len(q) > _MAX_QUESTION_CHARS:
                raise ValueError(
                    f"`question` exceeds {_MAX_QUESTION_CHARS} chars; "
                    "shorten it or split into multiple clarify calls."
                )
            if not _MIN_OPTIONS <= len(options) <= _MAX_OPTIONS:
                raise ValueError(
                    f"`options` must contain between {_MIN_OPTIONS} and "
                    f"{_MAX_OPTIONS} entries; got {len(options)}."
                )
            cleaned: list[str] = []
            seen: set[str] = set()
            for i, raw in enumerate(options, start=1):
                if not isinstance(raw, str):
                    raise ValueError(f"option #{i} must be a string; got {type(raw).__name__}.")
                opt = raw.strip()
                if not opt:
                    raise ValueError(f"option #{i} is empty.")
                if len(opt) > _MAX_OPTION_CHARS:
                    raise ValueError(f"option #{i} exceeds {_MAX_OPTION_CHARS} chars.")
                key = opt.lower()
                if key in seen:
                    raise ValueError(
                        f"option #{i} duplicates an earlier choice "
                        f"({opt!r}); each option must be distinct."
                    )
                seen.add(key)
                cleaned.append(opt)

            future: asyncio.Future[ClarifyResponse] = asyncio.get_running_loop().create_future()
            await bus.emit(
                ClarifyRequest(
                    question=q,
                    options=tuple(cleaned),
                    response_future=future,
                )
            )
            response = await future
            if response.cancelled or response.selected_text is None:
                raise RuntimeError(
                    "The user cancelled the clarify prompt without "
                    "selecting an option. Don't retry the same prompt; "
                    "pick a different approach or ask the user openly."
                )
            return response.selected_text

        return [clarify]


def make_clarify_capability(bus: EventBus) -> ClarifyCapability:
    """Build a fresh :class:`ClarifyCapability`. Requires a bus."""
    return ClarifyCapability(bus=bus)
