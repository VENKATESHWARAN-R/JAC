"""Tests for the MCP server loader (Phase F, D28).

Covers catalog discovery + layering, knob defaults/validation, enable/disable
persistence, toolset wrapping (defer → summarize → approval), graceful
load-error handling, the system-prompt hint, and the D28 ``reason:`` exemption
rendering in the approval handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolCallPart

from jac.capabilities import mcp as mcp_mod
from jac.capabilities.mcp import (
    MCPCapability,
    MCPServerKnobs,
    build_mcp_toolsets,
    load_mcp_catalog,
)
from jac.runtime.approval import make_approval_handler
from jac.runtime.events import ApprovalRequest, ApprovalResponse, EventBus
from jac.tools.toolset import SummarizingToolset
from jac.workspace import paths


@pytest.fixture
def isolated_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point user + project MCP catalogs at tmp_path and isolate state root."""
    user_file = tmp_path / ".jac" / "mcp.json"
    project_root = tmp_path / "project"
    project_agents = project_root / ".agents"
    user_file.parent.mkdir(parents=True)
    project_agents.mkdir(parents=True)
    (project_root / ".git").mkdir()

    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    paths.project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(paths, "USER_MCP_FILE", user_file)
    monkeypatch.setattr(paths, "find_project_root", lambda start=None: project_root)
    monkeypatch.setattr(paths, "project_root", lambda start=None: project_root)
    monkeypatch.setattr(paths, "in_project", lambda start=None: True)
    yield tmp_path


def _write_catalog(path: Path, servers: dict[str, Any], jac: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"mcpServers": servers}
    if jac is not None:
        payload["jac"] = jac
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


_STDIO = {"command": "echo", "args": ["hi"]}
_HTTP = {"type": "http", "url": "https://example.com/mcp"}


# ---------- catalog discovery + layering ----------


def test_empty_when_no_files(isolated_mcp: Path) -> None:
    catalog = load_mcp_catalog()
    assert catalog.servers == {}
    assert catalog.parse_errors == []


def test_user_catalog_loads_with_default_knobs(isolated_mcp: Path) -> None:
    _write_catalog(paths.USER_MCP_FILE, {"weather": _HTTP})
    catalog = load_mcp_catalog()
    assert set(catalog.servers) == {"weather"}
    srv = catalog.servers["weather"]
    assert srv.source == "user"
    assert srv.transport == "http"
    assert srv.knobs == MCPServerKnobs(enabled=True, defer=True, requires_approval=True)


def test_knobs_from_jac_block(isolated_mcp: Path) -> None:
    _write_catalog(
        paths.USER_MCP_FILE,
        {"weather": _HTTP},
        jac={"weather": {"requires_approval": False, "defer": False}},
    )
    srv = load_mcp_catalog().servers["weather"]
    assert srv.knobs.requires_approval is False
    assert srv.knobs.defer is False
    assert srv.knobs.enabled is True  # untouched key keeps its default


def test_project_shadows_user_per_name(isolated_mcp: Path) -> None:
    _write_catalog(paths.USER_MCP_FILE, {"shared": _HTTP, "user_only": _STDIO})
    _write_catalog(paths.project_mcp_file(), {"shared": _STDIO})
    catalog = load_mcp_catalog()
    assert set(catalog.servers) == {"shared", "user_only"}
    assert catalog.servers["shared"].source == "project"
    assert catalog.servers["shared"].transport == "stdio"
    assert catalog.servers["user_only"].source == "user"


def test_invalid_json_collected_not_raised(isolated_mcp: Path) -> None:
    paths.USER_MCP_FILE.write_text("{not json", encoding="utf-8")
    catalog = load_mcp_catalog()
    assert catalog.servers == {}
    assert len(catalog.parse_errors) == 1


def test_invalid_knobs_fall_back_to_defaults(isolated_mcp: Path) -> None:
    _write_catalog(paths.USER_MCP_FILE, {"weather": _HTTP}, jac={"weather": {"bogus_key": 1}})
    catalog = load_mcp_catalog()
    assert catalog.servers["weather"].knobs == MCPServerKnobs()
    assert any("invalid" in e for e in catalog.parse_errors)


def test_enabled_property_filters_disabled(isolated_mcp: Path) -> None:
    _write_catalog(
        paths.USER_MCP_FILE,
        {"a": _HTTP, "b": _HTTP},
        jac={"b": {"enabled": False}},
    )
    catalog = load_mcp_catalog()
    assert set(catalog.enabled) == {"a"}


# ---------- enable / disable persistence ----------


def test_set_enabled_persists_and_updates_memory(isolated_mcp: Path) -> None:
    _write_catalog(paths.USER_MCP_FILE, {"weather": _HTTP})
    cap = MCPCapability()
    assert cap.set_enabled("weather", False) is True
    # in-memory updated
    assert cap.catalog.servers["weather"].knobs.enabled is False
    # persisted to the owning (user) file's jac block, mcpServers preserved
    on_disk = json.loads(paths.USER_MCP_FILE.read_text())
    assert on_disk["jac"]["weather"]["enabled"] is False
    assert "weather" in on_disk["mcpServers"]
    # survives a reload
    cap.reload()
    assert cap.catalog.servers["weather"].knobs.enabled is False


def test_set_enabled_unknown_server_returns_false(isolated_mcp: Path) -> None:
    cap = MCPCapability()
    assert cap.set_enabled("nope", False) is False


# ---------- toolset building + wrapping ----------


def test_build_wraps_defer_summarize_approval(isolated_mcp: Path) -> None:
    # Real (never-connected) entries — construction doesn't spawn anything.
    _write_catalog(
        paths.USER_MCP_FILE,
        {"gated": _STDIO, "trusted": _HTTP},
        jac={"trusted": {"requires_approval": False}},
    )
    toolsets, error = build_mcp_toolsets(load_mcp_catalog())
    assert error is None
    assert len(toolsets) == 2
    # gated → approval is outermost; trusted → summarizing is outermost.
    from pydantic_ai.toolsets.approval_required import ApprovalRequiredToolset

    by_outer = {type(ts).__name__ for ts in toolsets}
    assert "ApprovalRequiredToolset" in by_outer
    assert "SummarizingToolset" in by_outer
    gated = next(ts for ts in toolsets if isinstance(ts, ApprovalRequiredToolset))
    trusted = next(ts for ts in toolsets if isinstance(ts, SummarizingToolset))
    assert isinstance(gated, ApprovalRequiredToolset)
    assert trusted.summarize_all is True


def test_build_skips_disabled_with_missing_env(isolated_mcp: Path) -> None:
    # The disabled server references an undefined env var; because only
    # enabled servers are built (and expanded), it must not error or build.
    _write_catalog(
        paths.USER_MCP_FILE,
        {"on": _HTTP, "off": {"command": "secret", "args": ["${UNDEFINED_VAR}"]}},
        jac={"off": {"enabled": False}},
    )
    toolsets, error = build_mcp_toolsets(load_mcp_catalog())
    assert error is None
    assert len(toolsets) == 1


def test_build_no_enabled_servers_returns_empty(isolated_mcp: Path) -> None:
    _write_catalog(paths.USER_MCP_FILE, {"a": _HTTP}, jac={"a": {"enabled": False}})
    toolsets, error = build_mcp_toolsets(load_mcp_catalog())
    assert toolsets == []
    assert error is None


def test_build_isolates_per_server_error(
    isolated_mcp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One enabled server has a missing env var; the other still builds, and
    # the error names the broken server (per-server isolation).
    monkeypatch.delenv("MISSING_TOK", raising=False)
    _write_catalog(
        paths.USER_MCP_FILE,
        {"good": _HTTP, "bad": {"command": "x", "env": {"T": "${MISSING_TOK}"}}},
    )
    toolsets, error = build_mcp_toolsets(load_mcp_catalog())
    assert len(toolsets) == 1
    assert error is not None
    assert "bad" in error
    assert "MISSING_TOK" in error


def test_build_expands_env(isolated_mcp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TEST_URL", "https://expanded.example.com/mcp")
    _write_catalog(paths.USER_MCP_FILE, {"s": {"type": "http", "url": "${MCP_TEST_URL}"}})
    toolsets, error = build_mcp_toolsets(load_mcp_catalog())
    assert error is None
    assert len(toolsets) == 1


# ---------- capability get_toolset / get_instructions ----------


def test_get_toolset_none_when_empty(isolated_mcp: Path) -> None:
    cap = MCPCapability()
    assert cap.get_toolset() is None


def test_get_toolset_combines_when_multiple(isolated_mcp: Path) -> None:
    _write_catalog(paths.USER_MCP_FILE, {"a": _HTTP, "b": _HTTP})
    cap = MCPCapability()
    from pydantic_ai.toolsets import CombinedToolset

    assert isinstance(cap.get_toolset(), CombinedToolset)


def test_instructions_list_enabled_names(isolated_mcp: Path) -> None:
    _write_catalog(
        paths.USER_MCP_FILE,
        {"github": _HTTP, "off": _HTTP},
        jac={"off": {"enabled": False}},
    )
    cap = MCPCapability()
    text = cap.get_instructions()(None)
    assert "github" in text
    assert "off" not in text
    assert "tool search" in text


def test_instructions_empty_when_no_enabled(isolated_mcp: Path) -> None:
    cap = MCPCapability()
    assert cap.get_instructions()(None) == ""


# ---------- D28 reason exemption in the approval handler ----------


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


async def _capture_approval_reason(call: ToolCallPart) -> str | None:
    """Drive the approval handler; return the reason rendered on the event."""
    bus = EventBus()
    handler_cap = make_approval_handler(bus)
    seen: dict[str, str | None] = {}

    async def responder() -> None:
        async for event in bus.stream():
            if isinstance(event, ApprovalRequest):
                seen["reason"] = event.reason
                event.response_future.set_result(ApprovalResponse(approved=True))
                return

    responder_task = asyncio.create_task(responder())
    try:
        ctx = cast(RunContext[Any], None)
        coro = cast(
            Coroutine[Any, Any, Any],
            handler_cap.handler(ctx, DeferredToolRequests(approvals=[call])),
        )
        await coro
    finally:
        responder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await responder_task
    return seen.get("reason")


def test_mcp_tool_without_reason_renders_placeholder() -> None:
    call = ToolCallPart(tool_name="github_create_issue", args={"title": "x"}, tool_call_id="c1")
    reason = _run(_capture_approval_reason(call))
    assert reason == "(mcp tool — no reason captured)"


def test_jac_tool_reason_is_preserved() -> None:
    call = ToolCallPart(
        tool_name="write_file", args={"reason": "save the report"}, tool_call_id="c2"
    )
    reason = _run(_capture_approval_reason(call))
    assert reason == "save the report"


# ---------- module export sanity ----------


def test_make_mcp_capability_factory(isolated_mcp: Path) -> None:
    cap = mcp_mod.make_mcp_capability()
    assert isinstance(cap, MCPCapability)
