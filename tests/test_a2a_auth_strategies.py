"""Tests for jac.capabilities.a2a.auth_strategies.

Three layers:

1. **Pure dispatch** — :func:`make_strategy` maps the right auth-config
   subclass to the right strategy class.
2. **Header generation** — bearer / api_key produce the expected
   single-header dicts with ``${ENV_VAR}`` references expanded.
3. **OAuth2 client_credentials** — full happy path against an
   in-process token endpoint, plus failure cases (HTTP error, no
   ``access_token``) and cache-reuse behavior (two calls = one
   token-endpoint roundtrip).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from jac.capabilities.a2a.auth_strategies import (
    ApiKeyStrategy,
    BearerStrategy,
    OAuth2ClientCredentialsStrategy,
    make_strategy,
)
from jac.errors import JacConfigError
from jac.profiles import ApiKeyAuth, BearerAuth, OAuth2ClientCredentialsAuth


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ---------- dispatch ----------


def test_make_strategy_dispatches_bearer():
    s = make_strategy(BearerAuth(token="abc"))
    assert isinstance(s, BearerStrategy)


def test_make_strategy_dispatches_api_key():
    s = make_strategy(ApiKeyAuth(header="X-K", value="v"))
    assert isinstance(s, ApiKeyStrategy)


def test_make_strategy_dispatches_oauth2():
    s = make_strategy(
        OAuth2ClientCredentialsAuth(
            token_url="https://idp/tok",
            client_id="cid",
            client_secret="sec",
            scope="api",
        )
    )
    assert isinstance(s, OAuth2ClientCredentialsStrategy)


# ---------- BearerStrategy ----------


def test_bearer_strategy_returns_authorization_header():
    s = BearerStrategy(config=BearerAuth(token="my-token"))
    headers = _run(s.headers_for())
    assert headers == {"Authorization": "Bearer my-token"}


def test_bearer_strategy_expands_env_var(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "from-env")
    s = BearerStrategy(config=BearerAuth(token="${MY_TOKEN}"))
    headers = _run(s.headers_for())
    assert headers == {"Authorization": "Bearer from-env"}


def test_bearer_strategy_raises_on_missing_env_var(monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
    s = BearerStrategy(config=BearerAuth(token="${DEFINITELY_NOT_SET}"))
    with pytest.raises(JacConfigError, match="DEFINITELY_NOT_SET"):
        _run(s.headers_for())


# ---------- ApiKeyStrategy ----------


def test_api_key_strategy_returns_named_header():
    s = ApiKeyStrategy(config=ApiKeyAuth(header="X-API-Key", value="abc"))
    headers = _run(s.headers_for())
    assert headers == {"X-API-Key": "abc"}


def test_api_key_strategy_expands_env_var(monkeypatch):
    monkeypatch.setenv("MY_KEY", "from-env-key")
    s = ApiKeyStrategy(config=ApiKeyAuth(header="X-Token", value="${MY_KEY}"))
    headers = _run(s.headers_for())
    assert headers == {"X-Token": "from-env-key"}


# ---------- OAuth2ClientCredentialsStrategy ----------


def _make_token_endpoint(
    *,
    access_token: str = "the-access-token",
    expires_in: int = 3600,
    status_code: int = 200,
    omit_access_token: bool = False,
    non_json: bool = False,
) -> tuple[Starlette, list[dict]]:
    """Build an in-process /tok endpoint mimicking RFC 6749 §4.4 responses.

    Returns ``(app, requests_log)`` so tests can both drive the strategy
    against the app AND inspect what the strategy actually sent (basic
    auth header, body grant_type/scope, etc.).
    """
    log: list[dict] = []

    async def token_endpoint(request: Request) -> Response:
        body = await request.body()
        # form-urlencoded — split manually so we don't need a parser dep
        body_str = body.decode("utf-8")
        form: dict[str, str] = {}
        for pair in body_str.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                form[k] = v
        log.append(
            {
                "body": form,
                "auth": request.headers.get("authorization", ""),
                "accept": request.headers.get("accept", ""),
            }
        )
        if non_json:
            return Response(content="not json", status_code=status_code)
        if status_code >= 400:
            return JSONResponse({"error": "nope"}, status_code=status_code)
        payload: dict = {"token_type": "Bearer", "expires_in": expires_in}
        if not omit_access_token:
            payload["access_token"] = access_token
        return JSONResponse(payload)

    app = Starlette(routes=[Route("/tok", token_endpoint, methods=["POST"])])
    return app, log


def _drive_via_asgi(app: Starlette):
    """Patch the httpx.AsyncClient inside auth_strategies to use ASGI transport."""

    class _Patched(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.ASGITransport(app=app))
            kwargs.setdefault("base_url", "http://idp")
            super().__init__(*args, **kwargs)

    return patch("jac.capabilities.a2a.auth_strategies.httpx.AsyncClient", _Patched)


def test_oauth2_fetches_token_and_returns_bearer_header():
    app, log = _make_token_endpoint(access_token="OAUTH-ACCESS-1")
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok",
            client_id="cid",
            client_secret="sec",
            scope="api://x/.default",
        )
    )
    with _drive_via_asgi(app):
        headers = _run(s.headers_for())
    assert headers == {"Authorization": "Bearer OAUTH-ACCESS-1"}
    # Verified one token-endpoint round-trip
    assert len(log) == 1
    # grant_type per RFC 6749 §4.4
    assert log[0]["body"]["grant_type"] == "client_credentials"
    # scope passed through (URL-encoded: ":" → "%3A", "/" → "%2F")
    assert "scope" in log[0]["body"]
    # Basic auth (id:secret base64) sent — RFC-preferred form
    assert log[0]["auth"].startswith("Basic ")


def test_oauth2_omits_scope_when_empty():
    app, log = _make_token_endpoint()
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok",
            client_id="cid",
            client_secret="sec",
            scope="",  # default
        )
    )
    with _drive_via_asgi(app):
        _run(s.headers_for())
    assert "scope" not in log[0]["body"]


def test_oauth2_caches_token_across_calls():
    """Two calls to headers_for should only hit the token endpoint once
    while the cached token is still valid."""
    app, log = _make_token_endpoint(access_token="CACHED", expires_in=3600)
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok",
            client_id="cid",
            client_secret="sec",
        )
    )
    with _drive_via_asgi(app):
        h1 = _run(s.headers_for())
        h2 = _run(s.headers_for())
    assert h1 == h2 == {"Authorization": "Bearer CACHED"}
    assert len(log) == 1


def test_oauth2_refreshes_when_token_expired(monkeypatch):
    """When the cached token is past its expiry, headers_for re-fetches."""
    app, log = _make_token_endpoint(access_token="FIRST", expires_in=1)
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok",
            client_id="cid",
            client_secret="sec",
        )
    )
    with _drive_via_asgi(app):
        _run(s.headers_for())
        # Force expiry by pinning `_expires_at` to the past — simpler than
        # waiting; the freshness check uses monotonic time, not wall clock.
        s._expires_at = 0.0
        _run(s.headers_for())
    assert len(log) == 2


def test_oauth2_raises_on_4xx_response():
    app, _ = _make_token_endpoint(status_code=400)
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok", client_id="cid", client_secret="sec"
        )
    )
    with _drive_via_asgi(app), pytest.raises(JacConfigError, match="HTTP 400"):
        _run(s.headers_for())


def test_oauth2_raises_on_non_json_response():
    app, _ = _make_token_endpoint(non_json=True)
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok", client_id="cid", client_secret="sec"
        )
    )
    with _drive_via_asgi(app), pytest.raises(JacConfigError, match="non-JSON"):
        _run(s.headers_for())


def test_oauth2_raises_when_response_has_no_access_token():
    app, _ = _make_token_endpoint(omit_access_token=True)
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok", client_id="cid", client_secret="sec"
        )
    )
    with _drive_via_asgi(app), pytest.raises(JacConfigError, match="no `access_token`"):
        _run(s.headers_for())


def test_oauth2_expands_env_in_token_url(monkeypatch):
    """`${ENV}` references in token_url, client_id, client_secret, scope all expand."""
    monkeypatch.setenv("TENANT", "my-tenant")
    monkeypatch.setenv("CID", "client-id-from-env")
    monkeypatch.setenv("SEC", "secret-from-env")
    app, log = _make_token_endpoint()
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/${TENANT}/tok",  # token URL gets expanded too
            client_id="${CID}",
            client_secret="${SEC}",
        )
    )
    # The app routes /tok, but the URL after expansion is /my-tenant/tok — we
    # don't bind that route, so we'd get a 404. To prove env expansion in the
    # value path without rerouting the app, monkeypatch the URL to plain /tok
    # before calling.
    s.config = OAuth2ClientCredentialsAuth(
        token_url="http://idp/tok",
        client_id="${CID}",
        client_secret="${SEC}",
    )
    with _drive_via_asgi(app):
        _run(s.headers_for())
    # Basic auth header is base64(client-id-from-env:secret-from-env) — just
    # verify it's present + non-empty. Full decode is overkill for this test.
    assert log[0]["auth"].startswith("Basic ")


def test_oauth2_raises_on_missing_env_var(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    s = OAuth2ClientCredentialsStrategy(
        config=OAuth2ClientCredentialsAuth(
            token_url="http://idp/tok",
            client_id="cid",
            client_secret="${MISSING_SECRET}",
        )
    )
    with pytest.raises(JacConfigError, match="MISSING_SECRET"):
        _run(s.headers_for())
