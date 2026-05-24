"""Outbound A2A tools — ``a2a_discover`` and ``a2a_call`` (D24, D31).

Two read-only tools Gru uses to talk to A2A peers. Both follow the
spec's :class:`A2ACardResolver` pattern: clients normally discover an
agent's capabilities (its AgentCard) before sending a message. We
expose both as separate tools rather than auto-discovering on every
call — discovery itself costs a roundtrip and Gru may already know
what the peer can do from a prior turn.

Why two tools, not one with an ``auto_discover`` flag: clearer
agent-side reasoning. Gru calls ``a2a_discover`` when it wants to
inspect a peer, ``a2a_call`` when it wants to ask the peer something.
The audit trail + bus events then say exactly what happened.

Peer resolution: ``peer_or_url`` accepts either a named peer (from the
active profile's ``a2a.peers.<name>`` block OR session-scoped
``/a2a peer add`` entries) or a raw URL. Named peers get their
configured auth strategy applied automatically — bearer / api_key /
OAuth2 / etc. depending on the peer's ``auth`` block. Raw URLs go
unauthenticated (peer must be running ``--unsafe``).

We deliberately don't accept a ``token=`` kwarg — putting bearer
secrets in tool args means they end up in the model's context window
and on disk in session ``messages.json``. Peers with credentials live
either in the profile YAML (stable peers, env-var-backed secrets) or
in session-scoped in-memory state (ephemeral peers via
:meth:`A2ACapability.add_session_peer`). Either way, the credential
never crosses the agent boundary.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fasta2a.schema import (
    AgentCard,
    Message,
    SendMessageRequest,
    agent_card_ta,
    send_message_request_ta,
    send_message_response_ta,
)

from jac.capabilities.a2a.audit import make_message_preview
from jac.capabilities.a2a.auth_strategies import AuthStrategy
from jac.errors import JacConfigError
from jac.profiles import A2APeerConfig
from jac.runtime.bus import EventBus
from jac.runtime.events import A2AOutboundCall, A2AOutboundCompleted
from jac.tools import jac_tool

# Reasonable defaults for outbound calls. The discover timeout is short
# because the well-known endpoint is just a JSON file. The call timeout
# is generous because the peer may run a real model behind the request.
_DISCOVER_TIMEOUT_S = 10.0
_CALL_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class _ResolvedTarget:
    """The (url, optional peer config, display name) triple ``a2a_call`` operates on.

    Carrying the *peer config* (not the bearer token) lets the caller
    look up the right auth strategy via the capability — see
    :func:`build_outbound_tools` for how it threads through.
    """

    url: str
    peer: A2APeerConfig | None
    """The resolved peer config (with its ``auth`` block) when looked
    up by name; ``None`` when the caller passed a raw URL."""
    display: str
    """What goes into the renderer / audit events — the peer name when
    we resolved by name, or the URL when called ad-hoc."""


def resolve_target(peer_or_url: str, *, peers: dict[str, A2APeerConfig]) -> _ResolvedTarget:
    """Map ``peer_or_url`` to a concrete URL + optional peer config.

    Resolution rules:

    - If ``peer_or_url`` starts with ``http://`` or ``https://``, treat
      it as a raw URL; no auth (peer must be running ``--unsafe``).
      Display = the URL; ``peer`` is ``None``.
    - Otherwise look it up in ``peers``. Found → return the peer config
      with display = the peer name. Auth strategy lookup happens in
      ``a2a_call`` via the strategy provider passed to
      :func:`build_outbound_tools`.
    - Otherwise raise :class:`JacConfigError` with a list of configured
      peer names so the agent can re-call with a valid one.

    Args:
        peer_or_url: peer name (e.g. ``"backend-jac"``) or raw URL.
        peers: the merged ``profile.a2a.peers`` + session-scoped peers
            map (assembled by :class:`A2ACapability.peers`).

    Returns:
        Resolved target.

    Raises:
        JacConfigError: unknown peer name with no http(s):// prefix.
    """
    if peer_or_url.startswith(("http://", "https://")):
        return _ResolvedTarget(url=peer_or_url.rstrip("/"), peer=None, display=peer_or_url)

    peer = peers.get(peer_or_url)
    if peer is None:
        configured = ", ".join(sorted(peers)) if peers else "(none configured)"
        raise JacConfigError(
            f"unknown A2A peer {peer_or_url!r}. Configured peers: {configured}. "
            "Either pass a raw URL starting with http:// or https://, or add "
            f"the peer via `/a2a peer add {peer_or_url} URL ...` for this "
            f"session, or under a2a.peers.{peer_or_url} in your active profile."
        )
    return _ResolvedTarget(
        url=peer.url.rstrip("/"),
        peer=peer,
        display=peer_or_url,
    )


def build_outbound_tools(
    *,
    peers_getter: Callable[[], dict[str, A2APeerConfig]],
    strategy_provider: Callable[[A2APeerConfig], AuthStrategy | None] | None = None,
    bus: EventBus | None = None,
):
    """Build the outbound tool closures bound to runtime accessors and a bus.

    Why getters rather than concrete dicts: the REPL can swap profiles
    mid-session via ``/profile``, and the user can add/remove session
    peers via ``/a2a peer add|remove``. Capturing dicts/strategies by
    value would leave the tools stuck with the old state. The getters
    are called once per tool invocation and always return the live
    view.

    Args:
        peers_getter: zero-arg callable returning the current MERGED
            peers map (profile + session). Typically
            ``A2ACapability._current_peers``.
        strategy_provider: callable mapping a peer config to its
            :class:`AuthStrategy` (or ``None`` when the peer has no
            ``auth`` block). Typically ``A2ACapability._strategy_for``.
            The capability owns the strategy cache so OAuth2 tokens
            persist across calls in a session. When ``None`` (test /
            headless callers without a capability), strategies are
            built fresh per call via :func:`make_strategy` — fine for
            tests, wasteful in production.
        bus: optional event bus; outbound events post here when set.

    Returns:
        ``[a2a_discover, a2a_call]`` — already ``@jac_tool``-decorated.
    """
    # Default strategy provider: build a fresh strategy per call. This is
    # the fallback for callers that don't supply one (tests, headless
    # one-shot usage). The A2ACapability passes its own caching variant
    # in production so OAuth2 tokens survive across calls.
    if strategy_provider is None:
        from jac.capabilities.a2a.auth_strategies import make_strategy

        def _default_provider(peer: A2APeerConfig) -> AuthStrategy | None:
            return make_strategy(peer.auth) if peer.auth is not None else None

        strategy_provider = _default_provider

    async def _emit(event) -> None:
        if bus is not None:
            await bus.emit(event)

    @jac_tool
    async def a2a_discover(reason: str, url: str) -> dict[str, Any]:
        """Fetch and parse an A2A peer's AgentCard.

        Use this before ``a2a_call`` when you don't already know what
        the peer can do — the returned dict lists its name, skills,
        version, and auth scheme. Read-only; no approval needed.

        Args:
            reason: One-sentence justification.
            url: Peer base URL (``http://host:port``). The
                ``/.well-known/agent-card.json`` suffix is appended.

        Returns:
            Parsed AgentCard as a plain dict — same shape as the JSON on
            the wire, with the spec's camelCase field names.

        Raises:
            ValueError: HTTP error (4xx/5xx) or invalid card payload.
        """
        if not url.strip():
            raise ValueError("`url` must not be empty.")
        base = url.rstrip("/")
        discovery_url = f"{base}/.well-known/agent-card.json"

        await _emit(A2AOutboundCall(target=url, message_preview="(discover)"))
        started = time.monotonic()
        state = "completed"
        try:
            async with httpx.AsyncClient(timeout=_DISCOVER_TIMEOUT_S) as client:
                resp = await client.get(discovery_url)
            if resp.status_code >= 400:
                state = "failed"
                raise ValueError(
                    f"A2A discover failed: HTTP {resp.status_code} from {discovery_url}"
                )
            try:
                # Validate to catch schema drift; agent_card_ta accepts
                # both snake_case (field names) and camelCase (aliases).
                card: AgentCard = agent_card_ta.validate_json(resp.content)
            except Exception as exc:
                state = "failed"
                raise ValueError(f"A2A discover: peer returned malformed AgentCard: {exc}") from exc
        finally:
            await _emit(
                A2AOutboundCompleted(
                    target=url,
                    state=state,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

        # Return a plain dict (TypedDicts are runtime dicts already; cast
        # for clarity). Use the camelCase alias dump so the agent sees
        # the same field names the spec documents.
        return _card_to_dict(card)

    @jac_tool
    async def a2a_call(
        reason: str,
        peer_or_url: str,
        message: str,
        context_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to an A2A peer and return the task result.

        Use after ``a2a_discover`` (or when you already know the peer
        from a previous turn / profile config). Read-only from JAC's
        perspective — the remote side decides what *its* tools can do.

        Args:
            reason: One-sentence justification.
            peer_or_url: Named peer from ``a2a.peers`` in the active
                profile, OR a raw URL starting with ``http(s)://``.
                Named peers get their configured bearer token applied
                automatically; raw URLs go unauthenticated.
            message: The message body (plain text).
            context_id: Optional A2A context id to continue a prior
                conversation thread. Omit to start fresh; the peer
                generates a new uuid you can pass back next call to
                continue it.

        Returns:
            Parsed JSON-RPC ``result`` from the peer's ``message/send``
            response — typically a ``Task`` envelope with ``id``,
            ``status``, optional ``artifacts``, etc.

        Raises:
            JacConfigError: unknown peer name (no http:// prefix).
            ValueError: HTTP error, network failure, or JSON-RPC error
                from the peer.
        """
        if not message.strip():
            raise ValueError("`message` must not be empty.")

        peers = peers_getter()
        target = resolve_target(peer_or_url, peers=peers)

        preview = make_message_preview(message)
        await _emit(A2AOutboundCall(target=target.display, message_preview=preview))

        headers: dict[str, str] = {"Content-Type": "application/json"}
        # Apply the peer's auth strategy (bearer / api_key / oauth2 / ...).
        # Raw URLs (target.peer is None) and peers with no `auth` block
        # both skip this — the caller is on the hook for ensuring the
        # remote allows unauthenticated requests.
        if target.peer is not None and target.peer.auth is not None:
            strategy = strategy_provider(target.peer)
            if strategy is not None:
                # Auth resolution can do I/O (OAuth2 token fetch) and
                # raise JacConfigError on missing env vars or token-
                # endpoint failures. Let those propagate — they're
                # config issues the operator needs to fix, not retries
                # the model should keep attempting.
                auth_headers = await strategy.headers_for()
                headers.update(auth_headers)

        msg: Message = {
            "role": "user",
            "parts": [{"kind": "text", "text": message}],
            "kind": "message",
            "message_id": str(uuid.uuid4()),
        }
        if context_id:
            msg["context_id"] = context_id

        request: SendMessageRequest = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {"message": msg},
        }
        payload = send_message_request_ta.dump_json(request, by_alias=True)

        started = time.monotonic()
        state = "completed"
        try:
            async with httpx.AsyncClient(timeout=_CALL_TIMEOUT_S) as client:
                resp = await client.post(target.url, content=payload, headers=headers)
            if resp.status_code >= 400:
                state = "failed"
                raise ValueError(
                    f"A2A call to {target.display} failed: HTTP {resp.status_code} "
                    f"— {resp.text[:200]}"
                )
            try:
                parsed = send_message_response_ta.validate_json(resp.content)
            except Exception as exc:
                state = "failed"
                raise ValueError(f"A2A call: peer returned malformed response: {exc}") from exc

            error = parsed.get("error")
            if error is not None:
                state = "failed"
                raise ValueError(
                    f"A2A call to {target.display} returned JSON-RPC error "
                    f"{error.get('code')}: {error.get('message')}"
                )

            result = parsed.get("result")
            if result is None:
                state = "failed"
                raise ValueError(
                    f"A2A call to {target.display} returned neither result nor "
                    "error — peer is not spec-compliant."
                )
            # result is Task | Message TypedDict (runtime dict).
            return dict(result)
        finally:
            await _emit(
                A2AOutboundCompleted(
                    target=target.display,
                    state=state,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )

    return [a2a_discover, a2a_call]


def _card_to_dict(card: AgentCard) -> dict[str, Any]:
    """Re-serialize an AgentCard with camelCase keys for tool output.

    ``card`` is a TypedDict (runtime dict) with snake_case keys; the
    fasta2a TypeAdapter writes wire-format camelCase via aliases. We
    serialize-then-parse to flip the keys so what Gru sees matches the
    spec's documented field names — easier reasoning when the model
    compares against docs or another card.
    """
    import json

    return json.loads(agent_card_ta.dump_json(card, by_alias=True))
