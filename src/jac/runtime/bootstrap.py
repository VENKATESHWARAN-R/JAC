"""Surface-agnostic session bootstrap — the shared engine wiring (D48).

Both the CLI REPL and the web chat need the *same* fully-wired session: an
``EventBus``, the ``Hooks`` + approval capabilities, the plan / process /
clarify / A2A / skills / MCP capabilities, a built ``Gru`` agent, a
``SessionDriver``, a ``UsageTracker``, and the sub-agent factory + module
globals. That wiring used to live inline in ``cli/repl.py``; it is extracted
here so a second surface reuses it verbatim rather than duplicating it (and
drifting).

This module is the engine half of a session. Each surface keeps its own
*renderer* half: the CLI builds a status bar + prompt + ``CliRenderer``; the
web builds an SSE/WebSocket renderer. Both consume the same bus.

:func:`build_session_runtime` returns a :class:`SessionRuntime` holding every
handle a surface needs to drive turns and to rebuild/switch (the slash commands
mutate ``driver.gru`` etc. in place). It raises :class:`JacConfigError` on a
missing model or malformed profile — the caller renders that however it likes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent

from jac.capabilities.a2a import make_a2a_capability
from jac.capabilities.clarify import make_clarify_capability
from jac.capabilities.mcp import make_mcp_capability
from jac.capabilities.plan import make_plan_capability
from jac.capabilities.process import make_process_capability
from jac.capabilities.skills import make_skills_capability
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.profiles_crud import get_profile
from jac.runtime.approval import make_approval_handler
from jac.runtime.driver import SessionDriver
from jac.runtime.events import EventBus
from jac.runtime.gru import build_gru, sub_agent_capabilities
from jac.runtime.hooks import make_hooks
from jac.runtime.session import Session
from jac.runtime.sub_agent import (
    SubAgentCapability,
    set_sub_agent_capability,
    set_sub_agent_event_bus,
)
from jac.runtime.sub_agent_usage import (
    reset_sub_agent_stats,
    set_sub_agent_usage_recorder,
)
from jac.runtime.tool_summarize import reset_summarizer_stats, set_summarizer_model
from jac.runtime.usage import BudgetLimits, UsageTracker, make_usage_tracker
from jac.secrets import resolve_optional_keys
from jac.workspace import paths
from jac.workspace.paths import load_prompt
from jac.workspace.session_ctx import set_current_session_id


def resolve_summarizer_model(profile_name: str | None) -> str | None:
    """Return the small-tier model for the named profile, or ``None``.

    Falls back gracefully when:

    - No profile is in play (``--model`` ad-hoc session, ``profile_name`` is ``None``).
    - The profile has no ``small`` tier (e.g. only ``medium`` configured).
    - The profile fails to load for some reason.

    Returning ``None`` makes the history capability drop-only on compaction —
    safe, no crash; we just lose the summary.
    """
    if profile_name is None:
        return None
    try:
        profile = get_profile(profile_name)
    except JacConfigError:
        return None
    if "small" not in profile.tiers or not profile.tiers["small"]:
        return None
    return profile.tiers["small"][0]


@dataclass
class SessionRuntime:
    """Every engine handle a surface needs to drive + mutate one session.

    The capabilities are exposed individually (not just inside ``gru``) because
    the slash commands / web actions mutate them in place — ``a2a_capability``
    starts/stops the guest server, ``plan_capability`` switches plan files on a
    session change, and a Gru rebuild re-attaches ``persisted_capabilities``.
    """

    gru: Agent
    bus: EventBus
    driver: SessionDriver
    usage_tracker: UsageTracker
    hooks: Any
    approval: Any
    plan_capability: Any
    process_capability: Any
    clarify_capability: Any
    a2a_capability: Any
    skills_capability: Any
    mcp_capability: Any
    persisted_capabilities: list[Any]
    active_profile: Profile | None
    model_id: str


def build_session_runtime(
    session: Session,
    *,
    model_override: str | None,
    profile_name: str | None,
    restored_plan: list[dict[str, str]] | None = None,
) -> SessionRuntime:
    """Wire a complete, surface-agnostic session engine around ``session``.

    Mirrors the bootstrap the REPL used to do inline. Side effects (the same
    ones the REPL relied on): publishes the active session id, the summarizer
    model, and the sub-agent factory/recorder/event-bus to their module
    globals; best-effort resolves optional feature keys into ``os.environ``.

    Raises:
        JacConfigError: no model configured, or a malformed/unknown profile.
    """
    # Make the active session id discoverable to tools (e.g. `remember`)
    # without threading a session object through every call site.
    set_current_session_id(session.session_id)

    # The small-tier model for the tool-result post-processor + compaction.
    summarizer_model = resolve_summarizer_model(profile_name)
    set_summarizer_model(summarizer_model)
    reset_summarizer_stats()

    # Best-effort optional feature keys (e.g. TAVILY upgrades web_search).
    resolve_optional_keys(["TAVILY_API_KEY"])

    bus = EventBus()
    hooks = make_hooks(bus)
    approval = make_approval_handler(bus)
    plan_capability = make_plan_capability(
        bus,
        plan_file=session.plan_file,
        initial_steps=restored_plan or None,
    )
    process_capability = make_process_capability(bus)
    clarify_capability = make_clarify_capability(bus)
    a2a_capability = make_a2a_capability(
        bus=bus,
        model=None,  # filled after settings.model resolves below
        profile_name=profile_name,
    )
    skills_capability = make_skills_capability()
    a2a_capability.skills_getter = lambda: skills_capability.skills
    mcp_capability = make_mcp_capability()

    gru = build_gru(
        model_override=model_override,
        extra_capabilities=[
            hooks,
            approval,
            plan_capability,
            process_capability,
            clarify_capability,
            a2a_capability,
            skills_capability,
            mcp_capability,
        ],
        bus=bus,
        summarizer_model=summarizer_model,
    )

    # Active Profile object so /model and /profile can enumerate tier models.
    # None when started with --model (ad-hoc, no profile).
    active_profile: Profile | None = None
    if profile_name is not None:
        active_profile = get_profile(profile_name)

    settings = get_settings()
    model_id = model_override or settings.model or "unknown"
    if model_id != "unknown":
        a2a_capability.model = model_id
    if active_profile is not None:
        a2a_capability.retention_days = active_profile.a2a.context_retention_days
        a2a_capability.allow_private_peers = active_profile.a2a.allow_private_peers
        a2a_capability.profile_peers = dict(active_profile.a2a.peers)

    usage_tracker = make_usage_tracker(
        session_id=session.session_id,
        bus=bus,
        usage_file=paths.project_usage_file(),
        limits=BudgetLimits(
            session_input_tokens=settings.budget.session_input_tokens,
            session_total_tokens=settings.budget.session_total_tokens,
            project_total_tokens=settings.budget.project_total_tokens,
            warn_pct=settings.budget.warn_pct,
            hardstop_pct=settings.budget.hardstop_pct,
        ),
    )
    a2a_capability.usage_tracker = usage_tracker

    driver = SessionDriver(gru=gru, bus=bus, usage_tracker=usage_tracker)

    persisted_capabilities = [
        hooks,
        approval,
        plan_capability,
        process_capability,
        clarify_capability,
        a2a_capability,
        skills_capability,
        mcp_capability,
    ]

    # Sub-agent factory — closes over the shared bus-bound capabilities so a
    # spawned worker's tool calls emit onto the same bus the surface reads AND
    # route through the same HITL approval handler the main agent uses.
    def _sub_agent_capability_factory(allowed_tools: list[str] | None = None) -> list[Any]:
        return sub_agent_capabilities(
            allowed_tools,
            hooks=hooks,
            approval=approval,
            skills_capability=skills_capability,
            a2a_capability=a2a_capability,
            mcp_capability=mcp_capability,
        )

    if active_profile is not None:
        set_sub_agent_capability(
            SubAgentCapability(
                profile=active_profile,
                base_prompt=load_prompt("sub_agent_system").strip(),
                capability_factory=_sub_agent_capability_factory,
            )
        )
    else:
        set_sub_agent_capability(None)
    reset_sub_agent_stats()

    async def _record_sub_agent(in_tokens: int, out_tokens: int, tier: str) -> None:
        await usage_tracker.add_sub_agent(in_tokens, out_tokens, tier)

    set_sub_agent_usage_recorder(_record_sub_agent)
    set_sub_agent_event_bus(bus)

    return SessionRuntime(
        gru=gru,
        bus=bus,
        driver=driver,
        usage_tracker=usage_tracker,
        hooks=hooks,
        approval=approval,
        plan_capability=plan_capability,
        process_capability=process_capability,
        clarify_capability=clarify_capability,
        a2a_capability=a2a_capability,
        skills_capability=skills_capability,
        mcp_capability=mcp_capability,
        persisted_capabilities=persisted_capabilities,
        active_profile=active_profile,
        model_id=model_id,
    )
