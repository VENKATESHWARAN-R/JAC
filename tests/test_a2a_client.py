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
from jac.runtime.bus import EventBus
from jac.runtime.events import (
    A2AOutboundCall,
    A2AOutboundCompleted,
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
