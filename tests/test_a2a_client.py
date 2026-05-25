"""Tests for jac.capabilities.a2a.client.

Three layers:

1. **resolve_target** (pure) — peer name → ``(url, token, display)``
   vs raw URL passthrough vs unknown name raising loudly.
2. **a2a_discover** — exercised end-to-end against a tiny in-process
   Starlette app that serves a real AgentCard; verifies the parsed
   dict has the expected camelCase keys + raises on 404 / bad payload.
3. **a2a_call** — same in-process pattern, but the app implements a
   minimal JSON-RPC ``message/send`` handler so we can prove the
   request shape is right (method + Bearer header + message body) and
   the response parses correctly. JSON-RPC error responses surface as
   ``ValueError``.

We don't spin up uvicorn for these (no need — the tools just hit
``httpx`` and httpx can drive an ASGI app via ``ASGITransport``). The
end-to-end-with-uvicorn coverage lives in ``test_a2a_server.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fasta2a.schema import AgentCapabilities, AgentCard, agent_card_ta
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from jac.capabilities.a2a.client import build_outbound_tools, resolve_target
from jac.errors import JacConfigError
from jac.profiles import A2APeerConfig
from jac.runtime.events import (
    A2AOutboundCall,
    A2AOutboundCompleted,
    EventBus,
    JacEventT,
)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ---------- resolve_target ----------


def test_resolve_target_by_name_uses_peer_config():
    peers = {
        "backend": A2APeerConfig(
            url="http://localhost:9000/", token="t1", description="backend repo"
        )
    }
    t = resolve_target("backend", peers=peers)
    # trailing slash stripped
    assert t.url == "http://localhost:9000"
    # legacy `token=` shorthand promoted to BearerAuth — exposed via the
    # back-compat .token property + the .peer field
    assert t.peer is not None
    assert t.peer.token == "t1"
    assert t.display == "backend"


def test_resolve_target_raw_url_has_no_peer():
    t = resolve_target("http://example.com:8001", peers={})
    assert t.url == "http://example.com:8001"
    assert t.peer is None
    assert t.display == "http://example.com:8001"


def test_resolve_target_https_url_supported():
    t = resolve_target("https://secure.example.com", peers={})
    assert t.url == "https://secure.example.com"


def test_resolve_target_unknown_name_raises():
    with pytest.raises(JacConfigError, match="unknown A2A peer"):
        resolve_target("nope", peers={})


def test_resolve_target_unknown_name_lists_configured_peers():
    peers = {
        "alpha": A2APeerConfig(url="http://a"),
        "beta": A2APeerConfig(url="http://b"),
    }
    with pytest.raises(JacConfigError, match=r"alpha.*beta"):
        resolve_target("gamma", peers=peers)


# ---------- resolve_target: URL → peer auto-promote (Phase 4.d.2) ----------


def test_resolve_target_raw_url_promotes_to_matching_peer():
    """When the model passes a URL that exactly matches a configured peer,
    we promote to that peer so auth is applied (fixes the 401 the user saw
    when Gru reused the URL from a prior a2a_discover instead of the name)."""
    peers = {
        "project-a": A2APeerConfig(url="http://127.0.0.1:8001", token="secret"),
    }
    t = resolve_target("http://127.0.0.1:8001", peers=peers)
    assert t.url == "http://127.0.0.1:8001"
    assert t.peer is not None
    assert t.peer.token == "secret"
    # Display flips to the peer name so events / audit show the friendly id.
    assert t.display == "project-a"


def test_resolve_target_raw_url_promote_normalizes_trailing_slash():
    """URL match after trailing-slash normalization on both sides."""
    peers = {"p": A2APeerConfig(url="http://h:8001/", token="t")}
    t = resolve_target("http://h:8001", peers=peers)
    assert t.peer is not None
    assert t.display == "p"


def test_resolve_target_raw_url_no_promote_when_no_match():
    """Raw URL with no matching peer stays raw — no auth, no surprise."""
    peers = {"other": A2APeerConfig(url="http://different:9000", token="t")}
    t = resolve_target("http://127.0.0.1:8001", peers=peers)
    assert t.peer is None
    assert t.display == "http://127.0.0.1:8001"


def test_resolve_target_raw_url_no_promote_on_multi_match():
    """Two peers pointing at the same URL is a config smell — don't guess
    which auth strategy to apply; fall through to raw call (no auth)."""
    peers = {
        "p1": A2APeerConfig(url="http://shared:8001", token="t1"),
        "p2": A2APeerConfig(url="http://shared:8001", token="t2"),
    }
    t = resolve_target("http://shared:8001", peers=peers)
    assert t.peer is None
    assert t.display == "http://shared:8001"


def test_call_applies_auth_when_url_matches_configured_peer():
    """End-to-end: a2a_call invoked with a raw URL that matches a configured
    peer must send the bearer header (otherwise the peer returns 401)."""
    app, log = _make_rpc_app(expected_token="t-promoted")
    peers = {"project-a": A2APeerConfig(url="http://peer", token="t-promoted")}
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: peers)
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    # No 401 → call succeeded, bearer was applied.
    assert result["id"] == "task-1"
    assert log[0]["auth"] == "Bearer t-promoted"


# ---------- a2a_discover ----------


def _make_card_app(card: AgentCard) -> Starlette:
    payload = agent_card_ta.dump_json(card, by_alias=True)

    async def card_endpoint(_request: Request) -> Response:
        return Response(content=payload, media_type="application/json")

    async def not_found(_request: Request) -> Response:
        return Response(status_code=404)

    return Starlette(
        routes=[
            Route("/.well-known/agent-card.json", card_endpoint),
            Route("/", not_found, methods=["GET", "POST"]),
        ]
    )


def _sample_card() -> AgentCard:
    return {
        "name": "test-peer",
        "description": "a tiny test peer",
        "url": "http://test",
        "version": "0.0.1",
        "protocol_version": "0.3.0",
        "capabilities": AgentCapabilities(
            streaming=False, push_notifications=False, state_transition_history=False
        ),
        "skills": [
            {
                "id": "echo",
                "name": "Echo",
                "description": "echoes user input",
                "tags": ["test"],
                "input_modes": ["text/plain"],
                "output_modes": ["text/plain"],
            }
        ],
        "default_input_modes": ["text/plain"],
        "default_output_modes": ["text/plain"],
    }


def _drive_via_asgi(app: Starlette, base_url: str = "http://peer"):
    """Patch httpx.AsyncClient so it drives the in-process ASGI app.

    Returns a fresh client per call via ASGITransport — same behavior the
    outbound tools see, no network needed.
    """

    class _Patched(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.ASGITransport(app=app))
            kwargs.setdefault("base_url", base_url)
            super().__init__(*args, **kwargs)

    return patch("jac.capabilities.a2a.client.httpx.AsyncClient", _Patched)


def test_discover_returns_card_with_camelcase_keys():
    app = _make_card_app(_sample_card())
    [a2a_discover, _] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        card = _run(a2a_discover(reason="check the peer", url="http://peer"))
    # Tool returns spec camelCase keys
    assert card["name"] == "test-peer"
    assert card["protocolVersion"] == "0.3.0"
    assert "defaultInputModes" in card
    assert card["skills"][0]["id"] == "echo"


def test_discover_rejects_empty_url():
    [a2a_discover, _] = build_outbound_tools(peers_getter=lambda: {})
    with pytest.raises(ValueError, match="must not be empty"):
        _run(a2a_discover(reason="x", url=""))


def test_discover_raises_on_404():
    async def not_found(_r):
        return Response(status_code=404)

    app = Starlette(routes=[Route("/.well-known/agent-card.json", not_found)])
    [a2a_discover, _] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app), pytest.raises(ValueError, match="HTTP 404"):
        _run(a2a_discover(reason="x", url="http://peer"))


def test_discover_raises_on_malformed_card():
    async def bad_card(_r):
        return JSONResponse({"not": "a card"})

    app = Starlette(routes=[Route("/.well-known/agent-card.json", bad_card)])
    [a2a_discover, _] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app), pytest.raises(ValueError, match="malformed AgentCard"):
        _run(a2a_discover(reason="x", url="http://peer"))


def test_discover_emits_outbound_events():
    app = _make_card_app(_sample_card())
    bus = EventBus()
    [a2a_discover, _] = build_outbound_tools(peers_getter=lambda: {}, bus=bus)

    async def go():
        await a2a_discover(reason="x", url="http://peer")
        # Drain bus into a list (non-blocking would require a sentinel;
        # we know exactly 2 events fired).
        events: list[JacEventT] = []
        for _ in range(2):
            events.append(bus._queue.get_nowait())
        return events

    with _drive_via_asgi(app):
        events = _run(go())
    assert isinstance(events[0], A2AOutboundCall)
    assert events[0].target == "http://peer"
    assert events[0].message_preview == "(discover)"
    assert isinstance(events[1], A2AOutboundCompleted)
    assert events[1].state == "completed"


# ---------- a2a_call ----------


def _make_rpc_app(
    *,
    expected_token: str | None = None,
    response_result: dict[str, Any] | None = None,
    response_error: dict[str, Any] | None = None,
) -> tuple[Starlette, list[dict[str, Any]]]:
    """Build a minimal JSON-RPC server that records each request.

    Returns (app, requests_log) — the log is appended to on each call so
    tests can assert what the tool actually sent (method name, message
    parts, auth header, etc.).
    """
    requests_log: list[dict[str, Any]] = []

    async def rpc_endpoint(request: Request) -> Response:
        body = await request.body()
        payload = json.loads(body)
        auth = request.headers.get("authorization", "")
        requests_log.append({"body": payload, "auth": auth})

        # Optional auth check (used by the bearer-injection test).
        if expected_token is not None and auth != f"Bearer {expected_token}":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {"code": -32000, "message": "missing or wrong bearer"},
                },
                status_code=200,
            )

        if response_error is not None:
            return JSONResponse({"jsonrpc": "2.0", "id": payload["id"], "error": response_error})
        # Response shape uses camelCase aliases (wire format) so the
        # fasta2a TypeAdapter on the client side validates cleanly.
        result = response_result or {
            "id": "task-1",
            "contextId": payload["params"]["message"].get("contextId", "ctx-1"),
            "kind": "task",
            "status": {"state": "completed"},
        }
        return JSONResponse({"jsonrpc": "2.0", "id": payload["id"], "result": result})

    app = Starlette(routes=[Route("/", rpc_endpoint, methods=["POST"])])
    return app, requests_log


def test_call_sends_message_send_with_text_part():
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="ask peer", peer_or_url="http://peer", message="hello peer"))
    assert result["id"] == "task-1"
    # The request that hit the server has the right shape
    assert len(log) == 1
    body = log[0]["body"]
    assert body["method"] == "message/send"
    parts = body["params"]["message"]["parts"]
    assert parts == [{"kind": "text", "text": "hello peer"}]


def test_call_injects_bearer_for_named_peer():
    app, log = _make_rpc_app(expected_token="secret-123")
    peers = {"backend": A2APeerConfig(url="http://peer", token="secret-123")}
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: peers)
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="backend", message="hi"))
    assert log[0]["auth"] == "Bearer secret-123"


def test_call_omits_auth_for_raw_url():
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    assert log[0]["auth"] == ""


def test_call_passes_context_id_when_provided():
    """Request body uses camelCase aliases on the wire (`by_alias=True`)."""
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi", context_id="ctx-99"))
    assert log[0]["body"]["params"]["message"]["contextId"] == "ctx-99"


def test_call_omits_context_id_for_fresh_conversation():
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    msg = log[0]["body"]["params"]["message"]
    # Both forms absent — we never set the snake field, dump_json would
    # only emit camelCase if we had.
    assert "contextId" not in msg
    assert "context_id" not in msg


def test_call_surfaces_jsonrpc_error_as_valueerror():
    app, _ = _make_rpc_app(response_error={"code": -32600, "message": "bad request"})
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app), pytest.raises(ValueError, match=r"-32600.*bad request"):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))


def test_call_rejects_empty_message():
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with pytest.raises(ValueError, match="must not be empty"):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message=""))


def test_call_raises_on_unknown_peer_name():
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with pytest.raises(JacConfigError, match="unknown A2A peer"):
        _run(a2a_call(reason="x", peer_or_url="nope", message="hi"))


def test_call_emits_outbound_events_with_peer_name():
    app, _ = _make_rpc_app()
    peers = {"backend": A2APeerConfig(url="http://peer", token=None)}
    bus = EventBus()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: peers, bus=bus)

    async def go():
        await a2a_call(reason="x", peer_or_url="backend", message="hello")
        return [bus._queue.get_nowait() for _ in range(2)]

    with _drive_via_asgi(app):
        events = _run(go())
    assert isinstance(events[0], A2AOutboundCall)
    assert events[0].target == "backend"  # peer name, not URL
    assert events[0].message_preview.startswith("hello")
    assert isinstance(events[1], A2AOutboundCompleted)
    assert events[1].target == "backend"
    assert events[1].state == "completed"


def test_call_emits_failed_state_on_jsonrpc_error():
    app, _ = _make_rpc_app(response_error={"code": -32000, "message": "nope"})
    bus = EventBus()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {}, bus=bus)

    async def go():
        import contextlib

        with contextlib.suppress(ValueError):
            await a2a_call(reason="x", peer_or_url="http://peer", message="hi")
        return [bus._queue.get_nowait() for _ in range(2)]

    with _drive_via_asgi(app):
        events = _run(go())
    assert isinstance(events[1], A2AOutboundCompleted)
    assert events[1].state == "failed"


# ---------- peer refresh via the getter ----------


def test_peers_getter_picks_up_runtime_changes():
    """A2ACapability swaps peers in place on /profile — the getter must
    see the new map without rebuilding the tools."""
    peers: dict[str, A2APeerConfig] = {}
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: peers)

    # Initially no peer named "live"
    with pytest.raises(JacConfigError):
        _run(a2a_call(reason="x", peer_or_url="live", message="hi"))

    # Mutate the dict the getter closes over — same dict, new contents.
    app, _ = _make_rpc_app()
    peers["live"] = A2APeerConfig(url="http://peer", token=None)
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="live", message="hi"))
    assert result["id"] == "task-1"


# ---------- Polling tasks/get until terminal (Phase 4.d follow-up) ----------


def _make_broker_app(
    *,
    states: list[str],
    final_artifacts: list | None = None,
    final_history: list | None = None,
    expected_token: str | None = None,
):
    """Peer that mimics fasta2a: message/send returns 'submitted' immediately,
    then tasks/get walks through ``states`` one entry per call until it lands
    on the final terminal state. Lets us assert the polling loop transitions
    correctly without a real broker."""
    state_iter = iter(states)
    log: list[dict[str, Any]] = []
    task_id = "task-poll-1"
    context_id = "ctx-poll-1"

    async def rpc_endpoint(request: Request) -> Response:
        payload = json.loads(await request.body())
        auth = request.headers.get("authorization", "")
        log.append({"method": payload["method"], "body": payload, "auth": auth})

        if expected_token is not None and auth != f"Bearer {expected_token}":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {"code": -32000, "message": "bad bearer"},
                }
            )

        method = payload["method"]
        if method == "message/send":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "id": task_id,
                        "contextId": context_id,
                        "kind": "task",
                        "status": {"state": "submitted"},
                    },
                }
            )
        if method == "tasks/get":
            try:
                next_state = next(state_iter)
            except StopIteration:
                next_state = "completed"
            task = {
                "id": task_id,
                "contextId": context_id,
                "kind": "task",
                "status": {"state": next_state},
            }
            if next_state == "completed":
                if final_artifacts is not None:
                    task["artifacts"] = final_artifacts
                if final_history is not None:
                    task["history"] = final_history
            return JSONResponse({"jsonrpc": "2.0", "id": payload["id"], "result": task})

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {"code": -32601, "message": f"unknown method {method}"},
            }
        )

    app = Starlette(routes=[Route("/", rpc_endpoint, methods=["POST"])])
    return app, log


def test_call_polls_until_completed():
    """submitted (from message/send) → working → working → completed should
    surface the completed task to the caller, with artifacts intact."""
    app, log = _make_broker_app(
        states=["working", "working", "completed"],
        final_artifacts=[
            {
                "artifactId": "a1",
                "parts": [{"kind": "text", "text": "here is your answer"}],
            }
        ],
    )
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))

    assert result["status"]["state"] == "completed"
    assert result["artifacts"][0]["parts"][0]["text"] == "here is your answer"
    # message/send + 3 tasks/get round-trips
    methods = [entry["method"] for entry in log]
    assert methods == ["message/send", "tasks/get", "tasks/get", "tasks/get"]


def test_call_returns_inline_terminal_without_polling():
    """If message/send already returns a terminal task (no broker / fast peer),
    we must NOT issue any tasks/get calls."""
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    assert [entry["body"]["method"] for entry in log] == ["message/send"]


def test_call_stops_polling_on_input_required():
    """input-required is non-terminal but waiting on us — return the task
    as-is so the calling Gru can read the embedded prompt."""
    app, log = _make_broker_app(states=["working", "input-required"])
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    assert result["status"]["state"] == "input-required"
    # No extra polling after the peer asked us for input.
    methods = [e["method"] for e in log]
    assert methods.count("tasks/get") == 2


def test_call_propagates_auth_to_tasks_get():
    """The same bearer used on message/send must flow into tasks/get polls."""
    app, log = _make_broker_app(states=["completed"], expected_token="secret-9")
    peers = {"backend": A2APeerConfig(url="http://peer", token="secret-9")}
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: peers)
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="backend", message="hi"))
    # Both calls carried the bearer.
    auths = [entry["auth"] for entry in log]
    assert auths == ["Bearer secret-9", "Bearer secret-9"]


def test_call_timeout_returns_partial_with_marker(monkeypatch):
    """When the peer never reaches terminal within the deadline, return the
    latest state we saw with a `_jac_timeout` marker so the calling Gru can
    tell stale data from a fresh terminal."""
    # Squash the timeout to a sub-second value AND speed up the poll loop
    # so the test isn't slow.
    monkeypatch.setattr("jac.capabilities.a2a.client._CALL_TIMEOUT_S", 0.6)
    monkeypatch.setattr("jac.capabilities.a2a.client._POLL_INITIAL_INTERVAL_S", 0.05)
    monkeypatch.setattr("jac.capabilities.a2a.client._POLL_MAX_INTERVAL_S", 0.1)

    # Always "working" — never reaches a terminal state.
    app, _log = _make_broker_app(states=["working"] * 50)
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    assert result["status"]["state"] == "working"
    assert result.get("_jac_timeout") is True


def test_call_passes_task_id_through_to_tasks_get():
    """tasks/get params.id must be the id returned by message/send."""
    app, log = _make_broker_app(states=["completed"])
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    get_call = next(e for e in log if e["method"] == "tasks/get")
    assert get_call["body"]["params"]["id"] == "task-poll-1"


def test_call_handles_message_response_without_polling():
    """Some peers reply with a Message (no status block) for simple sync
    queries — we must return it as-is, never poll."""

    async def rpc_endpoint(request: Request) -> Response:
        payload = json.loads(await request.body())
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "m-9",
                    "parts": [{"kind": "text", "text": "hello back"}],
                },
            }
        )

    app = Starlette(routes=[Route("/", rpc_endpoint, methods=["POST"])])
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    assert result["kind"] == "message"
    assert result["parts"][0]["text"] == "hello back"


# ---------- File transfer: outbound (files= param) ----------


def test_call_attaches_file_part_with_bytes(tmp_path):
    """files=[csv_path] adds a FilePart with base64 bytes alongside the text."""
    import base64 as _b64

    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")

    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(
            a2a_call(
                reason="x",
                peer_or_url="http://peer",
                message="analyze this",
                files=[str(csv)],
            )
        )

    parts = log[0]["body"]["params"]["message"]["parts"]
    # First part text, second the file
    assert parts[0] == {"kind": "text", "text": "analyze this"}
    file_part = parts[1]
    assert file_part["kind"] == "file"
    assert file_part["file"]["name"] == "data.csv"
    assert file_part["file"]["mimeType"] == "text/csv"
    assert _b64.b64decode(file_part["file"]["bytes"]).decode() == "a,b\n1,2\n"
    # Metadata carries filename too (belt-and-braces for strict peers)
    assert file_part["metadata"]["filename"] == "data.csv"


def test_call_rejects_missing_file_path(tmp_path):
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    missing = tmp_path / "nope.csv"
    with pytest.raises(JacConfigError, match="file not found"):
        _run(
            a2a_call(
                reason="x",
                peer_or_url="http://peer",
                message="m",
                files=[str(missing)],
            )
        )


def test_call_rejects_directory_as_file(tmp_path):
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with pytest.raises(JacConfigError, match="not a regular file"):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="m", files=[str(tmp_path)]))


def test_call_rejects_oversize_file(tmp_path, monkeypatch):
    """Cap defends against accidental DOS via giant payloads."""
    monkeypatch.setattr("jac.capabilities.a2a.client._MAX_OUTBOUND_FILE_BYTES", 16)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 32)
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with pytest.raises(JacConfigError, match="cap is"):
        _run(
            a2a_call(
                reason="x",
                peer_or_url="http://peer",
                message="m",
                files=[str(big)],
            )
        )


def test_call_uses_octet_stream_for_unknown_extension(tmp_path):
    f = tmp_path / "weird.zzz"
    f.write_bytes(b"\x00\x01\x02")
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="m", files=[str(f)]))
    file_part = log[0]["body"]["params"]["message"]["parts"][1]
    assert file_part["file"]["mimeType"] == "application/octet-stream"


def test_call_no_files_param_omits_file_parts():
    """Backward compat: existing callers without files= see no FilePart."""
    app, log = _make_rpc_app()
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        _run(a2a_call(reason="x", peer_or_url="http://peer", message="hi"))
    parts = log[0]["body"]["params"]["message"]["parts"]
    assert len(parts) == 1
    assert parts[0]["kind"] == "text"


# ---------- File transfer: inbound (auto-save) ----------


def _make_artifact_app(*, artifact_parts: list[dict]):
    """Peer that returns a single artifact with the given parts on
    message/send (inline-terminal, no polling needed)."""

    async def rpc_endpoint(request: Request) -> Response:
        payload = json.loads(await request.body())
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "id": "task-with-files",
                    "contextId": "ctx-1",
                    "kind": "task",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"artifactId": "a1", "parts": artifact_parts},
                    ],
                },
            }
        )

    app = Starlette(routes=[Route("/", rpc_endpoint, methods=["POST"])])
    return app


def _file_part(name: str, content: bytes, mime: str = "image/png"):
    import base64 as _b64

    return {
        "kind": "file",
        "file": {
            "name": name,
            "mimeType": mime,
            "bytes": _b64.b64encode(content).decode("ascii"),
        },
    }


def test_inbound_file_part_saved_and_path_returned(tmp_path, monkeypatch):
    """Returned FilePart with bytes lands under inbound-files/<task_id>/ and
    its path is surfaced in _jac_saved_files."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    app = _make_artifact_app(artifact_parts=[_file_part("chart.png", b"\x89PNG-bytes")])
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="m"))

    assert "_jac_saved_files" in result
    assert len(result["_jac_saved_files"]) == 1
    saved = Path(result["_jac_saved_files"][0])
    assert saved.exists()
    assert saved.read_bytes() == b"\x89PNG-bytes"
    # Lands under the right subtree
    assert "inbound-files/task-with-files/chart.png" in saved.as_posix()


def test_inbound_no_files_means_no_save_no_key(tmp_path, monkeypatch):
    """Text-only artifact: no _jac_saved_files key, no inbound-files dir."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    app = _make_artifact_app(artifact_parts=[{"kind": "text", "text": "summary text"}])
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="m"))

    assert "_jac_saved_files" not in result
    assert not (tmp_path / ".agents" / "a2a" / "inbound-files").exists()


def test_inbound_sanitizes_filename_path_traversal(tmp_path, monkeypatch):
    """A malicious peer can't write outside our inbound-files/<task_id>/ box."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    # Filename tries to climb out
    app = _make_artifact_app(
        artifact_parts=[_file_part("../../etc/passwd", b"haha", mime="text/plain")]
    )
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="m"))

    saved = Path(result["_jac_saved_files"][0])
    # Stays under inbound-files/<task_id>/, not in /etc
    assert "inbound-files/task-with-files/" in saved.as_posix()
    assert ".." not in saved.parts
    assert saved.exists()


def test_inbound_dedupes_colliding_filenames(tmp_path, monkeypatch):
    """Two file parts with the same name get a numeric suffix to avoid
    silently overwriting."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    app = _make_artifact_app(
        artifact_parts=[
            _file_part("out.png", b"first", mime="image/png"),
            _file_part("out.png", b"second", mime="image/png"),
        ]
    )
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="m"))

    saved = sorted(result["_jac_saved_files"])
    assert len(saved) == 2
    names = [Path(p).name for p in saved]
    assert names == ["out-2.png", "out.png"]


def test_inbound_skips_malformed_base64(tmp_path, monkeypatch):
    """Bad base64 from peer doesn't crash the call; we just skip that part."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    app = _make_artifact_app(
        artifact_parts=[
            {
                "kind": "file",
                "file": {"name": "broken.bin", "bytes": "@@@not-base64@@@"},
            },
            _file_part("ok.png", b"good", mime="image/png"),
        ]
    )
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="m"))

    saved = result["_jac_saved_files"]
    assert len(saved) == 1
    assert Path(saved[0]).name == "ok.png"


def test_inbound_uri_only_file_part_is_skipped(tmp_path, monkeypatch):
    """v1 explicitly skips FileWithUri (no SSRF guard yet); only bytes save."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    app = _make_artifact_app(
        artifact_parts=[
            {
                "kind": "file",
                "file": {"name": "remote.bin", "uri": "https://example.com/data"},
            }
        ]
    )
    [_, a2a_call] = build_outbound_tools(peers_getter=lambda: {})
    with _drive_via_asgi(app):
        result = _run(a2a_call(reason="x", peer_or_url="http://peer", message="m"))

    assert "_jac_saved_files" not in result
