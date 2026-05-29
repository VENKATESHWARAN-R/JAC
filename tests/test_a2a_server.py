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

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
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

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
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

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
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


# ---------- Phase 4.d: retention timer ----------


def test_retention_loop_starts_with_server_and_stops_with_it(tmp_path, monkeypatch, free_port: int):
    """The 1-hour retention loop spins up on start, runs on the server's
    event loop, and is cancelled cleanly on stop. We don't wait for it
    to fire (would burn an hour) — we assert task lifecycle."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    cap = make_a2a_capability(model=TestModel(), profile_name="test")

    async def _scenario():
        await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        try:
            assert cap.server is not None
            retention = cap.server._retention_task
            assert retention is not None
            assert not retention.done()
        finally:
            await cap.stop_server()
        # Stop must cancel + clear the retention task.
        assert cap.server is None

    _run(_scenario())


def test_retention_disabled_when_retention_days_zero(tmp_path, monkeypatch, free_port: int):
    """If retention_days=0 (keep forever), the timer must NOT start —
    otherwise we'd spin a background task that does no useful work."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    cap = make_a2a_capability(model=TestModel(), profile_name="test", retention_days=0)

    async def _scenario():
        await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        try:
            assert cap.server is not None
            assert cap.server._retention_task is None
        finally:
            await cap.shutdown()

    _run(_scenario())


# ---------- Phase 4.d: usage tracker plumbing ----------


def test_capability_accepts_and_threads_usage_tracker(tmp_path, monkeypatch, free_port: int):
    """The usage tracker passed to A2ACapability must make it all the way
    to the AuditingAgentWorker so inbound calls can feed add_external."""
    from jac.runtime.events import EventBus
    from jac.runtime.usage import BudgetLimits, UsageTracker
    from jac.workspace import paths

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    tracker = UsageTracker(
        session_id="s1",
        bus=None,
        usage_file=None,
        limits=BudgetLimits(
            session_input_tokens=None,
            session_total_tokens=None,
            project_total_tokens=None,
            warn_pct=80,
            hardstop_pct=100,
        ),
    )
    cap = make_a2a_capability(
        bus=EventBus(),
        model=TestModel(),
        profile_name="test",
        usage_tracker=tracker,
    )

    async def _scenario():
        await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        try:
            assert cap.server is not None
            # We can't introspect the worker without poking fasta2a internals,
            # but server has the tracker reference — that's the seam we own.
            assert cap.server._usage_tracker is tracker
        finally:
            await cap.shutdown()

    _run(_scenario())


# ---------- Phase 4.d.4: guest materializes inbound FileParts ----------


def test_inbound_file_part_lands_under_guest_uploads(tmp_path, monkeypatch, free_port: int):
    """Smoke test: POST a message/send carrying a FilePart with bytes to a
    live guest server, then verify the file landed under
    .agents/a2a/guest-uploads/<context_id>/. We don't run the agent
    (TestModel has no tools to do anything useful), but the materialize
    step runs BEFORE agent.run, so the file lands either way."""
    import asyncio as _asyncio
    import base64 as _b64

    from jac.workspace import paths

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()

    # call_tools=[] keeps TestModel from synthesizing tool calls — the
    # guest has read_file/grep/etc. in its toolset and would call them
    # with garbage args, which would fail noisily. We only care that
    # materialize runs before the agent does, so an inert reply works.
    cap = make_a2a_capability(model=TestModel(call_tools=[]), profile_name="test")
    csv_bytes = b"a,b\n1,2\n3,4\n"

    async def _scenario():
        info = await cap.start_server(host="127.0.0.1", port=free_port, unsafe=True)
        try:
            req = {
                "jsonrpc": "2.0",
                "id": "r1",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "kind": "message",
                        "messageId": "m1",
                        "contextId": "ctx-upload-test",
                        "parts": [
                            {"kind": "text", "text": "summarize this csv"},
                            {
                                "kind": "file",
                                "file": {
                                    "name": "data.csv",
                                    "mimeType": "text/csv",
                                    "bytes": _b64.b64encode(csv_bytes).decode("ascii"),
                                },
                                # Belt-and-braces: filename in metadata too —
                                # fasta2a 0.6.1's FileWithBytes TypedDict
                                # omits `name`, so its request validator
                                # strips it. Real a2a_call sends both fields;
                                # mirror that here so the server-side
                                # materialize finds the original filename.
                                "metadata": {"filename": "data.csv"},
                            },
                        ],
                    }
                },
            }
            async with httpx.AsyncClient(base_url=info.url, timeout=5.0) as client:
                resp = await client.post("/", json=req)
                assert resp.status_code != 401

            # Materialize happens before agent.run; even with TestModel
            # producing a noisy result, the file should be on disk.
            # Give the broker a beat to dispatch the worker.
            target = tmp_path / ".agents" / "a2a" / "guest-uploads" / "ctx-upload-test"
            for _ in range(20):  # ~2s budget
                if target.exists() and any(target.iterdir()):
                    break
                await _asyncio.sleep(0.1)

            assert target.is_dir(), "guest-uploads/<ctx>/ should exist"
            saved = list(target.iterdir())
            assert len(saved) == 1
            assert saved[0].name == "data.csv"
            assert saved[0].read_bytes() == csv_bytes

            # Regression: prior to the strip_binary_content fix, the
            # worker would crash in agent.run() with "Unsupported binary
            # content type" the moment a non-image FilePart hit the
            # model adapter. TestModel doesn't reject binaries, but a
            # `failed` terminal here would still flag any other
            # regression in the worker's run_task path.
            inbound_log = tmp_path / ".agents" / "a2a" / "inbound.jsonl"
            for _ in range(40):  # ~4s — TestModel run + state update
                if inbound_log.exists() and inbound_log.read_text().strip():
                    break
                await _asyncio.sleep(0.1)
            assert inbound_log.exists(), "inbound.jsonl should be written"
            import json as _json

            last = inbound_log.read_text().splitlines()[-1]
            record = _json.loads(last)
            assert record["state"] == "completed", (
                f"expected completed terminal state, got {record['state']!r}"
            )
        finally:
            await cap.shutdown()

    _run(_scenario())
