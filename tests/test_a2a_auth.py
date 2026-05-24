"""Tests for jac.capabilities.a2a.auth.

Two surfaces:

- pure helpers (``generate_token`` / ``redact_token`` / ``peer_id_from_token``)
  — fast unit tests, no I/O
- :class:`BearerAuthMiddleware` — exercised by mounting it on a toy
  Starlette app and driving it with the in-process httpx test client
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from jac.capabilities.a2a.auth import (
    BearerAuthMiddleware,
    generate_token,
    peer_id_from_token,
    redact_token,
)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ---------- pure helpers ----------


def test_generate_token_is_url_safe_and_long_enough():
    t = generate_token()
    # 32 bytes urlsafe-base64 → 43 chars, no padding
    assert len(t) >= 40
    # url-safe: no =, +, /
    assert "=" not in t and "+" not in t and "/" not in t


def test_generate_token_is_unique():
    # Probabilistically — collision odds for 256-bit random are absurd.
    assert generate_token() != generate_token()


def test_redact_token_short_token_fully_masked():
    assert redact_token("abc") == "***"


def test_redact_token_long_token_shows_ends():
    out = redact_token("abcdefghijklmnop")
    assert out.startswith("abcd")
    assert out.endswith("mnop")
    assert "…" in out
    # Middle is hidden — the redacted form is shorter than the original
    assert len(out) < len("abcdefghijklmnop")


def test_peer_id_from_token_uses_token_suffix():
    assert peer_id_from_token("abcdefghijklmnop") == "peer-ijklmnop"


def test_peer_id_from_token_marks_unsafe_when_missing():
    assert peer_id_from_token(None) == "unsafe"
    assert peer_id_from_token("") == "unsafe"


# ---------- BearerAuthMiddleware ----------


@pytest.fixture
def app_factory():
    """Build a tiny Starlette app gated by ``BearerAuthMiddleware``."""

    def _build(expected_token: str) -> Starlette:
        async def root(request):
            return PlainTextResponse("ok")

        async def card(request):
            return JSONResponse({"name": "jac-test"})

        return Starlette(
            middleware=[Middleware(BearerAuthMiddleware, expected_token=expected_token)],
            routes=[
                Route("/", root),
                Route("/.well-known/agent-card.json", card),
            ],
        )

    return _build


async def _get(app: Starlette, path: str, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


def test_middleware_rejects_missing_authorization(app_factory):
    app = app_factory("supersecret")
    resp = _run(_get(app, "/"))
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"
    # WWW-Authenticate set so clients know the challenge scheme
    assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_middleware_rejects_wrong_scheme(app_factory):
    app = app_factory("supersecret")
    resp = _run(_get(app, "/", {"Authorization": "Basic dXNlcjpwYXNz"}))
    assert resp.status_code == 401


def test_middleware_rejects_wrong_bearer(app_factory):
    app = app_factory("supersecret")
    resp = _run(_get(app, "/", {"Authorization": "Bearer wrong"}))
    assert resp.status_code == 401


def test_middleware_allows_valid_bearer(app_factory):
    app = app_factory("supersecret")
    resp = _run(_get(app, "/", {"Authorization": "Bearer supersecret"}))
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_middleware_bypasses_agent_card_endpoint(app_factory):
    """Agent card MUST be reachable unauthenticated — peers discover before auth."""
    app = app_factory("supersecret")
    resp = _run(_get(app, "/.well-known/agent-card.json"))
    assert resp.status_code == 200
    assert resp.json()["name"] == "jac-test"
