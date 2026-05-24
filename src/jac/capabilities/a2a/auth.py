"""Bearer-token auth for the A2A guest server (D24).

We don't roll our own auth — the A2A spec lets us declare standard HTTP
schemes (``http`` with ``scheme: bearer``, ``apiKey``, ``oauth2``,
``openIdConnect``) in the AgentCard's ``securitySchemes`` and clients
send the matching header. For v1 we ship the cheapest scheme that's
still secure-by-default: a single ephemeral bearer token generated at
server start.

Two pieces:

- :func:`generate_token` — :func:`secrets.token_urlsafe` for cryptographic
  randomness. URL-safe so users can paste it into env vars without
  escaping. 32 bytes → ~43 chars; brute-forcing is not interesting.
- :class:`BearerAuthMiddleware` — Starlette ASGI middleware. Inspects
  the ``Authorization`` header on every incoming request, allows it
  through iff the bearer matches the expected token (constant-time
  compare via :func:`hmac.compare_digest`), otherwise returns a JSON
  ``401 Unauthorized``. The agent-card endpoint
  (``/.well-known/agent-card.json``) is exempt — peers must be able to
  discover us before authenticating, and the card itself is non-sensitive
  by design.

When the server runs with ``--unsafe``, this middleware is **not
installed** and the AgentCard omits ``securitySchemes`` so clients
won't try to send a (missing) bearer. The startup banner prints a loud
warning so the operator can't miss it.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# 32 bytes of entropy → 43 url-safe chars. Way beyond brute-force range.
_TOKEN_BYTES = 32

# Paths that bypass auth. The agent card MUST be reachable unauthenticated
# so peers can discover capabilities — its contents are non-sensitive by
# design (no secrets, no host paths, no internal config).
_PUBLIC_PATHS: frozenset[str] = frozenset({"/.well-known/agent-card.json"})


def generate_token() -> str:
    """Return a fresh URL-safe bearer token.

    Generated via :func:`secrets.token_urlsafe` (cryptographically
    strong, no padding chars, safe for ``Authorization`` headers).
    Ephemeral: regenerated on every server start so a leaked token
    expires the next time the operator restarts.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def redact_token(token: str) -> str:
    """Display-safe form of ``token`` — first 4 chars, ellipsis, last 4.

    Used by the status panel and the ``A2AServerStarted`` event so the
    operator sees enough to recognize their token without it appearing
    in scrollback in full. The plaintext token is only printed once at
    server startup (and via ``/a2a token`` if the operator scrolls past).
    """
    if len(token) <= 8:
        # Pathological short token (shouldn't happen — we generate 43-char).
        # Render fully redacted rather than leaking it entirely.
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def peer_id_from_token(token: str | None) -> str:
    """Short caller-identity tag derived from the inbound bearer token.

    OAuth2 / OIDC bring real identities (v2); until then we use the
    bearer's last 8 chars as a stable-but-not-secret tag for telemetry
    and audit logging. When the request hit an ``--unsafe`` server (no
    auth at all), returns ``"unsafe"`` so the audit log makes the gap
    visible.
    """
    if not token:
        return "unsafe"
    return f"peer-{token[-8:]}"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware enforcing ``Authorization: Bearer <token>``.

    Constant-time compare via :func:`hmac.compare_digest` to avoid
    timing oracle on the token (paranoid but cheap — the alternative is
    a string equality that leaks length info). Public paths (see
    :data:`_PUBLIC_PATHS`) bypass the check.
    """

    def __init__(self, app: ASGIApp, expected_token: str) -> None:
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return _unauthorized("missing or malformed Authorization header")

        if not hmac.compare_digest(value.encode("utf-8"), self._expected.encode("utf-8")):
            return _unauthorized("invalid bearer token")

        return await call_next(request)


def _unauthorized(detail: str) -> Response:
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized", "detail": detail},
        headers={"WWW-Authenticate": 'Bearer realm="jac-a2a"'},
    )
