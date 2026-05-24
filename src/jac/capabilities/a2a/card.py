"""AgentCard auto-generation for the A2A guest server (D24).

The A2A spec requires every server to expose an ``AgentCard`` at
``/.well-known/agent-card.json``. fasta2a auto-mounts the route — we
just supply the content. The card lists who we are (``name``,
``description``, ``url``, ``version``), what we can do (``skills``),
how to talk to us (``capabilities`` — streaming/push/etc.), and how
to authenticate (``securitySchemes``).

For v1 we ship a **single generic skill** rather than enumerating
tools. The A2A ``Skill`` schema was designed for human-curated
capabilities ("answer recipe questions", "process invoices") — peers
don't want to see ``read_file`` / ``grep`` / ``glob`` as discoverable
skills. Once Phase 3 (community skill loader, D21) ships, Phase 4.1
auto-publishes every loaded inline-mode skill as a real A2A skill.

When the server runs with ``--unsafe`` we **omit** ``securitySchemes``
so clients don't try to send a bearer header the server isn't going
to accept anyway — and so the gap is visible in discovery.

Streaming/push not supported in v1 (fasta2a 0.6.1 raises
``NotImplementedError`` on ``message/stream``). Card always declares
``streaming: false`` and ``push_notifications: false`` honestly.
"""

from __future__ import annotations

from typing import Any

from fasta2a.schema import AgentCapabilities, AgentCard, HttpSecurityScheme, Skill

from jac import __version__

_GENERIC_SKILL_ID = "jac-coding-assistant"


def build_agent_card(
    *,
    profile_name: str | None,
    base_url: str,
    unsafe: bool,
    description: str | None = None,
) -> AgentCard:
    """Build the AgentCard shape fasta2a expects.

    Args:
        profile_name: active profile name; used as part of ``name`` so
            peers can tell ``jac-claude`` apart from ``jac-gateway``.
            ``None`` (raw ``--model`` REPL session) → ``jac``.
        base_url: where the server is bound (``http://host:port`` — no
            trailing slash). Goes into ``AgentCard.url``.
        unsafe: when ``True``, omit ``securitySchemes`` so peers know
            no bearer is required (matches the actual middleware state).
        description: optional human-readable override; defaults to a
            generic blurb pointing at the active profile.

    Returns:
        ``AgentCard`` TypedDict ready for fasta2a's ``FastA2A``
        constructor or its agent-card endpoint serializer.
    """
    name = f"jac-{profile_name}" if profile_name else "jac"
    desc = description or (
        f"JAC guest agent running profile {profile_name!r}. "
        "Read-only coworker for the project this JAC instance is hosting; "
        "answers questions about the codebase via filesystem reads + search."
        if profile_name
        else "JAC guest agent. Read-only coworker for the project this JAC instance is hosting."
    )

    # TypedDict keys are snake_case (the FIELD names); pydantic dumps with
    # camelCase on the wire via alias_generator=to_camel. Use snake_case here
    # so validation (which prefers field names) accepts what we produce.
    card: AgentCard = {
        "name": name,
        "description": desc,
        "url": base_url,
        "version": __version__,
        # fasta2a 0.6.1 hardcodes 0.3.0 — we match so peers see consistent
        # protocol_version regardless of which endpoint they query.
        "protocol_version": "0.3.0",
        "capabilities": AgentCapabilities(
            streaming=False,
            push_notifications=False,
            state_transition_history=False,
        ),
        "skills": [_generic_skill(profile_name)],
        "default_input_modes": ["application/json", "text/plain"],
        "default_output_modes": ["application/json", "text/plain"],
    }

    if not unsafe:
        bearer_scheme: HttpSecurityScheme = {
            "type": "http",
            "scheme": "bearer",
            "description": "Ephemeral token printed at server start; rotated on restart.",
        }
        card["security_schemes"] = {"bearer": bearer_scheme}
        card["security"] = [{"bearer": []}]

    return card


def _generic_skill(profile_name: str | None) -> Skill:
    """Single generic skill — Phase 4.1 will replace this with community skills."""
    label = profile_name or "default"
    return {
        "id": _GENERIC_SKILL_ID,
        "name": "Coding assistant",
        "description": (
            f"Read-only coworker. Answers questions about the project this JAC instance is "
            f"running in (profile: {label}). Capabilities: read files, list directories, "
            "grep, glob pattern matching. No writes, no shell, no web access — strictly "
            "read-only by design."
        ),
        "tags": ["coding", "read-only", "code-search", "code-understanding"],
        "examples": [
            "What does the auth module do?",
            "Find every place that calls `process_payment`.",
            "Summarize the structure of `src/`.",
        ],
        "input_modes": ["application/json", "text/plain"],
        "output_modes": ["application/json", "text/plain"],
    }


def card_to_summary(card: AgentCard) -> dict[str, Any]:
    """Human-readable summary for the ``/a2a status`` slash output.

    Pulls just the fields a human cares about — name, url, protocol,
    streaming?, # skills, auth advertised? — for inline rendering. Not
    used on the wire; the wire format is whatever fasta2a serializes.
    """
    caps = card.get("capabilities") or {}
    return {
        "name": card["name"],
        "url": card["url"],
        "version": card["version"],
        "protocol_version": card["protocol_version"],
        "streaming": bool(caps.get("streaming")),
        "skills": [s["id"] for s in card.get("skills", [])],
        "auth": "bearer" if card.get("security_schemes") else "none",
    }
