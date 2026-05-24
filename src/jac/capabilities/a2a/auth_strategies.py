"""Pluggable outbound auth strategies for A2A peers (D31).

Each strategy maps an :class:`A2APeerConfig.auth` block to the HTTP
headers to send on the next outbound request. The dispatcher in
``client.py`` looks up the right strategy at call time, asks it for
headers, and merges them into the httpx request.

Today's strategies:

- :class:`BearerStrategy` — static ``Authorization: Bearer <token>``.
  No state, no I/O. Just returns the configured token.
- :class:`ApiKeyStrategy` — arbitrary header name + value
  (``X-API-Key`` and friends). Same shape: no state, no I/O.
- :class:`OAuth2ClientCredentialsStrategy` — RFC 6749 §4.4 flow:
  POST ``token_url`` with client id/secret + scope, get back an
  ``access_token`` and ``expires_in``, cache (per peer) for the rest
  of the session, send as bearer. Refresh lazily on next call when
  expired.

Adding a new strategy:

1. Add a config model in :mod:`jac.profiles` (e.g. ``OidcAuth``) and
   include it in the ``PeerAuth`` discriminated union.
2. Add a strategy class here implementing :class:`AuthStrategy`.
3. Wire it into :func:`make_strategy` (the dispatch).
4. Document the new ``type:`` in ``gru_system.md`` and the auth
   section of the user guide.

**Token caching is per strategy instance, not global.** The
:class:`A2ACapability` builds one strategy per peer on demand (cached
on the capability so per-peer state survives across calls in a
session) — see :meth:`A2ACapability._strategy_for`. That means an
OAuth2 client_credentials token is shared across every call to the
same peer in a session, but never crosses peer boundaries or
sessions.

**Secrets resolution.** ``${ENV_VAR}`` references in config values are
expanded here, at strategy build time, via :func:`os.environ.get`.
Missing env vars raise :class:`JacConfigError` with the variable name
in the message — fail-first, no silent fallback. We deliberately do
NOT recurse through the secrets backend on every call (that would
re-prompt the OS keychain on each request). The REPL's startup
``resolve_optional_keys`` pulls relevant keys into ``os.environ``
once; everything after reads from there.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from jac.errors import JacConfigError
from jac.profiles import ApiKeyAuth, BearerAuth, OAuth2ClientCredentialsAuth, PeerAuth

# Matches ${NAME} where NAME is alnum + underscore. Anchored to allow
# embedded references like "https://login/${TENANT}/oauth2/v2.0/token".
_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# Reasonable defaults for the OAuth2 token fetch. Token endpoints
# usually respond fast; we don't need the 60s outbound-call budget.
_TOKEN_FETCH_TIMEOUT_S = 15.0
# How early to refresh before the published expires_at (give us a few
# seconds of slack so we don't race a clock-skewed expiry).
_REFRESH_SLACK_S = 30.0


# ---------- Strategy Protocol ----------


class AuthStrategy(Protocol):
    """A pluggable strategy for setting outbound auth headers."""

    async def headers_for(self) -> dict[str, str]:
        """Return the headers to merge into the next request.

        Most strategies return a one-key dict (e.g.
        ``{"Authorization": "Bearer ..."}``). Implementations may do
        I/O (OAuth2 token fetch) and should cache as appropriate;
        callers will invoke this on every request.
        """
        ...


# ---------- Built-in strategies ----------


@dataclass
class BearerStrategy:
    """Static HTTP Bearer. No I/O, no cache."""

    config: BearerAuth

    async def headers_for(self) -> dict[str, str]:
        token = _resolve_env(self.config.token, field="bearer.token")
        return {"Authorization": f"Bearer {token}"}


@dataclass
class ApiKeyStrategy:
    """API key in a custom header. No I/O, no cache."""

    config: ApiKeyAuth

    async def headers_for(self) -> dict[str, str]:
        value = _resolve_env(self.config.value, field="api_key.value")
        return {self.config.header: value}


@dataclass
class OAuth2ClientCredentialsStrategy:
    """RFC 6749 §4.4 with a per-instance in-memory token cache.

    The cache lives on this dataclass instance, which the
    :class:`A2ACapability` caches per peer. So every call to the same
    peer reuses the same access token until it expires; calls to
    different peers (even with the same IDP) don't share state.
    """

    config: OAuth2ClientCredentialsAuth
    _access_token: str | None = field(default=None, init=False, repr=False)
    _expires_at: float = field(default=0.0, init=False, repr=False)

    async def headers_for(self) -> dict[str, str]:
        now = time.monotonic()
        if self._access_token is None or now >= (self._expires_at - _REFRESH_SLACK_S):
            await self._refresh()
        assert self._access_token is not None  # set by _refresh
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _refresh(self) -> None:
        token_url = _resolve_env(self.config.token_url, field="oauth2.token_url")
        client_id = _resolve_env(self.config.client_id, field="oauth2.client_id")
        client_secret = _resolve_env(self.config.client_secret, field="oauth2.client_secret")

        body: dict[str, str] = {"grant_type": "client_credentials"}
        if self.config.scope:
            body["scope"] = _resolve_env(self.config.scope, field="oauth2.scope")

        async with httpx.AsyncClient(timeout=_TOKEN_FETCH_TIMEOUT_S) as client:
            # HTTP Basic with id+secret is the RFC-preferred form (more
            # interoperable than including them in the body; some IDPs
            # accept only this shape).
            resp = await client.post(
                token_url,
                data=body,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
        if resp.status_code >= 400:
            raise JacConfigError(
                f"OAuth2 token endpoint at {token_url} returned "
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise JacConfigError(
                f"OAuth2 token endpoint at {token_url} returned non-JSON body"
            ) from exc

        access = payload.get("access_token")
        if not isinstance(access, str) or not access:
            raise JacConfigError(f"OAuth2 token endpoint at {token_url} returned no `access_token`")
        # ``expires_in`` is seconds-from-now per RFC; many providers
        # default to 3600. Treat missing as "short-lived; refresh
        # aggressively" (5min) rather than assuming the spec default.
        expires_in = int(payload.get("expires_in", 300))

        self._access_token = access
        self._expires_at = time.monotonic() + expires_in


# ---------- Dispatcher ----------


def make_strategy(auth: PeerAuth) -> AuthStrategy:
    """Build the strategy for a peer's ``auth:`` block.

    Args:
        auth: Discriminated union member from
            :class:`jac.profiles.A2APeerConfig.auth`.

    Returns:
        The matching :class:`AuthStrategy` instance.

    Raises:
        JacConfigError: if the auth type isn't registered. Shouldn't
            fire in practice because pydantic discriminates on
            ``type:`` at validation time, but defensive.
    """
    if isinstance(auth, BearerAuth):
        return BearerStrategy(config=auth)
    if isinstance(auth, ApiKeyAuth):
        return ApiKeyStrategy(config=auth)
    if isinstance(auth, OAuth2ClientCredentialsAuth):
        return OAuth2ClientCredentialsStrategy(config=auth)
    raise JacConfigError(  # pragma: no cover - defensive
        f"no auth strategy for auth.type={type(auth).__name__}"
    )


# ---------- Internals ----------


def _resolve_env(value: str, *, field: str) -> str:
    """Expand ``${ENV_VAR}`` references in ``value`` via ``os.environ``.

    Args:
        value: Config string, may contain zero or more ``${NAME}``.
        field: Dotted name of the config field for error messages
            (e.g. ``"oauth2.client_secret"``).

    Returns:
        ``value`` with every reference expanded.

    Raises:
        JacConfigError: if any referenced env var is missing. The
            message lists every missing var so the operator can fix
            them in one pass.
    """
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        v = os.environ.get(name)
        if v is None:
            missing.append(name)
            return match.group(0)  # placeholder; we'll raise below
        return v

    expanded = _ENV_VAR_RE.sub(_sub, value)
    if missing:
        raise JacConfigError(
            f"A2A peer auth `{field}` references missing env var(s): "
            f"{', '.join(sorted(set(missing)))}. "
            f"Set them in your shell, in `.env`, or store via `jac keys set NAME`."
        )
    return expanded
