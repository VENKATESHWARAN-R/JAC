"""Outbound A2A tools — ``a2a_discover`` and ``a2a_call`` (D24, Phase 4.b).

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
active profile's ``a2a.peers.<name>``) or a raw URL. Named peers get
their bearer token applied automatically; raw URLs go through
unauthenticated unless the peer is running ``--unsafe`` on the other
end. We deliberately don't accept a ``token=`` kwarg — putting bearer
secrets in tool args means they end up in the model's context window
and on disk in session messages.json. Peers with tokens live in the
profile.
"""

from __future__ import annotations

import time
import uuid
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
    """The (url, optional token, display name) triple ``a2a_call`` operates on."""

    url: str
    token: str | None
    display: str
    """What goes into the renderer / audit events — the peer name when
    we resolved by name, or the URL when called ad-hoc."""


def resolve_target(peer_or_url: str, *, peers: dict[str, A2APeerConfig]) -> _ResolvedTarget:
    """Map ``peer_or_url`` to a concrete URL + optional bearer.

    Resolution rules:

    - If ``peer_or_url`` starts with ``http://`` or ``https://``, treat
      it as a raw URL; no bearer token (peer must be running ``--unsafe``
      or there's no auth). Display = the URL.
    - Otherwise look it up in ``peers``. Found → return its
      ``(url, token)`` with display = the peer name.
    - Otherwise raise :class:`JacConfigError` with a list of configured
      peer names so the agent can re-call with a valid one.

    Args:
        peer_or_url: peer name (e.g. ``"backend-jac"``) or raw URL.
        peers: ``a2a.peers`` block from the active profile.

    Returns:
        Resolved target. Tool builds the auth header (if any) from
        ``target.token``.

    Raises:
        JacConfigError: unknown peer name with no http:// prefix.
    """
    if peer_or_url.startswith(("http://", "https://")):
        return _ResolvedTarget(url=peer_or_url.rstrip("/"), token=None, display=peer_or_url)

    peer = peers.get(peer_or_url)
    if peer is None:
        configured = ", ".join(sorted(peers)) if peers else "(none configured)"
        raise JacConfigError(
            f"unknown A2A peer {peer_or_url!r}. Configured peers: {configured}. "
            "Either pass a raw URL starting with http:// or https://, or add "
            f"the peer to your active profile under a2a.peers.{peer_or_url}."
        )
    return _ResolvedTarget(
        url=peer.url.rstrip("/"),
        token=peer.token,
        display=peer_or_url,
    )


def build_outbound_tools(
    *,
    peers_getter,
    bus: EventBus | None = None,
):
    """Build the outbound tool closures bound to ``peers_getter`` and ``bus``.

    Why a getter rather than the peers dict directly: the REPL can swap
    profiles mid-session via ``/profile``, which rebuilds the
    capability's ``peers`` attribute. Capturing the dict by value would
    leave the tool stuck with the old map. The getter is called once
    per tool invocation and always returns the live map.

    Args:
        peers_getter: zero-arg callable returning the current
            ``dict[str, A2APeerConfig]`` (typically a method bound to
            the :class:`A2ACapability` instance).
        bus: optional event bus; outbound events post here when set.

    Returns:
        ``[a2a_discover, a2a_call]`` — already ``@jac_tool``-decorated.
    """

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
        if target.token:
            headers["Authorization"] = f"Bearer {target.token}"

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
