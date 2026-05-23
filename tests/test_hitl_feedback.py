"""Tests for Phase 1.7.d — approval & clarify accept in-band feedback (D26).

Two channels are covered:

- the approval handler turns ``ApprovalResponse(approved=False, feedback=...)``
  into a ``ToolDenied`` whose message embeds the feedback as a labeled
  ``user_feedback`` field — Gru reads that field as the redirection.
- the clarify capability returns the user's free-form answer verbatim when
  the response is marked ``free_text=True``.

We test the runtime pieces directly — the rich/console prompt flow in the
renderer is exercised by hand and stays out of the unit tests (no
console-IO coverage exists for the pre-D26 prompts either).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any, cast

from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolApproved,
    ToolDenied,
)

from jac.capabilities.approval import _deny_message, make_approval_handler
from jac.capabilities.clarify import make_clarify_capability
from jac.runtime.bus import EventBus
from jac.runtime.events import (
    ApprovalRequest,
    ApprovalResponse,
    ClarifyRequest,
    ClarifyResponse,
)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ---------- _deny_message helper ----------


def test_deny_message_plain_denial() -> None:
    msg = _deny_message(ApprovalResponse(approved=False))
    assert msg == "The user declined this tool call."


def test_deny_message_honors_explicit_deny_message() -> None:
    msg = _deny_message(ApprovalResponse(approved=False, deny_message="custom denial"))
    assert msg == "custom denial"


def test_deny_message_embeds_feedback() -> None:
    msg = _deny_message(
        ApprovalResponse(approved=False, feedback="edit the test file instead")
    )
    assert "user_feedback:" in msg
    assert '"edit the test file instead"' in msg
    # The hint to not retry is part of the contract Gru reads.
    assert "do not retry" in msg.lower()


def test_deny_message_feedback_beats_deny_message() -> None:
    """When both fields are set, feedback wins — it's the richer signal."""
    msg = _deny_message(
        ApprovalResponse(
            approved=False, deny_message="ignored", feedback="do this instead"
        )
    )
    assert "do this instead" in msg
    assert "ignored" not in msg


# ---------- approval handler integration ----------


def _approval_call(call_id: str, tool_name: str = "write_file") -> ToolCallPart:
    return ToolCallPart(
        tool_name=tool_name,
        args={"reason": "to confirm the path"},
        tool_call_id=call_id,
    )


async def _run_handler(
    requests: DeferredToolRequests,
    response: ApprovalResponse,
) -> DeferredToolResults | None:
    """Drive the approval handler with a prepared response.

    Mirrors the renderer's role (drain ``ApprovalRequest``, resolve the
    future) without the rich/console plumbing.
    """
    bus = EventBus()
    handler_cap = make_approval_handler(bus)

    async def responder() -> None:
        async for event in bus.stream():
            if isinstance(event, ApprovalRequest):
                event.response_future.set_result(response)
                return

    responder_task = asyncio.create_task(responder())
    try:
        ctx = cast(RunContext[Any], None)
        # The handler is async in our build; cast away pydantic-ai's
        # sync-or-async union return type.
        coro = cast(Coroutine[Any, Any, DeferredToolResults | None], handler_cap.handler(ctx, requests))
        return await coro
    finally:
        responder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await responder_task


def test_handler_denies_with_feedback() -> None:
    call = _approval_call("call-1")
    requests = DeferredToolRequests(approvals=[call])
    results = _run(
        _run_handler(
            requests,
            ApprovalResponse(
                approved=False, feedback="touch the test file, not the source"
            ),
        )
    )
    assert results is not None
    entry = results.approvals["call-1"]
    assert isinstance(entry, ToolDenied)
    assert "user_feedback:" in entry.message
    assert '"touch the test file, not the source"' in entry.message


def test_handler_plain_deny_preserves_default_message() -> None:
    call = _approval_call("call-2")
    requests = DeferredToolRequests(approvals=[call])
    results = _run(_run_handler(requests, ApprovalResponse(approved=False)))
    assert results is not None
    entry = results.approvals["call-2"]
    assert isinstance(entry, ToolDenied)
    assert entry.message == "The user declined this tool call."
    assert "user_feedback" not in entry.message


def test_handler_approves() -> None:
    """Approval path is unchanged by D26 — covered to keep the channel honest."""
    call = _approval_call("call-3")
    requests = DeferredToolRequests(approvals=[call])
    results = _run(_run_handler(requests, ApprovalResponse(approved=True)))
    assert results is not None
    entry = results.approvals["call-3"]
    assert entry is True or isinstance(entry, ToolApproved)


# ---------- clarify free-text path ----------


def _clarify_tool(bus: EventBus) -> Any:
    cap = make_clarify_capability(bus)
    return cap._build_tools()[0]


async def _clarify_with(response: ClarifyResponse) -> Any:
    """Drive one clarify call with a prepared response."""
    bus = EventBus()
    clarify = _clarify_tool(bus)

    async def responder() -> None:
        async for event in bus.stream():
            if isinstance(event, ClarifyRequest):
                event.response_future.set_result(response)
                return

    responder_task = asyncio.create_task(responder())
    try:
        return await clarify(
            reason="picking how to refactor auth",
            question="Where should the validation live?",
            options=["in the model", "in the route handler"],
        )
    finally:
        responder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await responder_task


def test_clarify_returns_free_text_verbatim() -> None:
    """When the renderer marks ``free_text=True``, the user's typed answer is
    what the tool returns to Gru — it is NOT validated against the offered
    options."""
    result = _run(
        _clarify_with(
            ClarifyResponse(
                selected_index=None,
                selected_text="actually let's split this across two files",
                cancelled=False,
                free_text=True,
            )
        )
    )
    assert result == "actually let's split this across two files"


def test_clarify_returns_picked_option_when_not_free_text() -> None:
    """Regression: the normal numbered-pick path still returns the option text."""
    result = _run(
        _clarify_with(
            ClarifyResponse(
                selected_index=2,
                selected_text="in the route handler",
                cancelled=False,
            )
        )
    )
    assert result == "in the route handler"


def test_clarify_cancel_raises() -> None:
    """Regression: cancellation still raises so the agent picks a different
    approach instead of looping."""
    try:
        _run(
            _clarify_with(
                ClarifyResponse(
                    selected_index=None, selected_text=None, cancelled=True
                )
            )
        )
    except RuntimeError as exc:
        assert "cancelled the clarify prompt" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("clarify did not raise on cancellation")
