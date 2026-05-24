"""Profile schema — Pydantic models for tiered profiles, A2A peers, auth.

A profile lives in ``~/.jac/config.yaml`` under ``profiles.<name>``. The
CLI's ``--profile``/``-p`` flag (or ``default_profile``) picks which one is
active for a session. Each profile defines one or more **tiers** (typically
``small`` / ``medium`` / ``large``) — each tier is an ordered list of
fully-qualified model ids; the first entry is the tier's default. Required
credentials are inferred from the union of provider prefixes across every
tier model and resolved at startup by :mod:`jac.secrets`.

Profile names must match ``[a-z0-9-]+`` so they're safe in shell args and
file paths. See docs/architecture.md §11 D13 (names) and D22 (tiered models).

YAML I/O lives in :mod:`jac.profiles_io`; CRUD operations in
:mod:`jac.profiles_crud`. This module is import-light: schema only.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, model_validator

from jac.errors import JacConfigError
from jac.providers.registry import get_provider_registry, provider_prefix

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_TIER_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


def validate_profile_name(name: str) -> None:
    """Raise :class:`JacConfigError` if ``name`` isn't a valid profile name."""
    if not _NAME_RE.fullmatch(name):
        raise JacConfigError(
            f"invalid profile name {name!r}. "
            "Use lowercase letters, digits, and hyphens only; cannot start or end with a hyphen."
        )


class BearerAuth(BaseModel):
    """Static HTTP Bearer token. Sent as ``Authorization: Bearer <token>``.

    The cheapest scheme that's still secure-by-default. Use for JAC↔JAC,
    test peers, and any service-to-service link where you've pre-shared
    a long-lived token.
    """

    type: Literal["bearer"] = "bearer"
    token: str
    """The bearer token, or a ``${ENV_VAR}`` reference resolved at apply
    time via the configured secrets backend."""


class ApiKeyAuth(BaseModel):
    """API key in a custom HTTP header. Sent as ``<header>: <value>``.

    For peers that use non-Bearer header conventions like
    ``X-API-Key: <key>``. The ``Authorization`` header is allowed as
    a value of ``header`` but is unusual — use :class:`BearerAuth` if
    the scheme is bearer.
    """

    type: Literal["api_key"] = "api_key"
    header: str
    """The HTTP header name (e.g. ``"X-API-Key"``). Case-insensitive on
    the wire but stored verbatim."""
    value: str
    """The header value, or a ``${ENV_VAR}`` reference."""


class OAuth2ClientCredentialsAuth(BaseModel):
    """OAuth2 client_credentials flow (RFC 6749 §4.4).

    Standard server-to-server flow. We POST to ``token_url`` with the
    client id/secret, receive an access token + ``expires_in``, cache
    it on the strategy instance for the rest of the session, and send
    it as ``Authorization: Bearer <access_token>`` on every A2A call.

    Used by Azure AD / Entra ID (the most common path for service-to-
    service in Microsoft cloud), Auth0, Okta API access, generic OAuth2
    deployments. For peers that need different flows
    (authorization_code, password, etc.) we'll add separate strategies
    in a follow-up — client_credentials is by far the most common for
    agent-to-agent.
    """

    type: Literal["oauth2_client_credentials"] = "oauth2_client_credentials"
    token_url: str
    """Full token endpoint URL (e.g.
    ``https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token``).
    May contain ``${ENV_VAR}`` references for tenant ids, etc."""
    client_id: str
    """OAuth2 client id (or ``${ENV_VAR}``)."""
    client_secret: str
    """OAuth2 client secret (or ``${ENV_VAR}``)."""
    scope: str = ""
    """Space-separated scope string (e.g. ``"api://my-agent/.default"``
    for Azure). Empty string omits the param."""


PeerAuth = Annotated[
    BearerAuth | ApiKeyAuth | OAuth2ClientCredentialsAuth,
    Discriminator("type"),
]
"""Discriminated union of every supported auth strategy. Add new
strategies by extending :mod:`jac.capabilities.a2a.auth_strategies`
and adding the model class here."""


class A2APeerConfig(BaseModel):
    """One configured A2A peer (D24, D31).

    Each peer is a (URL, optional auth, description) triple. Auth is a
    tagged union — :class:`BearerAuth` / :class:`ApiKeyAuth` /
    :class:`OAuth2ClientCredentialsAuth`. Omit ``auth`` entirely for
    unauthenticated peers (peer must be running with ``--unsafe``).

    Backward compatibility: the pre-D31 ``token: <str>`` shorthand is
    still accepted. The validator promotes it to ``auth: BearerAuth(...)``
    so existing configs keep working. Side-by-side ``token:`` and
    ``auth:`` is rejected — pick one.
    """

    url: str
    """Base URL of the peer's A2A endpoint (e.g. ``http://127.0.0.1:8001``).
    Trailing slash optional; the client normalizes either form."""

    auth: PeerAuth | None = None
    """How to authenticate with this peer. ``None`` means no auth —
    works only against ``--unsafe`` peers. See :class:`BearerAuth` /
    :class:`ApiKeyAuth` / :class:`OAuth2ClientCredentialsAuth`."""

    description: str = ""
    """Human-readable hint surfaced in ``/a2a peers`` listing."""

    @model_validator(mode="before")
    @classmethod
    def _promote_token_shorthand(cls, data: Any) -> Any:
        """Pre-D31 ``token: <str>`` becomes ``auth: BearerAuth(...)``.

        Backward-compat shim so existing configs keep loading. Reject
        if BOTH ``token:`` and ``auth:`` are set (ambiguous — operator
        must pick one form).
        """
        if not isinstance(data, dict):
            return data
        token = data.get("token")
        auth = data.get("auth")
        if token is not None and auth is not None:
            raise ValueError(
                "A2A peer config has both `token:` (legacy shorthand) and `auth:` blocks. "
                "Pick one — `auth:` is the documented form."
            )
        if token is not None:
            data = {**data, "auth": {"type": "bearer", "token": token}}
            data.pop("token", None)
        return data

    @property
    def token(self) -> str | None:
        """Pre-D31 compatibility — return the bearer token if auth is a
        :class:`BearerAuth`, else ``None``. Read-only; new code should
        introspect ``auth`` directly."""
        if isinstance(self.auth, BearerAuth):
            return self.auth.token
        return None


class A2AProfileConfig(BaseModel):
    """Per-profile A2A configuration (D24).

    Bundles peer definitions (used by ``a2a_call`` — Phase 4.b) and the
    server defaults Cur applies when ``/a2a serve`` or ``jac a2a serve``
    is invoked without explicit CLI flags. Every field has a safe default
    so an empty ``a2a: {}`` block is valid; even better, omit the block
    entirely and JAC uses these defaults.
    """

    peers: dict[str, A2APeerConfig] = Field(default_factory=dict)
    """Named peers ``a2a.peers.<name>: {url, token, description}``. Names
    follow the same convention as profile names (lowercase / digits / hyphens)
    so ``/a2a peers`` displays them safely. Validated on load."""

    host: str = "127.0.0.1"
    """Default bind address for ``/a2a serve``. Loopback by default — the
    headline cross-repo use case is local, and exposing on the LAN should
    be an explicit ``--host 0.0.0.0`` choice."""

    port: int = 8001
    """Default port for ``/a2a serve``. ``8001`` so it doesn't collide with
    typical dev servers on ``3000`` / ``8000``."""

    context_retention_days: int = 3
    """How long persisted A2A contexts (``<project>/.agents/a2a/contexts/``)
    are kept before the server-start cleanup pass removes them. ``0`` disables
    retention (keep forever); negative values rejected by the validator."""

    @model_validator(mode="after")
    def _validate_shape(self) -> A2AProfileConfig:
        if self.port < 1 or self.port > 65535:
            raise ValueError(f"a2a.port must be 1-65535; got {self.port}")
        if self.context_retention_days < 0:
            raise ValueError(
                f"a2a.context_retention_days must be ≥0 (0 = keep forever); "
                f"got {self.context_retention_days}"
            )
        for name in self.peers:
            if not _NAME_RE.fullmatch(name):
                raise ValueError(
                    f"a2a.peers.{name!r}: invalid peer name. Use lowercase letters, "
                    "digits, and hyphens only; cannot start or end with a hyphen."
                )
        return self


class Profile(BaseModel):
    """A named bundle of tiered models + non-secret env + (optionally) explicit secret requirements."""

    tiers: dict[str, list[str]]
    """Mapping ``tier_name -> [model_id, ...]``. Each list's first entry is that
    tier's default. Conventional tier names are ``small`` / ``medium`` / ``large``
    but any lowercase identifier works."""

    active_tier: str
    """The tier Gru uses by default on REPL start. Must be a key in ``tiers``."""

    env: dict[str, str] = Field(default_factory=dict)
    """Non-secret env vars injected when this profile activates (e.g. ``OLLAMA_BASE_URL``)."""

    requires_env: list[str] | None = None
    """If set, the explicit list of secret env vars the profile needs.
    If ``None``, JAC infers them from the union of provider prefixes across
    every tier model via the provider catalog (:mod:`jac.providers.registry`)."""

    a2a: A2AProfileConfig = Field(default_factory=A2AProfileConfig)
    """A2A subsystem config (D24) — peers + server defaults. Optional; the
    defaults are usable as-is so omitting the block in YAML is fine."""

    @model_validator(mode="after")
    def _validate_shape(self) -> Profile:
        if not self.tiers:
            raise ValueError("profile must define at least one tier")
        for tier_name, models in self.tiers.items():
            if not _TIER_NAME_RE.fullmatch(tier_name):
                raise ValueError(
                    f"invalid tier name {tier_name!r}; use lowercase letters, digits, "
                    "underscores, and hyphens (cannot start or end with a separator)"
                )
            if not models:
                raise ValueError(f"tier {tier_name!r} must list at least one model")
            for model in models:
                if not isinstance(model, str) or not model.strip():
                    raise ValueError(f"tier {tier_name!r} contains an empty or non-string model id")
        if self.active_tier not in self.tiers:
            raise ValueError(
                f"active_tier {self.active_tier!r} is not defined in tiers "
                f"(have: {', '.join(sorted(self.tiers))})"
            )
        return self

    def default_model(self) -> str:
        """The active tier's default model — used as ``JAC_MODEL`` on activation."""
        return self.tiers[self.active_tier][0]

    def tier(self, name: str) -> list[str]:
        """Return the model list for ``name``, or raise :class:`JacConfigError`."""
        if name not in self.tiers:
            raise JacConfigError(
                f"profile has no tier {name!r}; available: {', '.join(sorted(self.tiers))}"
            )
        return list(self.tiers[name])

    def all_models(self) -> list[str]:
        """Every model id mentioned across all tiers, preserving first-seen order."""
        seen: list[str] = []
        for models in self.tiers.values():
            for m in models:
                if m not in seen:
                    seen.append(m)
        return seen

    def required_env_keys(self) -> list[str]:
        """Union of secret env vars required by every tier model."""
        if self.requires_env is not None:
            return list(self.requires_env)
        registry = get_provider_registry()
        seen: list[str] = []
        for model in self.all_models():
            for key in registry.required_env_for_prefix(provider_prefix(model)):
                if key not in seen:
                    seen.append(key)
        return seen
