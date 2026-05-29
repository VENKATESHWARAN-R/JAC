"""Tests for interaction modes (D23): normal / plan / accept-edits."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jac.runtime import modes


@pytest.fixture(autouse=True)
def _reset_mode() -> Iterator[None]:
    modes.reset_mode()
    yield
    modes.reset_mode()


def test_default_mode_is_normal() -> None:
    assert modes.get_mode() == "normal"


def test_set_and_get_mode() -> None:
    modes.set_mode("plan")
    assert modes.get_mode() == "plan"
    modes.set_mode("accept-edits")
    assert modes.get_mode() == "accept-edits"


def test_set_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        modes.set_mode("yolo")  # type: ignore[arg-type]


def test_reset_mode_returns_to_normal() -> None:
    modes.set_mode("plan")
    modes.reset_mode()
    assert modes.get_mode() == "normal"


# ---------- approval override (the runtime knob) ----------


def test_normal_never_overrides() -> None:
    assert modes.approval_override("write_file", "normal") is None
    assert modes.approval_override("run_shell", "normal") is None


def test_plan_denies_everything_gated() -> None:
    for tool in ("write_file", "edit_file", "run_shell", "delete_file", "spawn_sub_agent"):
        assert modes.approval_override(tool, "plan") == "deny"


def test_accept_edits_allows_writes_only() -> None:
    assert modes.approval_override("write_file", "accept-edits") == "allow"
    assert modes.approval_override("edit_file", "accept-edits") == "allow"
    # Shell and the rest still prompt (no override).
    assert modes.approval_override("run_shell", "accept-edits") is None
    assert modes.approval_override("delete_file", "accept-edits") is None


def test_approval_override_reads_active_mode_by_default() -> None:
    modes.set_mode("plan")
    assert modes.approval_override("write_file") == "deny"


# ---------- deny message ----------


def test_plan_deny_message_guides_the_model() -> None:
    msg = modes.deny_message("plan")
    assert "Plan Mode" in msg
    assert "/mode normal" in msg


# ---------- prompt addendum ----------


def test_prompt_addendum_present_for_plan_and_accept_edits() -> None:
    plan = modes.prompt_addendum("plan")
    assert plan is not None and "Plan Mode" in plan
    accept = modes.prompt_addendum("accept-edits")
    assert accept is not None and "Accept-Edits" in accept


def test_prompt_addendum_none_for_normal() -> None:
    assert modes.prompt_addendum("normal") is None


# ---------- status segment ----------


def test_status_segment_hidden_in_normal() -> None:
    assert modes.status_segment("normal") is None


def test_status_segment_for_plan_and_accept_edits() -> None:
    assert modes.status_segment("plan") == ("plan", "ansiblue")
    label, _ = modes.status_segment("accept-edits")  # type: ignore[misc]
    assert label == "accept-edits"


# ---------- filter_capabilities (reserved knob) ----------


def test_filter_capabilities_is_identity_today() -> None:
    caps = [object(), object()]
    assert modes.filter_capabilities(caps, "plan") is caps


# ---------- approval handler honours the mode (no prompt) ----------


class _FakeCall:
    def __init__(self, tool_name: str, call_id: str) -> None:
        self.tool_name = tool_name
        self.tool_call_id = call_id
        self.args = {"reason": "because"}


class _FakeRequests:
    def __init__(self, calls: list[_FakeCall]) -> None:
        self.approvals = calls

    def build_results(self, approvals: dict) -> dict:
        # Echo the decisions back so the test can inspect them.
        return approvals


@pytest.mark.anyio
async def test_plan_mode_auto_denies_without_prompting() -> None:
    from pydantic_ai.tools import ToolDenied

    from jac.runtime.approval import make_approval_handler
    from jac.runtime.events import EventBus, ModeAutoDecision

    modes.set_mode("plan")
    bus = EventBus()
    handler = make_approval_handler(bus)
    requests = _FakeRequests([_FakeCall("write_file", "c1")])

    # No future is ever awaited (would hang if it tried to prompt).
    results = await handler.handler(None, requests)  # type: ignore[arg-type]
    assert isinstance(results["c1"], ToolDenied)

    events: list = []
    while not bus._queue.empty():
        events.append(bus._queue.get_nowait())
    decision = [e for e in events if isinstance(e, ModeAutoDecision)]
    assert decision and decision[0].decision == "deny"


@pytest.mark.anyio
async def test_accept_edits_auto_approves_writes() -> None:
    from jac.runtime.approval import make_approval_handler
    from jac.runtime.events import EventBus, ModeAutoDecision

    modes.set_mode("accept-edits")
    bus = EventBus()
    handler = make_approval_handler(bus)
    requests = _FakeRequests([_FakeCall("write_file", "c1")])

    results = await handler.handler(None, requests)  # type: ignore[arg-type]
    assert results["c1"] is True

    events: list = []
    while not bus._queue.empty():
        events.append(bus._queue.get_nowait())
    decision = [e for e in events if isinstance(e, ModeAutoDecision)]
    assert decision and decision[0].decision == "allow"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
