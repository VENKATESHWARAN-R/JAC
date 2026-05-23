"""HITL approval flow — bus-driven ``HandleDeferredToolCalls`` capability.

When a tool guarded by :class:`ApprovalRequiredToolset` is called, Pydantic AI
collects the call as a deferred request and hands it to any registered
``HandleDeferredToolCalls`` capability. We use that hook to:

1. Emit an :class:`ApprovalRequest` event onto the bus, one per pending call.
2. Await an :class:`ApprovalResponse` on a per-request future.
3. Build a :class:`DeferredToolResults` with the user's choices.

The renderer is the other end of the channel: it consumes the
:class:`ApprovalRequest` events, prompts the user, and resolves the future.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic_ai.capabilities import HandleDeferredToolCalls
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolApproved,
    ToolDenied,
)

from jac.runtime.bus import EventBus
from jac.runtime.events import ApprovalRequest, ApprovalResponse


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Normalize ``ToolCallPart.args`` (which may be ``str`` or ``dict``) to a dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    return {}


def _deny_message(response: ApprovalResponse) -> str:
    """Build the tool-result message the model sees on a denial.

    When the user redirected with feedback (D26), the message embeds the
    feedback as a labeled ``user_feedback`` field so the model treats it
    as a structured signal and adapts rather than retrying.
    """
    if response.feedback:
        return (
            "The user declined this tool call and provided feedback. "
            f'user_feedback: "{response.feedback}". '
            "Use this feedback to adjust your approach; do not retry the same call."
        )
    return response.deny_message or "The user declined this tool call."


def make_approval_handler(bus: EventBus) -> HandleDeferredToolCalls[Any]:
    """Build a ``HandleDeferredToolCalls`` capability that asks the user via ``bus``."""

    async def handler(
        ctx: RunContext[Any],
        requests: DeferredToolRequests,
    ) -> DeferredToolResults | None:
        # External-execution calls aren't ours; let the loop bubble them up.
        if not requests.approvals:
            return None

        approvals: dict[str, bool | ToolApproved | ToolDenied] = {}
        for call in requests.approvals:
            args = _coerce_args(call.args)
            reason = args.get("reason")
            future: asyncio.Future[ApprovalResponse] = asyncio.get_running_loop().create_future()
            await bus.emit(
                ApprovalRequest(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    reason=reason if isinstance(reason, str) else None,
                    args=args,
                    response_future=future,
                )
            )
            response = await future
            if response.approved:
                approvals[call.tool_call_id] = True
            else:
                approvals[call.tool_call_id] = ToolDenied(message=_deny_message(response))

        return requests.build_results(approvals=approvals)

    return HandleDeferredToolCalls(handler=handler)
