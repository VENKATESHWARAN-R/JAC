"""Tests for failed-turn history recovery (jac.cli.repl helpers).

A hard failure mid-turn (tool retries exhausted, MCP server disconnect, model
error) used to discard the turn and wipe the conversation. These cover the
recovery path that keeps the history resumable instead.
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jac.runtime.driver import _close_open_tool_calls, _recover_failed_history


def _dangling() -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content="do the thing")]),
        ModelResponse(parts=[ToolCallPart(tool_name="boom", args={}, tool_call_id="t1")]),
    ]


def test_close_open_tool_calls_appends_return() -> None:
    fixed = _close_open_tool_calls(_dangling())
    returns = [p for m in fixed for p in m.parts if isinstance(p, ToolReturnPart)]
    assert len(returns) == 1
    assert returns[0].tool_call_id == "t1"
    assert "aborted" in str(returns[0].content)


def test_close_open_tool_calls_noop_when_balanced() -> None:
    balanced = [
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="a")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="t", content="ok", tool_call_id="a")]),
    ]
    assert _close_open_tool_calls(balanced) == balanced


def test_recover_prefers_captured_sanitized() -> None:
    recovered = _recover_failed_history(original=[], captured=_dangling(), text="do the thing")
    # user prompt preserved + dangling call closed
    assert any(
        isinstance(p, UserPromptPart) and "do the thing" in str(p.content)
        for m in recovered
        for p in m.parts
    )
    assert any(isinstance(p, ToolReturnPart) for m in recovered for p in m.parts)


def test_recover_synthesizes_user_turn_when_nothing_captured() -> None:
    prior = [ModelRequest(parts=[UserPromptPart(content="earlier")])]
    recovered = _recover_failed_history(original=prior, captured=[], text="lost message")
    assert recovered[0] is prior[0]  # prior context kept
    assert any(
        isinstance(p, UserPromptPart) and "lost message" in str(p.content)
        for m in recovered
        for p in m.parts
    )
    assert any(isinstance(p, TextPart) for m in recovered for p in m.parts)


def test_recovered_history_is_resumable() -> None:
    """The whole point: a sanitized crashed history can resume a new run."""
    recovered = _recover_failed_history(original=[], captured=_dangling(), text="do the thing")

    def _answer(messages: list, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("recovered")])

    agent = Agent(FunctionModel(_answer))
    result = asyncio.run(agent.run("continue?", message_history=recovered))
    assert result.output == "recovered"
