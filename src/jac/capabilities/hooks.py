"""Pydantic AI hooks that emit JAC events onto an :class:`EventBus`.

Installing this capability is how the CLI (or any other surface) learns
what the agent is doing — model requests starting/finishing, tool calls
firing, errors. See docs/architecture.md §7.

Phase 1 step 1 wires model-request and tool-call hooks. Streaming text
deltas and approval surfacing land alongside tools in subsequent steps.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.capabilities import Hooks

from jac.runtime.bus import EventBus
from jac.runtime.events import (
    ModelRequestCompleted,
    ModelRequestStarted,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)

_RESULT_PREVIEW_MAX = 200


def make_hooks(bus: EventBus) -> Hooks:
    """Build a :class:`Hooks` capability that emits events onto ``bus``."""
    hooks = Hooks()

    @hooks.on.before_model_request
    async def _before_model(ctx: Any, request_context: Any) -> Any:
        await bus.emit(ModelRequestStarted())
        return request_context

    @hooks.on.after_model_request
    async def _after_model(ctx: Any, *, request_context: Any, response: Any) -> Any:
        await bus.emit(ModelRequestCompleted())
        return response

    @hooks.on.before_tool_execute
    async def _before_tool(
        ctx: Any,
        *,
        call: Any,
        tool_def: Any,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        reason = args.get("reason") if isinstance(args, dict) else None
        await bus.emit(
            ToolCallStarted(
                tool_name=call.tool_name,
                reason=reason if isinstance(reason, str) else None,
                args=dict(args) if isinstance(args, dict) else {},
            )
        )
        return args

    @hooks.on.after_tool_execute
    async def _after_tool(
        ctx: Any,
        *,
        call: Any,
        tool_def: Any,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        preview = str(result)
        if len(preview) > _RESULT_PREVIEW_MAX:
            preview = preview[: _RESULT_PREVIEW_MAX - 3] + "..."
        await bus.emit(ToolCallCompleted(tool_name=call.tool_name, result_preview=preview))
        return result

    @hooks.on.tool_execute_error
    async def _tool_error(
        ctx: Any,
        *,
        call: Any,
        tool_def: Any,
        args: dict[str, Any],
        error: BaseException,
    ) -> Any:
        await bus.emit(ToolCallFailed(tool_name=call.tool_name, error=str(error)))
        # Re-raise so pydantic-ai's normal retry/abort logic runs.
        raise error

    return hooks
