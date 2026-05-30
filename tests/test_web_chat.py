"""Tests for the web chat engine (Slice 2 of the web surface, D48).

Exercises the parts that don't need a live model: event→frame serialization
(including the invariant that the HITL ``response_future`` is never serialized),
graceful behavior when no model/profile is configured, and HITL future
resolution. A real streamed turn needs a provider and is covered by manual /
live testing, not the unit suite.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import jac.web.chat as chatmod
from jac.config import reset_settings_cache
from jac.providers.registry import reset_provider_registry_cache
from jac.runtime.events import ApprovalRequest, PlanReplaced, PlanStepView, TextDelta
from jac.web.chat import WebChatManager, event_to_frame
from jac.web.server import create_app
from jac.workspace import paths


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate config + project state; force a workspace with no usable profile."""
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_CONFIG_FILE", user_jac / "config.yaml")

    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    monkeypatch.chdir(project)
    paths.project_root.cache_clear()  # type: ignore[attr-defined]
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]

    monkeypatch.setenv("JAC_SECRETS__BACKEND", "env-only")
    monkeypatch.delenv("JAC_MODEL", raising=False)
    reset_settings_cache()
    reset_provider_registry_cache()
    # Reset the process-wide singleton so each test starts clean.
    monkeypatch.setattr(chatmod, "_MANAGER", None)
    yield
    reset_settings_cache()
    reset_provider_registry_cache()


# ---------- event serialization ----------


def test_event_to_frame_basic() -> None:
    frame = event_to_frame(TextDelta(content="hello"))
    assert frame == {"type": "TextDelta", "content": "hello"}


def test_event_to_frame_drops_response_future() -> None:
    # response_future is non-serializable; passing a sentinel is fine because
    # event_to_frame skips the field by name without reading its value.
    ev = ApprovalRequest(
        tool_call_id="call-1",
        tool_name="write_file",
        reason="save the file",
        args={"path": "x.txt"},
        response_future=None,  # type: ignore[arg-type]
        agent_label="Gru",
    )
    frame = event_to_frame(ev)
    assert "response_future" not in frame
    assert frame["tool_call_id"] == "call-1"
    assert frame["tool_name"] == "write_file"
    assert frame["args"] == {"path": "x.txt"}


def test_event_to_frame_nests_dataclass_fields() -> None:
    ev = PlanReplaced(
        steps=(
            PlanStepView(index=1, text="do a", status="completed"),
            PlanStepView(index=2, text="do b", status="pending"),
        )
    )
    frame = event_to_frame(ev)
    assert frame["type"] == "PlanReplaced"
    assert frame["steps"] == [
        {"index": 1, "text": "do a", "status": "completed"},
        {"index": 2, "text": "do b", "status": "pending"},
    ]


# ---------- HITL resolution (no pending → False) ----------


def test_resolve_unknown_approval_and_clarify_return_false() -> None:
    mgr = WebChatManager()
    assert mgr.resolve_approval("nope", True, None) is False
    assert mgr.resolve_clarify(selected_index=1, selected_text="a", free_text=False) is False


def test_resolve_approval_sets_future_result() -> None:
    async def scenario() -> None:
        mgr = WebChatManager()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        mgr._pending_approvals["call-9"] = future
        assert mgr.resolve_approval("call-9", True, None) is True
        result = await future
        assert result.approved is True
        # second resolve is a no-op (already popped)
        assert mgr.resolve_approval("call-9", False, None) is False

    asyncio.run(scenario())


# ---------- routes ----------


def test_chat_page_renders() -> None:
    client = TestClient(create_app())
    resp = client.get("/chat")
    assert resp.status_code == 200
    assert "/static/chat.js" in resp.text


def test_send_without_model_is_graceful() -> None:
    # No profile configured in this workspace → the chat can't bind a model,
    # so send returns a friendly ack rather than 500-ing.
    client = TestClient(create_app())
    resp = client.post("/chat/send", json={"text": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "profile" in body["reason"].lower() or "model" in body["reason"].lower()


def test_approve_and_clarify_routes_unknown_return_false() -> None:
    client = TestClient(create_app())
    assert client.post("/chat/approve", json={"id": "x", "approved": True}).json() == {"ok": False}
    assert client.post("/chat/clarify", json={"index": 1}).json() == {"ok": False}
