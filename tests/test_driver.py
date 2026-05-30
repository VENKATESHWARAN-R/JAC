"""Tests for the surface-agnostic :class:`SessionDriver` (R5 / R17).

The point of the driver is that a full turn can be driven *without* a renderer
— only the bus is consumed. These tests do exactly that: they build a hermetic
``gru`` (a ``FunctionModel``), run turns through the driver, and assert on the
returned history + the events that landed on the bus.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jac.runtime.driver import (
    SessionDriver,
    _close_open_tool_calls,
    _recover_failed_history,
)
from jac.runtime.events import (
    BudgetHardStop,
    CompactionRefused,
    EventBus,
    RunCompleted,
    RunFailed,
    TextDelta,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _drain(bus: EventBus) -> list[Any]:
    out: list[Any] = []
    while not bus._queue.empty():  # type: ignore[attr-defined]
        out.append(bus._queue.get_nowait())  # type: ignore[attr-defined]
    return out


def _text_agent(reply: str) -> Agent:
    def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=reply)])

    return Agent(FunctionModel(model_fn))


def _stream_agent(chunks: list[str]) -> Agent:
    async def stream_fn(messages: list[ModelMessage], info: Any):
        for chunk in chunks:
            yield chunk

    return Agent(FunctionModel(stream_function=stream_fn))


# ---------- run_turn ----------


async def test_run_turn_returns_history_and_emits_run_completed() -> None:
    bus = EventBus()
    driver = SessionDriver(gru=_text_agent("the answer"), bus=bus)
    result = await driver.run_turn("do the thing", [])
    assert not result.failed
    assert result.output == "the answer"
    # History is non-empty and resumable (user prompt + model response).
    assert len(result.message_history) >= 2
    events = _drain(bus)
    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].output == "the answer"


async def test_run_turn_failure_recovers_history_and_emits_run_failed() -> None:
    async def boom(self: Any, *a: Any, **k: Any) -> Any:
        raise RuntimeError("model unreachable")

    bus = EventBus()
    driver = SessionDriver(gru=_text_agent("unused"), bus=bus)
    # Patch the agent's run to blow up.
    import pydantic_ai

    orig = pydantic_ai.Agent.run
    pydantic_ai.Agent.run = boom  # type: ignore[method-assign]
    try:
        prior = [ModelRequest(parts=[TextPart(content="earlier")])]
        result = await driver.run_turn("new prompt", prior)
    finally:
        pydantic_ai.Agent.run = orig  # type: ignore[method-assign]

    assert result.failed
    # The user's prompt survives so the next turn keeps context.
    flat = "".join(
        getattr(p, "content", "")
        for m in result.message_history
        for p in getattr(m, "parts", [])
        if isinstance(getattr(p, "content", ""), str)
    )
    assert "new prompt" in flat
    events = _drain(bus)
    assert any(isinstance(e, RunFailed) for e in events)
    assert not any(isinstance(e, RunCompleted) for e in events)


async def test_run_turn_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, int] = {}

    class _Tracker:
        async def record(self, **kw: int) -> None:
            recorded.update(kw)

    bus = EventBus()
    driver = SessionDriver(gru=_text_agent("hi"), bus=bus, usage_tracker=_Tracker())  # type: ignore[arg-type]
    await driver.run_turn("x", [])
    assert "input_tokens" in recorded
    assert "output_tokens" in recorded


async def test_run_turn_streaming_emits_text_deltas() -> None:
    bus = EventBus()
    driver = SessionDriver(gru=_stream_agent(["Hello ", "world"]), bus=bus)
    result = await driver.run_turn("hi", [], stream=True)
    assert not result.failed
    events = _drain(bus)
    deltas = [e for e in events if isinstance(e, TextDelta)]
    # At least one delta, and concatenated they reconstruct the output.
    assert deltas
    assert "".join(d.content for d in deltas) == result.output
    assert any(isinstance(e, RunCompleted) for e in events)


# ---------- budget pre-flight ----------


async def test_check_token_budget_none_when_no_tracker() -> None:
    driver = SessionDriver(gru=_text_agent("x"), bus=EventBus())
    assert await driver.check_token_budget() is None


async def test_check_token_budget_refuses_and_carries_action() -> None:
    class _Tracker:
        def is_over_hardstop(self) -> tuple[str, int, int] | None:
            return ("session_total", 210_000, 200_000)

    bus = EventBus()
    driver = SessionDriver(gru=_text_agent("x"), bus=bus, usage_tracker=_Tracker())  # type: ignore[arg-type]
    event = await driver.check_token_budget()
    assert isinstance(event, BudgetHardStop)
    assert event.used == 210_000
    assert event.suggested_action  # R5c: non-CLI surfaces get the guidance
    assert any(isinstance(e, BudgetHardStop) for e in _drain(bus))


async def test_check_context_budget_refuses_over_pct(monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.runtime import driver as driver_mod

    bus = EventBus()
    driver = SessionDriver(gru=_text_agent("x"), bus=bus)
    # Force a tiny budget so any history projects over the refuse pct.
    monkeypatch.setattr(driver_mod, "resolve_context_budget", lambda: 10)
    huge = [ModelRequest(parts=[TextPart(content="word " * 500)])]
    event = await driver.check_context_budget(huge, "another prompt")
    assert isinstance(event, CompactionRefused)
    assert event.suggested_action
    assert any(isinstance(e, CompactionRefused) for e in _drain(bus))


async def test_check_context_budget_ok_under_pct() -> None:
    driver = SessionDriver(gru=_text_agent("x"), bus=EventBus())
    assert await driver.check_context_budget([], "tiny") is None


# ---------- history recovery helpers ----------


def test_close_open_tool_calls_appends_synthetic_returns() -> None:
    msgs: list[ModelMessage] = [
        ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={}, tool_call_id="c1")]),
    ]
    out = _close_open_tool_calls(msgs)
    # A synthetic return for the dangling call was appended.
    assert len(out) == 2
    last_parts = out[-1].parts
    assert any(getattr(p, "tool_call_id", None) == "c1" for p in last_parts)


def test_close_open_tool_calls_noop_when_all_answered() -> None:
    msgs: list[ModelMessage] = [ModelResponse(parts=[TextPart(content="done")])]
    assert _close_open_tool_calls(msgs) == msgs


def test_sdk_facade_reexports_the_documented_surface() -> None:
    # R5d: the jac.sdk facade is the supported embedding entry point.
    import jac.sdk as sdk

    for name in ("build_gru", "SessionDriver", "TurnResult", "EventBus", "make_approval_handler"):
        assert name in sdk.__all__
        assert getattr(sdk, name) is not None
    # SessionDriver re-exported via the facade is the same class.
    from jac.runtime.driver import SessionDriver as RealDriver

    assert sdk.SessionDriver is RealDriver


def test_recover_failed_history_synthesizes_when_nothing_captured() -> None:
    prior: list[ModelMessage] = [ModelRequest(parts=[TextPart(content="earlier")])]
    out = _recover_failed_history(prior, [], "my prompt")
    flat = "".join(
        p.content for m in out for p in getattr(m, "parts", []) if isinstance(p, TextPart)
    )
    # Falls back to TextPart for the user prompt isn't guaranteed; check the
    # UserPromptPart survives by scanning content broadly.
    joined = "".join(str(getattr(p, "content", "")) for m in out for p in getattr(m, "parts", []))
    assert "my prompt" in joined
    assert "failed before it could complete" in flat
