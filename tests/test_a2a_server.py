"""End-to-end smoke test for the A2A server.

Stands up the server on an OS-assigned port, fetches the agent card
through the well-known endpoint (unauthenticated by design), confirms
the JSON-RPC endpoint rejects requests without a valid bearer, then
shuts down cleanly. We do NOT exercise an actual ``message/send``
round-trip here — that requires a real LLM and is the proper job of
an integration suite (Phase 7). PR1's bar is "wiring is sound".

Why a real port instead of an in-process ASGI client: the server module
boots uvicorn, and we want to prove that path works (the slash/headless
commands rely on it). Using an ASGI transport would skip uvicorn
entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Coroutine
from typing import Any

import httpx
import pytest
from pydantic_ai.models.test import TestModel

from jac.capabilities.a2a import make_a2a_capability


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _free_port() -> int:
    """Bind to port 0, grab the OS-assigned port, release. Race-free enough
    for serial test runs on a developer machine."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def free_port() -> int:
    return _free_port()


def test_server_lifecycle_card_and_auth(tmp_path, monkeypatch, free_port: int):
    """One scenario, three assertions:

    1. ``start`` returns a ServerInfo with the right URL and token shape.
    2. ``GET /.well-known/agent-card.json`` works *without* auth and
       returns a card with our expected name.
    3. ``POST /`` without auth returns 401; with the right bearer it
       reaches fasta2a (even if our mocked agent fails the run, we get
       past auth — that's the wiring assertion).
    """
    # Pin the project root to tmp_path so context files don't pollute the repo.
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    # Use a fake model — we won't actually call it (PR1 doesn't round-trip a
    # message/send). The guest agent's CONSTRUCTION must succeed, which means
    # pydantic-ai needs to accept the model string. "test:fake" is in the
    # synthesizable family pydantic-ai uses for tests; if it doesn't work we
    # can switch to mocking build_guest_gru directly.
    cap = make_a2a_capability(model=TestModel(), profile_name="test")

    async def _scenario():
        info = await cap.start_server(host="127.0.0.1", port=free_port, unsafe=False)
        try:
            assert info.url == f"http://127.0.0.1:{free_port}"
            assert info.token and len(info.token) >= 40

            async with httpx.AsyncClient(base_url=info.url, timeout=5.0) as client:
                # 1. Card is reachable without auth.
                card_resp = await client.get("/.well-known/agent-card.json")
                assert card_resp.status_code == 200
                card = card_resp.json()
                assert card["name"] == "jac-test"
                assert card["url"] == info.url
                # Auth is advertised
                assert "securitySchemes" in card

                # 2. POST / without auth → 401.
                unauthed = await client.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "message/send",
                        "params": {
                            "message": {
                                "role": "user",
                                "parts": [{"kind": "text", "text": "hi"}],
                                "kind": "message",
                                "message_id": "m1",
                            }
                        },
                    },
                )
                assert unauthed.status_code == 401

                # 3. POST / with the right bearer makes it past auth. We
                #    don't assert on the JSON-RPC response body because
                #    test:fake-model can't actually run — fasta2a will
                #    return either a task in 'submitted' or an internal
                #    error. The point is: status code != 401.
                authed = await client.post(
                    "/",
                    headers={"Authorization": f"Bearer {info.token}"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "2",
                        "method": "message/send",
                        "params": {
                            "message": {
                                "role": "user",
                                "parts": [{"kind": "text", "text": "hi"}],
                                "kind": "message",
                                "message_id": "m2",
                            }
                        },
                    },
                )
                assert authed.status_code != 401
        finally:
            await cap.shutdown()

    _run(_scenario())


def test_server_unsafe_omits_auth_and_accepts_anyone(tmp_path, monkeypatch, free_port: int):
    """`--unsafe` skips middleware AND omits securitySchemes from the card."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    cap = make_a2a_capability(model=TestModel(), profile_name="test")

    async def _scenario():
        info = await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        try:
            assert info.unsafe is True
            assert info.token == ""  # no token issued in unsafe mode

            async with httpx.AsyncClient(base_url=info.url, timeout=5.0) as client:
                card = (await client.get("/.well-known/agent-card.json")).json()
                # Card honestly admits no auth
                assert "securitySchemes" not in card

                # No bearer header — should NOT be 401 (gets through to fasta2a)
                resp = await client.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "message/send",
                        "params": {
                            "message": {
                                "role": "user",
                                "parts": [{"kind": "text", "text": "hi"}],
                                "kind": "message",
                                "message_id": "m1",
                            }
                        },
                    },
                )
                assert resp.status_code != 401
        finally:
            await cap.shutdown()

    _run(_scenario())


def test_double_start_raises(tmp_path, monkeypatch, free_port: int):
    """Calling start twice should raise (caller bug; slash handler guards)."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    cap = make_a2a_capability(model=TestModel(), profile_name="test")

    async def _scenario():
        await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        finally:
            await cap.shutdown()

    _run(_scenario())


def test_stop_when_not_running_is_noop():
    cap = make_a2a_capability(model=TestModel(), profile_name="test")

    async def _scenario():
        # Should not raise.
        await cap.stop_server()
        await cap.shutdown()

    _run(_scenario())


# Suppress an unused-import lint — contextlib is not used today but kept so
# follow-up tests can `contextlib.suppress(...)` around teardown noise.
_ = contextlib
