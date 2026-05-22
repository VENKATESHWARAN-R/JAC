"""Message-history window management.

A thin :class:`ProcessHistory` capability that keeps the last *N*
user-prompt exchanges in context. "Exchange" boundaries are detected by
looking for :class:`UserPromptPart` parts on incoming :class:`ModelRequest`
messages — slicing on those boundaries guarantees we never cut between a
tool call and its matching tool-return, which would otherwise make the
model error out.

For short sessions this is a no-op. For long ones it stops context from
growing without bound. Default cap is 40 exchanges — a lot for most JAC
sessions; tune via :func:`make_history_capability`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

_DEFAULT_MAX_EXCHANGES = 40


def _has_user_prompt(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(isinstance(part, UserPromptPart) for part in message.parts)


def _slice_by_exchanges(messages: Sequence[ModelMessage], max_exchanges: int) -> list[ModelMessage]:
    user_indices = [i for i, m in enumerate(messages) if _has_user_prompt(m)]
    if len(user_indices) <= max_exchanges:
        return list(messages)
    keep_from = user_indices[-max_exchanges]
    return list(messages[keep_from:])


def make_history_capability(
    max_exchanges: int = _DEFAULT_MAX_EXCHANGES,
) -> ProcessHistory:
    """Build a ``ProcessHistory`` that keeps the last ``max_exchanges`` user turns."""

    async def process(messages: list[ModelMessage]) -> list[ModelMessage]:
        return _slice_by_exchanges(messages, max_exchanges)

    return ProcessHistory(process)
