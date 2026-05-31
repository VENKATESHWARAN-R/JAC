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
from jac.runtime.sub_agent import _pending_spawns
from jac.runtime.sub_agent_usage import reset_sub_agent_stats
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
    # Reset the process-wide singleton + sub-agent registries so the dashboard
    # snapshot is deterministic regardless of test ordering.
    monkeypatch.setattr(chatmod, "_MANAGER", None)
    reset_sub_agent_stats()
    _pending_spawns.clear()
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


# ---------- dashboard (Slice 3) ----------


def test_dashboard_shape_with_no_runtime() -> None:
    d = WebChatManager().dashboard()
    assert d["tokens"] == {
        "input": 0,
        "output": 0,
        "total": 0,
        "cache_pct": None,
        "project_total": 0,
        "budget_pct": None,
    }
    assert d["sub_agents"]["active"] == []
    assert d["sub_agents"]["spawns"] == 0
    assert d["files"] == []


def test_dashboard_reports_existing_changed_files(tmp_path: Path) -> None:
    # The cwd is the isolated tmp project (set by the fixture). Only files that
    # actually exist on disk are reported — a denied/phantom write drops out.
    Path("a.py").write_text("x")  # exists
    mgr = WebChatManager()
    mgr.files_changed["a.py"] = "write"
    mgr.files_changed["ghost.py"] = "write"  # never created
    files = mgr.dashboard()["files"]
    assert {"path": "a.py", "action": "write"} in files
    assert all(f["path"] != "ghost.py" for f in files)


def test_dashboard_lists_running_minions_from_events() -> None:
    # Parallel/sequential workers run to completion without ever parking in
    # ``_pending_spawns``, so the dashboard's active list must come from the
    # SubAgentSpawned/SubAgentCompleted lifecycle the consumer records.
    mgr = WebChatManager()
    mgr.active_minions["minion-1"] = {
        "tier": "small",
        "model": "anthropic:claude-haiku",
        "objective": "summarize module a",
    }
    active = mgr.dashboard()["sub_agents"]["active"]
    assert len(active) == 1
    assert active[0]["spawn_id"] == "minion-1"
    assert active[0]["tier"] == "small"
    assert active[0]["status"] == "running"
    assert active[0]["objective"] == "summarize module a"

    # Completion drops it back off the dashboard.
    mgr.active_minions.pop("minion-1", None)
    assert mgr.dashboard()["sub_agents"]["active"] == []


def test_history_messages_serializes_transcript() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )

    mgr = WebChatManager()
    mgr.history = [
        ModelRequest(parts=[UserPromptPart(content="write a file")]),
        ModelResponse(
            parts=[
                TextPart(content="On it."),
                ToolCallPart(
                    tool_name="write_file",
                    args={"reason": "create it", "path": "a.txt"},
                ),
            ]
        ),
        ModelRequest(parts=[UserPromptPart(content="thanks")]),
    ]
    msgs = mgr.history_messages()
    assert msgs == [
        {"role": "user", "content": "write a file"},
        {"role": "assistant", "content": "On it."},
        {"role": "tool", "name": "write_file", "reason": "create it"},
        {"role": "user", "content": "thanks"},
    ]


def test_history_messages_empty_without_history() -> None:
    assert WebChatManager().history_messages() == []


def test_chat_history_route() -> None:
    client = TestClient(create_app())
    resp = client.get("/chat/history")
    assert resp.status_code == 200
    assert resp.json() == {"messages": []}


def test_environment_empty_without_runtime() -> None:
    env = WebChatManager().environment()
    assert env == {"a2a": [], "mcp": [], "skills": []}


def test_environment_reads_live_capabilities() -> None:
    # No live model in the test workspace, so stub a runtime carrying the three
    # capabilities the panel reads. The shapes mirror the real objects.
    from types import SimpleNamespace

    mgr = WebChatManager()
    peer = SimpleNamespace(url="http://127.0.0.1:8001", auth=None, description="")
    skill = SimpleNamespace(name="jac-cli", description="how to run X", source="package")
    server = SimpleNamespace(
        transport="stdio",
        knobs=SimpleNamespace(enabled=True, requires_approval=True),
        source="project",
    )
    mgr.runtime = SimpleNamespace(  # type: ignore[assignment]
        a2a_capability=SimpleNamespace(peers={"peer-a": peer}, session_peers={}),
        mcp_capability=SimpleNamespace(catalog=SimpleNamespace(servers={"srv": server})),
        skills_capability=SimpleNamespace(skills={"jac-cli": skill}),
    )
    env = mgr.environment()
    assert env["a2a"] == [
        {"name": "peer-a", "url": "http://127.0.0.1:8001", "auth": "none", "source": "profile"}
    ]
    assert env["mcp"] == [
        {
            "name": "srv",
            "transport": "stdio",
            "enabled": True,
            "approval": True,
            "source": "project",
        }
    ]
    assert env["skills"] == [
        {"name": "jac-cli", "description": "how to run X", "source": "package"}
    ]


def test_chat_environment_route() -> None:
    client = TestClient(create_app())
    resp = client.get("/chat/environment")
    assert resp.status_code == 200
    assert set(resp.json()) == {"a2a", "mcp", "skills"}


def test_chat_status_route() -> None:
    client = TestClient(create_app())
    resp = client.get("/chat/status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"tokens", "sub_agents", "files", "scope"}
