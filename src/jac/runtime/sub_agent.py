"""Sub-agent runtime (Phase B).

The main agent delegates context-heavy work to an isolated sub-agent via
the ``spawn_sub_agent`` tool. The sub-agent runs in its *own* Agent loop
with its *own* message history, so the intermediate 50k-200k tokens of
file reads, shell output, web fetches, etc. stay in the sub-agent's
context — only the final result returns to the main agent.

Design: see ``docs/design/cost-efficient-orchestration.md`` §4.

Key invariants enforced here:

- **Depth cap = 1** — sub-agents do not get the ``spawn_sub_agent`` tool
  in their own toolset. Enforced *structurally* at construction (D40),
  not via runtime check.
- **Tier resolution cascades up only** — request ``small``; if the active
  profile has no ``small`` tier, fall back to ``medium``, then ``large``.
  Never cascade down (would silently exceed budget).
- **Approval-gated** — every spawn surfaces a HITL prompt with the
  resolved tier, tool allowlist, and packet details (D39).

The tool itself (``spawn_sub_agent``) lives at module bottom.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation

from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.tools import jac_tool

# ---------- models ----------

TierName = Literal["small", "medium", "large"]
"""Conventional tier names. Profile schema allows any lowercase identifier,
but the sub-agent tool exposes only these three — they're the cognitive
budget knobs the main agent reasons about."""

_TIER_CASCADE: dict[str, list[str]] = {
    "small": ["small", "medium", "large"],
    "medium": ["medium", "large"],
    "large": ["large"],
}
"""Cascade order: requested tier first, then strictly *larger* tiers as
fallback. Never cascades downward — that would silently exceed budget."""

ExitStatus = Literal["ok", "max_turns", "error"]


class SubAgentTaskPacket(BaseModel):
    """The full briefing the main agent gives a sub-agent (D36).

    Every field exists to constrain the sub-agent's behavior so the main
    agent can predict the result shape. The packet is rendered into the
    sub-agent's system prompt; together with the active capabilities it
    is *all* the context the sub-agent receives — no message history
    inheritance.
    """

    objective: str
    """Single-sentence statement of what success looks like."""

    success_criteria: list[str] = Field(default_factory=list)
    """Checklist the sub-agent should be able to mark complete."""

    relevant_paths: list[str] = Field(default_factory=list)
    """Files / directories the sub-agent should focus on. Advisory, not
    a sandbox — the filesystem capability still allows reads anywhere."""

    forbidden_actions: list[str] = Field(default_factory=list)
    """Explicit "don't do this" list. Surfaced verbatim in the prompt."""

    expected_output: str = ""
    """Shape of the answer the main agent expects back (e.g. "3-paragraph
    summary"). Helps the sub-agent stop talking once the goal is met."""

    allowed_tools: list[str] | None = None
    """Tool name allowlist. ``None`` means "all default sub-agent tools"
    (which is the main toolset minus ``spawn_sub_agent``)."""

    max_turns: int = 10
    """Hard cap on the sub-agent's model-call count. Prevents runaway
    loops; returns ``exit_status=max_turns`` when hit."""


class SubAgentResult(BaseModel):
    """Returned by ``spawn_sub_agent`` to the main agent.

    Kept small on purpose: the main agent's context shouldn't bloat with
    the sub-agent's intermediate work. If the caller needs detail, the
    Logfire span has it.
    """

    output: str
    """The sub-agent's final response, as a string."""

    turns_used: int
    """Number of model requests the sub-agent made."""

    resolved_tier: str
    """The tier actually used (after cascade)."""

    resolved_model: str
    """The model id actually used."""

    exit_status: ExitStatus = "ok"


# ---------- tier resolution ----------


@dataclass(frozen=True)
class _ResolvedTier:
    requested: str
    resolved: str
    model: str
    cascaded: bool

    @property
    def cascade_note(self) -> str | None:
        if not self.cascaded:
            return None
        return f"requested {self.requested!r}, cascaded up to {self.resolved!r}"


def resolve_tier(profile: Profile, requested: str) -> _ResolvedTier:
    """Pick the cheapest available tier ≥ ``requested`` from ``profile``.

    Cascades upward through :data:`_TIER_CASCADE`. Raises
    :class:`JacConfigError` when neither the requested tier nor any
    upward fallback exists — the main agent gets a structured error it
    can show the user.
    """
    candidates = _TIER_CASCADE.get(requested)
    if candidates is None:
        raise JacConfigError(
            f"unknown sub-agent tier {requested!r}; valid tiers: small, medium, large"
        )
    for candidate in candidates:
        if profile.tiers.get(candidate):
            return _ResolvedTier(
                requested=requested,
                resolved=candidate,
                model=profile.tiers[candidate][0],
                cascaded=(candidate != requested),
            )
    raise JacConfigError(
        f"no tier ≥ {requested!r} configured on the active profile "
        f"(have: {', '.join(sorted(profile.tiers)) or '<none>'}). "
        "Add a tier to ~/.jac/config.yaml or pick a different tier."
    )


# ---------- capability + factory ----------


@dataclass
class SubAgentCapability:
    """Holds the bits needed to build a sub-agent Agent on demand.

    Not a Pydantic AI ``AbstractCapability`` — it isn't registered on
    the main agent's capability list. It's a factory the
    ``spawn_sub_agent`` tool reaches through a module-level setter.
    """

    profile: Profile
    """Active profile, source of tier → model mapping."""

    base_prompt: str
    """The shipped ``sub_agent_system.md`` body, loaded once at setup."""

    capability_factory: Any
    """Callable returning the list of capabilities a sub-agent gets.
    Default = main agent's capabilities minus the spawn tool itself.
    Provided by the REPL at setup so we don't import upward."""


# Module-level singleton — set once at REPL session start by
# ``set_sub_agent_capability``. Mirrors the pattern used by
# ``set_summarizer_model`` in ``tool_summarize`` — keeps the tool
# implementation decoupled from the construction site.
_capability: SubAgentCapability | None = None


def set_sub_agent_capability(cap: SubAgentCapability | None) -> None:
    """Install the active sub-agent factory. ``None`` disables spawning."""
    global _capability
    _capability = cap


def get_sub_agent_capability() -> SubAgentCapability | None:
    """Return the active sub-agent factory, or ``None`` if disabled."""
    return _capability


# ---------- packet → prompt rendering ----------


def _render_packet(packet: SubAgentTaskPacket, base_prompt: str) -> str:
    """Compose the sub-agent's system instructions from base + packet.

    Stable across calls (no clocks, no random ids) — the sub-agent runs
    short-lived so prompt caching is less critical than for Gru, but
    keeping it stable costs nothing.
    """
    sections: list[str] = [base_prompt.strip(), "", "---", "", "# Task packet"]

    sections.append(f"\n## Objective\n\n{packet.objective}")
    if packet.success_criteria:
        bullets = "\n".join(f"- {c}" for c in packet.success_criteria)
        sections.append(f"\n## Success criteria\n\n{bullets}")
    if packet.relevant_paths:
        bullets = "\n".join(f"- `{p}`" for p in packet.relevant_paths)
        sections.append(f"\n## Relevant paths\n\n{bullets}")
    if packet.forbidden_actions:
        bullets = "\n".join(f"- {a}" for a in packet.forbidden_actions)
        sections.append(f"\n## Forbidden actions\n\n{bullets}")
    if packet.expected_output:
        sections.append(f"\n## Expected output shape\n\n{packet.expected_output}")
    sections.append(f"\n## Budget\n\nYou have at most {packet.max_turns} model calls.")
    return "\n".join(sections)


# ---------- the spawn implementation ----------


async def _run_sub_agent(
    cap: SubAgentCapability,
    packet: SubAgentTaskPacket,
    resolved: _ResolvedTier,
) -> SubAgentResult:
    """Build and run a sub-agent. Internal — wrapped by ``spawn_sub_agent``."""
    capabilities = list(cap.capability_factory(packet.allowed_tools))
    # Always attach Instrumentation so spans nest under the spawn span.
    capabilities.insert(0, Instrumentation())

    instructions = _render_packet(packet, cap.base_prompt)
    sub_agent: Agent[None, str] = Agent(
        resolved.model,
        instructions=instructions,
        capabilities=capabilities,
    )

    # Logfire span: parent of every model request the sub-agent makes.
    truncated_objective = packet.objective[:100]
    with logfire.span(
        "spawn_sub_agent",
        tier=resolved.resolved,
        requested_tier=resolved.requested,
        cascaded=resolved.cascaded,
        model=resolved.model,
        objective=truncated_objective,
        max_turns=packet.max_turns,
        allowed_tools=packet.allowed_tools or "<default>",
    ) as span:
        try:
            run_result = await sub_agent.run(
                packet.objective,
                usage_limits=None,
            )
        except Exception as exc:
            span.set_attribute("exit_status", "error")
            span.record_exception(exc)
            return SubAgentResult(
                output=f"Sub-agent failed: {exc}",
                turns_used=0,
                resolved_tier=resolved.resolved,
                resolved_model=resolved.model,
                exit_status="error",
            )

        turns = int(getattr(run_result.usage(), "requests", 0))
        # max_turns check is informational here — pydantic-ai's own
        # usage_limits will enforce hard caps in a follow-up. For now we
        # surface the status when the sub-agent burned the budget.
        exit_status: ExitStatus = "max_turns" if turns >= packet.max_turns else "ok"

        # Forward token usage to the main session's tracker so spawn
        # cost rolls up into session_total (per the dashboard).
        usage = run_result.usage()
        from jac.runtime.sub_agent_usage import record_sub_agent_usage

        await record_sub_agent_usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            tier=resolved.resolved,
        )

        span.set_attribute("turns_used", turns)
        span.set_attribute("exit_status", exit_status)

        return SubAgentResult(
            output=str(run_result.output),
            turns_used=turns,
            resolved_tier=resolved.resolved,
            resolved_model=resolved.model,
            exit_status=exit_status,
        )


@jac_tool(summarizable=True)
async def spawn_sub_agent(
    reason: str,
    task_summary: str,
    tier: str,
    task_packet: dict[str, Any],
) -> str:
    """Delegate a context-heavy task to an isolated sub-agent.

    Use when the task requires ≳20k tokens of intermediate tool output
    (reading several large files, running multiple shell commands,
    fetching long web pages). The sub-agent runs in its own loop with
    its own message history; only the final result returns to you. This
    keeps your context window — and the per-turn token cost — small.

    Args:
        reason: One-sentence justification (HITL prompt shows this).
        task_summary: Short label for the spawn, also shown in HITL.
        tier: One of ``"small"`` / ``"medium"`` / ``"large"``. Cascades
            up if the active profile lacks the requested tier.
        task_packet: Fields matching :class:`SubAgentTaskPacket`:
            objective, success_criteria, relevant_paths, forbidden_actions,
            expected_output, allowed_tools, max_turns.

    Returns:
        The sub-agent's final response, prefixed with a one-line
        ``[sub-agent tier=X model=Y turns=N exit=ok]`` header so you
        can see the resolved tier without re-reading the approval.

    **Approval-required.** Every call surfaces a HITL prompt.
    **Depth cap = 1.** A sub-agent's own toolset excludes this tool —
    spawn cannot recurse.
    """
    _ = task_summary  # surfaced via approval; tool body doesn't need it
    cap = get_sub_agent_capability()
    if cap is None:
        raise JacConfigError(
            "spawn_sub_agent is not available in this session — no profile "
            "is active. Run with `--profile NAME` to enable sub-agents."
        )

    resolved = resolve_tier(cap.profile, tier)
    packet = SubAgentTaskPacket.model_validate(task_packet)

    result = await _run_sub_agent(cap, packet, resolved)
    cascade_note = f", {resolved.cascade_note}" if resolved.cascaded else ""
    header = (
        f"[sub-agent tier={result.resolved_tier} model={result.resolved_model} "
        f"turns={result.turns_used} exit={result.exit_status}{cascade_note}]"
    )
    return f"{header}\n\n{result.output}"
