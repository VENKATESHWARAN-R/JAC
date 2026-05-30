"""Sub-agent runtime (Phase B + the Phase 4 suspend/resume comms redesign).

The main agent delegates context-heavy work to an isolated sub-agent via
the ``spawn_sub_agent`` tool. The sub-agent runs in its *own* Agent loop
with its *own* message history, so the intermediate 50k-200k tokens of
file reads, shell output, web fetches, etc. stay in the sub-agent's
context — only the final result returns to the main agent.

Design: see ``docs/design/cost-efficient-orchestration.md`` §4 and the
review tracker ``docs/design/audit/2026-05-30-review.md`` (R7b).

Key invariants enforced here:

- **Depth cap = 1** — sub-agents do not get the ``spawn_sub_agent`` tool
  in their own toolset. Enforced *structurally* at construction (D40),
  not via runtime check.
- **Tier resolution cascades up only** — request ``small``; if the active
  profile has no ``small`` tier, fall back to ``medium``, then ``large``.
  Never cascade down (would silently exceed budget).
- **Approval-gated** — every spawn surfaces a HITL prompt with the
  resolved tier, tool allowlist, and packet details (D39).

**Bidirectional comms — suspend/resume (Phase 4, replaces the D41
live-channel transport).** When ``cost.sub_agent_bidirectional`` is on, a
worker that hits a fork it can't resolve calls the external
``ask_supervisor`` tool. Instead of parking a live coroutine on a queue,
the worker's ``agent.run`` *returns* a ``DeferredToolRequests`` — the run
is suspended, its full message history checkpointed in a
:class:`PendingSpawn`. The main agent receives the question as the spawn
tool's result, answers it (from its own context, or by escalating to the
human via ``clarify``), and ``respond_to_sub_agent`` *resumes* the worker
from the saved history plus the appended answer. No parked task, no global
live-channel registry, no contextvar race-resolver; pending questions are
plain serializable state. Modeled on A2A ``input-required``.

The tools themselves (``spawn_sub_agent`` / ``spawn_sub_agents`` /
``respond_to_sub_agent``) live at module bottom.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, RunContext
from pydantic_ai.capabilities import Instrumentation, PrepareTools
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import ToolCallPart, ToolDefinition
from pydantic_ai.toolsets import ExternalToolset

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.runtime.events import (
    EventBus,
    JacEventT,
    SubAgentAnswer,
    SubAgentCompleted,
    SubAgentQuestion,
    SubAgentSpawned,
)
from jac.tools import jac_tool
from jac.workspace.context import load_agents_context

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
    """Tool name allowlist, enforced at the Agent layer (R2). ``None`` means
    "all default sub-agent tools" (the main toolset minus ``spawn_sub_agent``).
    When set, the worker sees only the named tools plus an always-allowed
    control-plane set (``read_file``, ``ask_supervisor``) — a real sandbox.
    Name a tighter set to keep a worker on-task and off destructive verbs."""

    max_turns: int = 10
    """Hard cap on the sub-agent's model-call count. Prevents runaway
    loops; returns ``exit_status=max_turns`` when hit."""


class SubAgentSpawnSpec(BaseModel):
    """One entry in a :func:`spawn_sub_agents` batch (Phase E).

    Each spec is fully independent: per-spawn tier (with its own cascade),
    per-spawn packet, optional label. The model emits a list of these as a
    single tool call; the tool body runs all of them via ``asyncio.gather``.
    """

    tier: TierName
    """Tier for this spawn. Cascades up independently of sibling spawns."""

    label: str = ""
    """Short tag shown in the HITL approval line and the per-spawn result
    header. Optional — when empty the header omits it."""

    task_packet: SubAgentTaskPacket
    """Briefing for this spawn (same shape as the single-spawn tool)."""


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


# --- event bus indirection (renderer hooks) ---
#
# The bus belongs to the REPL session; the runtime can't import upward
# to grab it without a cycle. Same pattern as ``set_sub_agent_usage_recorder``:
# REPL installs at session start, runtime emits through this hook.
_event_bus: EventBus | None = None


def set_sub_agent_event_bus(bus: EventBus | None) -> None:
    """Install (or clear) the bus used to emit ``SubAgent*`` lifecycle
    events for the renderer. ``None`` silences emission — useful in
    headless / test contexts where no consumer is wired."""
    global _event_bus
    _event_bus = bus


async def _emit_sub_agent_event(event: JacEventT) -> None:
    """Fire-and-forget emission. Silent when no bus is installed."""
    if _event_bus is not None:
        await _event_bus.emit(event)


# Human-readable label for whoever is currently asking for approval.
# Defaults to ``"Gru"`` (the main agent). Set to the spawn_id during a
# sub-agent's ``Agent.run()`` so the approval handler — shared between
# main and sub-agents — can stamp the right name onto the HITL prompt.
# Per-task copy (asyncio.Task contextvar semantics), so parallel spawns
# each see their own label.
_current_agent_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_agent_label", default="Gru"
)


def get_current_agent_label() -> str:
    """Return the label of the agent currently executing — ``"Gru"`` on
    the main loop, ``"minion-N"`` inside a sub-agent's run. Read by
    :func:`jac.runtime.approval.make_approval_handler` to stamp the
    label onto every emitted :class:`ApprovalRequest`."""
    return _current_agent_label.get()


# Session-scoped monotonic counter. ``minion-1``, ``minion-2``, … give the
# user a stable label they can correlate across the approval panel, the
# spawn lifecycle events, ``/spawns``, and the question/answer panels. The
# label leans into JAC's Gru-and-minions theme; "sub-agent", "minion", and
# "worker" all refer to the same thing in this codebase (the prompt teaches
# Gru this so casual user phrasing routes correctly). Resets when the
# session ends (via ``_reset_pending_spawns``) so each new session starts at
# ``minion-1``.
_spawn_counter: int = 0


def _mint_spawn_id() -> str:
    """Return the next ``minion-N`` ID and bump the counter. Not thread-safe;
    the REPL runs single-threaded and concurrent spawn calls inside one
    ``asyncio.gather`` still serialise through the event loop."""
    global _spawn_counter
    _spawn_counter += 1
    return f"minion-{_spawn_counter}"


def _reset_spawn_counter() -> None:
    """Reset the spawn counter. Called by ``_reset_pending_spawns`` so
    /exit + REPL teardown + per-test cleanup all reset together."""
    global _spawn_counter
    _spawn_counter = 0


# ---------- bidirectional comms: suspend/resume (Phase 4) ----------


_BIDIRECTIONAL_ROUND_TRIP_CAP = 5
"""Hard ceiling on questions a single sub-agent may surface to the main
agent. A sixth ``ask_supervisor`` call is auto-answered with
:data:`_BIDIRECTIONAL_FINALIZE_DIRECTIVE` (the worker is resumed with that
directive instead of the question reaching the main agent) — so the spawn
always produces a coherent final answer even when it tries to over-converse."""

_BIDIRECTIONAL_WARN_AT = 3
"""Logfire warning fires when round_trips reaches this count, ahead of cap."""

# Safety bound on total worker runs for one spawn (initial + resumes). Past
# the round-trip cap we auto-feed the finalize directive; a pathological
# worker that *keeps* asking even then is force-completed after this many
# runs so a spawn can never loop forever. Generous headroom over the cap.
_MAX_WORKER_RUNS = _BIDIRECTIONAL_ROUND_TRIP_CAP + 3

_BIDIRECTIONAL_FINALIZE_DIRECTIVE = (
    "This conversation has reached its round-trip cap "
    f"({_BIDIRECTIONAL_ROUND_TRIP_CAP} questions). Do not ask further "
    "questions — the next ask will return this same message. Finalize "
    "your response now with what you have learned. If you still have open "
    "uncertainties, state them as explicit discrepancies in your answer."
)


# The worker-side ``ask_supervisor`` tool is an **external** tool: when the
# model calls it, ``agent.run`` returns a ``DeferredToolRequests`` instead of
# executing anything in-process. That return is the suspension point. The
# schema carries the ``reason`` discipline field even though external tools
# aren't decorated with ``@jac_tool`` (the model still justifies the call).
_ASK_SUPERVISOR_DEF = ToolDefinition(
    name="ask_supervisor",
    description=(
        "Pause yourself and ask your supervisor (the main agent) ONE focused "
        "question, then resume once it replies. Use only as a last resort when "
        "the packet is genuinely ambiguous about success, or you discovered "
        "context the packet didn't cover and the supervisor has the history to "
        "decide. Not a chat channel — every question costs a round-trip and the "
        "hard cap is 5 per spawn."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One-sentence justification for interrupting (audit trail).",
            },
            "question": {
                "type": "string",
                "description": "The single, specific question. Answerable in a sentence or two.",
            },
            "context": {
                "type": "string",
                "description": "Optional extra context the supervisor needs to answer.",
            },
        },
        "required": ["reason", "question"],
    },
)


def _ask_supervisor_toolset() -> ExternalToolset[Any]:
    """The external toolset declaring ``ask_supervisor`` to a worker.

    Attached only when bidirectional comms is enabled. Because it's an
    *external* tool, the worker's ``Agent`` must include
    ``DeferredToolRequests`` in its ``output_type`` (see
    :func:`_build_worker_agent`)."""
    return ExternalToolset([_ASK_SUPERVISOR_DEF])


@dataclass
class PendingSpawn:
    """A suspended sub-agent waiting for the main agent's answer.

    Pure, serializable state — no live coroutine, no queues. Holds the
    checkpoint (:attr:`history`) plus the id of the deferred
    ``ask_supervisor`` call so :func:`respond_to_sub_agent` can resume the
    worker by re-running its Agent with the answer appended.
    """

    spawn_id: str
    """Stable label (e.g. ``minion-3``) the main agent echoes back to route
    its reply to the right worker."""

    packet: SubAgentTaskPacket
    """The original briefing — the worker Agent is rebuilt from it on resume."""

    resolved: _ResolvedTier
    """Tier/model captured at spawn so resume + result rendering stay stable."""

    history: list[ModelMessage]
    """Checkpoint of the worker's message history at the suspension point."""

    tool_call_id: str
    """Id of the pending ``ask_supervisor`` call the answer fulfills."""

    question: str = ""
    """The most recently surfaced question (for ``/spawns`` + event payloads)."""

    round_trips: int = 0
    """Questions surfaced to the main agent so far (cap is
    :data:`_BIDIRECTIONAL_ROUND_TRIP_CAP`)."""

    runs: int = 0
    """Total worker ``agent.run`` invocations (initial + resumes). Bounded by
    :data:`_MAX_WORKER_RUNS` as a runaway backstop."""

    turns_used: int = 0
    """Accumulated model requests across every run of this spawn."""

    objective: str = ""
    """First 200 chars of the packet's objective — surfaced in ``/spawns``
    so the user can identify a parked worker without scrolling back."""


_pending_spawns: dict[str, PendingSpawn] = {}
"""Suspended sub-agents keyed by spawn_id. Survives across main-agent turns —
populated when a worker asks a question, read by ``respond_to_sub_agent``,
popped when the worker completes. Plain on-disk-serializable state (no live
coroutine), so a pending question is resumable across ``--resume`` and
renderable by a future browser/SDK surface."""


def _get_pending_spawn(spawn_id: str) -> PendingSpawn | None:
    """Test-friendly accessor. Production code uses the dict directly."""
    return _pending_spawns.get(spawn_id)


def _reset_pending_spawns() -> None:
    """Drop every suspended spawn and reset the spawn counter. Test fixture
    hook + safety net for REPL shutdown. Resetting the counter here keeps
    every new session's first spawn at ``minion-1`` (and test isolation works
    for free). Suspended spawns hold no live task, so there's nothing to
    cancel — just clear the registry."""
    _pending_spawns.clear()
    _reset_spawn_counter()


# ---------- the spawn implementation ----------

# Control-plane tools a filtered worker must never lose, regardless of the
# packet's allowlist: ``read_file`` (a worker almost always needs to inspect
# its own inputs) and ``ask_supervisor`` (the bidirectional escape hatch — a
# sandboxed worker that hits a fork it can't resolve must still be able to
# ask). ``ask_supervisor`` is an *external* tool, which ``PrepareTools`` never
# touches (it filters function tools only), so it survives filtering for free;
# it's named here for documentation and belt-and-suspenders.
_ALWAYS_ALLOWED_SUB_AGENT_TOOLS = frozenset({"read_file", "ask_supervisor"})

# Type of a pydantic-ai ``prepare_tools`` filter: takes the run context plus
# the function-tool definitions and returns the subset to expose.
_ToolsFilter = Callable[
    [RunContext[None], list[ToolDefinition]],
    Awaitable[list[ToolDefinition]],
]


def _make_allowed_tools_filter(allowed: list[str] | None) -> _ToolsFilter | None:
    """Build a ``prepare_tools`` filter that restricts a sub-agent's function
    tools to ``allowed`` plus :data:`_ALWAYS_ALLOWED_SUB_AGENT_TOOLS`.

    Returns ``None`` when there is no allowlist (the common path) — the Agent
    then runs unfiltered, so spawns that don't set ``allowed_tools`` are
    unchanged. The filter reads only ``tool_def.name``, so it composes with
    any toolset the factory assembled.
    """
    if not allowed:
        return None
    permitted = set(allowed) | _ALWAYS_ALLOWED_SUB_AGENT_TOOLS

    async def _filter(
        _ctx: RunContext[None], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        return [td for td in tool_defs if td.name in permitted]

    return _filter


def _render_packet(
    packet: SubAgentTaskPacket,
    base_prompt: str,
    agents_context: str | None = None,
) -> str:
    """Render the sub-agent's full system prompt from its briefing.

    Order: shipped base prompt, then project/user ``AGENTS.md`` context
    (under a ``# Project context`` header, omitted when absent), then the
    task packet sections (under ``# Task packet``). Orientation before the
    job; stable across sibling spawns so prompt caching stays effective.
    """
    sections: list[str] = [base_prompt.strip()]
    if agents_context and agents_context.strip():
        sections.append(f"\n# Project context\n\n{agents_context.strip()}")
    sections.append("\n# Task packet")
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


def _build_worker_agent(
    cap: SubAgentCapability,
    packet: SubAgentTaskPacket,
    resolved: _ResolvedTier,
    *,
    bidirectional: bool,
) -> Agent[None, Any]:
    """Construct (or reconstruct, on resume) the worker Agent from the packet.

    Identical inputs → identical Agent, which is what makes the resume path
    valid: a suspended worker is rebuilt here and re-run with its saved
    history. When ``bidirectional`` is set, the Agent gains the external
    ``ask_supervisor`` toolset and ``DeferredToolRequests`` in its output
    types — that's what lets a run *suspend* on a question instead of
    blocking. ``allowed_tools`` filtering (R2) is applied either way.
    """
    capabilities = list(cap.capability_factory(packet.allowed_tools))
    # Always attach Instrumentation so spans nest under the spawn span.
    capabilities.insert(0, Instrumentation())
    tools_filter = _make_allowed_tools_filter(packet.allowed_tools)
    if tools_filter is not None:
        capabilities.append(PrepareTools(tools_filter))

    instructions = _render_packet(packet, cap.base_prompt, load_agents_context())
    if bidirectional:
        return Agent(
            resolved.model,
            instructions=instructions,
            capabilities=capabilities,
            output_type=[str, DeferredToolRequests],
            toolsets=[_ask_supervisor_toolset()],
        )
    return Agent(resolved.model, instructions=instructions, capabilities=capabilities)


async def _record_run_usage(run_result: Any, resolved: _ResolvedTier) -> int:
    """Forward a run's token usage to the session tracker; return turn count.

    ``AgentRunResult.usage`` is a property in current pydantic-ai (it used to
    be a method; the deprecation warning bites the moment we call it). Read
    once, reuse.
    """
    usage = run_result.usage
    turns = int(getattr(usage, "requests", 0))
    from jac.runtime.sub_agent_usage import record_sub_agent_usage

    await record_sub_agent_usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        tier=resolved.resolved,
    )
    return turns


async def _run_sub_agent(
    cap: SubAgentCapability,
    packet: SubAgentTaskPacket,
    resolved: _ResolvedTier,
    *,
    spawn_id: str | None = None,
) -> SubAgentResult:
    """Build and run a non-suspending sub-agent to completion.

    The simple path: one ``agent.run``, returns a :class:`SubAgentResult`.
    Used by the non-bidirectional ``spawn_sub_agent`` path and by every
    parallel spawn (parallel workers never get the ``ask_supervisor`` tool).
    The bidirectional single-spawn path uses :func:`_drive_worker` instead.

    ``spawn_id`` is the human-readable label (``"minion-N"``) bound into the
    contextvar so the shared approval handler stamps the right name onto HITL
    prompts. Optional for test factories; when omitted the label stays at the
    default ``"Gru"``.
    """
    label_token: contextvars.Token[str] | None = None
    if spawn_id is not None:
        label_token = _current_agent_label.set(spawn_id)

    sub_agent = _build_worker_agent(cap, packet, resolved, bidirectional=False)
    try:
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
            bidirectional=False,
        ) as span:
            try:
                run_result = await sub_agent.run(packet.objective, usage_limits=None)
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

            turns = await _record_run_usage(run_result, resolved)
            exit_status: ExitStatus = "max_turns" if turns >= packet.max_turns else "ok"
            span.set_attribute("turns_used", turns)
            span.set_attribute("exit_status", exit_status)
            return SubAgentResult(
                output=str(run_result.output),
                turns_used=turns,
                resolved_tier=resolved.resolved,
                resolved_model=resolved.model,
                exit_status=exit_status,
            )
    finally:
        if label_token is not None:
            _current_agent_label.reset(label_token)


def _render_final_result(result: SubAgentResult, resolved: _ResolvedTier) -> str:
    """Compose the tagged header + sub-agent output. Shared by the
    sequential, suspend/resume, and respond paths so the main agent always
    sees the same shape regardless of which tool returned the result."""
    cascade_note = f", {resolved.cascade_note}" if resolved.cascaded else ""
    header = (
        f"[sub-agent tier={result.resolved_tier} model={result.resolved_model} "
        f"turns={result.turns_used} exit={result.exit_status}{cascade_note}]"
    )
    return f"{header}\n\n{result.output}"


def _render_question(spawn_id: str, question: str) -> str:
    """Compose the tool-return string surfaced to the main agent when a
    sub-agent suspends on a question. The ``spawn_id`` token is the routing
    key the main agent must echo back via ``respond_to_sub_agent``."""
    return (
        f"[sub-agent → main: question pending] spawn_id={spawn_id}\n\n"
        f"{question}\n\n"
        f"Answer it yourself if you have the context, or escalate to the user "
        f"with `clarify` first. Then reply with "
        f"`respond_to_sub_agent(reason=..., spawn_id={spawn_id!r}, answer=...)`. "
        f"You may call other tools first if you need to look something up."
    )


def _extract_question(call: ToolCallPart) -> str:
    """Pull the question (+ optional context) out of a deferred
    ``ask_supervisor`` call's args. Tolerates args delivered as a dict or a
    JSON string."""
    args: Any = call.args
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            args = {"question": args}
    if not isinstance(args, dict):
        args = {}
    question = str(args.get("question", "") or "").strip()
    context = str(args.get("context", "") or "").strip()
    if not question:
        question = "(the sub-agent asked an empty question)"
    return f"{question}\n\nAdditional context:\n{context}" if context else question


def _first_ask_call(requests: DeferredToolRequests) -> ToolCallPart | None:
    """Return the first ``ask_supervisor`` deferred call, or ``None``.

    A worker should only ever emit one at a time, but be defensive."""
    for call in requests.calls:
        if call.tool_name == _ASK_SUPERVISOR_DEF.name:
            return call
    return requests.calls[0] if requests.calls else None


async def _drive_worker(
    cap: SubAgentCapability,
    pending: PendingSpawn,
    *,
    answer: str | None,
) -> SubAgentResult | None:
    """Run or resume a bidirectional worker until it finishes or surfaces a
    question.

    ``answer is None`` → the *initial* run (drives ``pending.packet.objective``).
    ``answer`` set → *resume* from ``pending.history`` with the answer fulfilling
    the pending ``ask_supervisor`` call.

    Returns a :class:`SubAgentResult` when the worker finished, or ``None``
    when it suspended on a fresh question (``pending`` is mutated in place with
    the new checkpoint + question, ready to be parked in ``_pending_spawns``).
    Past the round-trip cap the worker is auto-resumed with the finalize
    directive instead of the question reaching the main agent.
    """
    label_token = _current_agent_label.set(pending.spawn_id)
    next_answer = answer
    try:
        agent = _build_worker_agent(cap, pending.packet, pending.resolved, bidirectional=True)
        while True:
            if pending.runs >= _MAX_WORKER_RUNS:
                # Runaway backstop: a worker that keeps asking even after the
                # finalize directive. Force-complete with whatever it last said.
                return _force_complete(pending, "exceeded the worker-run safety bound")
            pending.runs += 1
            with logfire.span(
                "spawn_sub_agent",
                tier=pending.resolved.resolved,
                requested_tier=pending.resolved.requested,
                cascaded=pending.resolved.cascaded,
                model=pending.resolved.model,
                objective=pending.packet.objective[:100],
                max_turns=pending.packet.max_turns,
                allowed_tools=pending.packet.allowed_tools or "<default>",
                bidirectional=True,
                run=pending.runs,
            ) as span:
                try:
                    if pending.runs == 1 and next_answer is None:
                        run_result = await agent.run(pending.packet.objective, usage_limits=None)
                    else:
                        results = DeferredToolResults(
                            calls={pending.tool_call_id: next_answer or ""}
                        )
                        run_result = await agent.run(
                            message_history=pending.history,
                            deferred_tool_results=results,
                            usage_limits=None,
                        )
                except Exception as exc:
                    span.set_attribute("exit_status", "error")
                    span.record_exception(exc)
                    return SubAgentResult(
                        output=f"Sub-agent failed: {exc}",
                        turns_used=pending.turns_used,
                        resolved_tier=pending.resolved.resolved,
                        resolved_model=pending.resolved.model,
                        exit_status="error",
                    )

                pending.turns_used += await _record_run_usage(run_result, pending.resolved)
                output = run_result.output

                if not isinstance(output, DeferredToolRequests):
                    exit_status: ExitStatus = (
                        "max_turns" if pending.turns_used >= pending.packet.max_turns else "ok"
                    )
                    span.set_attribute("turns_used", pending.turns_used)
                    span.set_attribute("exit_status", exit_status)
                    return SubAgentResult(
                        output=str(output),
                        turns_used=pending.turns_used,
                        resolved_tier=pending.resolved.resolved,
                        resolved_model=pending.resolved.model,
                        exit_status=exit_status,
                    )

                # Worker suspended on a question. Checkpoint + route.
                call = _first_ask_call(output)
                if call is None:
                    return _force_complete(pending, "deferred request had no tool call")
                pending.history = run_result.all_messages()
                pending.tool_call_id = call.tool_call_id
                span.set_attribute("suspended", True)

                if pending.round_trips >= _BIDIRECTIONAL_ROUND_TRIP_CAP:
                    # 6th+ ask: auto-resume with the finalize directive — the
                    # question never reaches the main agent.
                    logfire.warning(
                        "ask_supervisor.cap_reached",
                        spawn_id=pending.spawn_id,
                        round_trips=pending.round_trips,
                        cap=_BIDIRECTIONAL_ROUND_TRIP_CAP,
                    )
                    next_answer = _BIDIRECTIONAL_FINALIZE_DIRECTIVE
                    continue

                pending.round_trips += 1
                if pending.round_trips == _BIDIRECTIONAL_WARN_AT:
                    logfire.warning(
                        "ask_supervisor.warn_threshold",
                        spawn_id=pending.spawn_id,
                        round_trips=pending.round_trips,
                        cap=_BIDIRECTIONAL_ROUND_TRIP_CAP,
                    )
                pending.question = _extract_question(call)
                return None
    finally:
        _current_agent_label.reset(label_token)


def _force_complete(pending: PendingSpawn, why: str) -> SubAgentResult:
    """Synthesize a final result for a worker that can't be driven to a clean
    finish (runaway asker / malformed deferred request). Loud, not silent."""
    logfire.warning("sub_agent.force_complete", spawn_id=pending.spawn_id, reason=why)
    return SubAgentResult(
        output=(
            f"Sub-agent {pending.spawn_id} was force-finalized ({why}); "
            "it did not produce a clean final answer."
        ),
        turns_used=pending.turns_used,
        resolved_tier=pending.resolved.resolved,
        resolved_model=pending.resolved.model,
        exit_status="error",
    )


async def _spawn_with_suspension(
    cap: SubAgentCapability,
    packet: SubAgentTaskPacket,
    resolved: _ResolvedTier,
) -> str:
    """Bidirectional single-spawn path. Runs the worker; if it suspends on a
    question, parks a :class:`PendingSpawn` and returns the question block."""
    spawn_id = _mint_spawn_id()
    pending = PendingSpawn(
        spawn_id=spawn_id,
        packet=packet,
        resolved=resolved,
        history=[],
        tool_call_id="",
        objective=packet.objective[:200],
    )
    await _emit_sub_agent_event(
        SubAgentSpawned(
            spawn_id=spawn_id,
            tier=resolved.resolved,
            model=resolved.model,
            objective=packet.objective[:200],
        )
    )

    result = await _drive_worker(cap, pending, answer=None)
    if result is not None:
        await _emit_sub_agent_event(
            SubAgentCompleted(
                spawn_id=spawn_id,
                exit_status=result.exit_status,
                turns_used=result.turns_used,
                ask_main_agent_count=pending.round_trips,
            )
        )
        return _render_final_result(result, resolved)

    # Suspended on a question — park it for the main agent's reply.
    _pending_spawns[spawn_id] = pending
    await _emit_sub_agent_event(
        SubAgentQuestion(
            spawn_id=spawn_id,
            question=pending.question,
            round_trip=pending.round_trips,
        )
    )
    return _render_question(spawn_id, pending.question)


@jac_tool(summarizable=False)
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
        When ``cost.sub_agent_bidirectional`` is enabled and the
        sub-agent pauses to ask a question, this tool instead returns
        a ``[sub-agent → main: question pending]`` block with the
        ``spawn_id`` to echo back via ``respond_to_sub_agent``.

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

    if get_settings().cost.sub_agent_bidirectional:
        return await _spawn_with_suspension(cap, packet, resolved)

    spawn_id = _mint_spawn_id()
    result = await _run_sub_agent(cap, packet, resolved, spawn_id=spawn_id)
    return _render_final_result(result, resolved)


# ---------- bidirectional tool: main-agent reply (Phase 4) ----------


@jac_tool(summarizable=False)
async def respond_to_sub_agent(
    reason: str,
    spawn_id: str,
    answer: str,
) -> str:
    """Reply to a suspended sub-agent's pending question, resuming it.

    Call this when ``spawn_sub_agent`` returned a
    ``[sub-agent → main: question pending] spawn_id=...`` block. You decide
    *how* to answer: from your own context, or — if it's the user's call —
    by asking the human via ``clarify`` first and passing their decision
    here. The worker resumes from its saved history with your answer and
    you'll get either its final result or its next question back.

    Args:
        reason: One-sentence justification.
        spawn_id: The id from the question block — a stable label like
            ``minion-3``. Echo it back verbatim.
        answer: Your reply. Keep it focused — the sub-agent only asked
            one question.

    Returns:
        Either the sub-agent's final tagged result (worker completed),
        or another ``[sub-agent → main: question pending]`` block if it
        has a follow-up question.
    """
    _ = reason
    pending = _pending_spawns.get(spawn_id)
    if pending is None:
        return (
            f"[error: no pending sub-agent with spawn_id={spawn_id!r}; it may have "
            f"already finished or never existed. Active spawn_ids: "
            f"{sorted(_pending_spawns)!r}]"
        )

    cap = get_sub_agent_capability()
    if cap is None:
        _pending_spawns.pop(spawn_id, None)
        return (
            f"[error: sub-agent {spawn_id!r} cannot be resumed — no active "
            "sub-agent profile in this session]"
        )

    # Renderer hook before delivery so the user sees the answer in scroll-back
    # before whatever the sub-agent does with it.
    await _emit_sub_agent_event(SubAgentAnswer(spawn_id=spawn_id, answer=answer))

    result = await _drive_worker(cap, pending, answer=answer)
    if result is not None:
        _pending_spawns.pop(spawn_id, None)
        await _emit_sub_agent_event(
            SubAgentCompleted(
                spawn_id=spawn_id,
                exit_status=result.exit_status,
                turns_used=result.turns_used,
                ask_main_agent_count=pending.round_trips,
            )
        )
        return _render_final_result(result, pending.resolved)

    # Worker asked again — it's still parked (same PendingSpawn object).
    await _emit_sub_agent_event(
        SubAgentQuestion(
            spawn_id=spawn_id,
            question=pending.question,
            round_trip=pending.round_trips,
        )
    )
    return _render_question(spawn_id, pending.question)


# ---------- parallel spawn (Phase E) ----------


def _render_spawn_block(
    index: int,
    spec: SubAgentSpawnSpec,
    result: SubAgentResult | Exception,
    resolved: _ResolvedTier | None,
) -> str:
    """Render one block of the parallel-spawn combined output.

    Mirrors the single-spawn tagged header so the main agent can read the
    same shape no matter which tool it called. ``resolved`` is ``None`` when
    tier resolution itself failed (the spawn never reached ``_run_sub_agent``).
    """
    label_part = f" ({spec.label})" if spec.label else ""
    divider = f"── spawn {index}{label_part}"

    if isinstance(result, Exception):
        # Resolution failed (unknown tier, no tier available, etc.) — surface
        # the requested tier so the user can fix the packet.
        return f"{divider}: tier={spec.tier} exit=error ──\nSpawn setup failed: {result}"

    assert resolved is not None  # paired with the non-exception branch
    cascade_note = f", {resolved.cascade_note}" if resolved.cascaded else ""
    header = (
        f"{divider}: tier={result.resolved_tier} model={result.resolved_model} "
        f"turns={result.turns_used} exit={result.exit_status}{cascade_note} ──"
    )
    return f"{header}\n{result.output}"


@jac_tool(summarizable=False)
async def spawn_sub_agents(
    reason: str,
    task_summary: str,
    spawns: list[SubAgentSpawnSpec],
) -> str:
    """Delegate **multiple independent** tasks to sub-agents *in parallel*.

    Use when you have N independent investigations whose results you want
    back at roughly the same time (e.g. summarize each of 4 modules; review
    3 files for separate concerns). Each spawn runs in its own isolated
    Agent loop — siblings' intermediate context never bleeds across.

    Sequential `spawn_sub_agent` calls are still the right tool when one
    result must inform the next. Reach for this one only when the spawns
    are genuinely independent.

    Args:
        reason: One-sentence justification (HITL prompt shows this).
        task_summary: Short label covering the batch as a whole.
        spawns: List of :class:`SubAgentSpawnSpec`. Each spec carries its
            own tier (cascaded independently) and its own task packet.

    Returns:
        A single string combining every spawn's output. Each spawn is
        delimited by a ``── spawn N (label): tier=... ──`` header so the
        main agent can read results in order.

    **Approval-required.** One HITL prompt covers the whole batch — the
    user sees all spawns at once.
    **Depth cap = 1.** Like the single-spawn tool, a spawned sub-agent's
    toolset excludes this tool — spawn cannot recurse, even via the
    parallel variant. Parallel workers are never bidirectional (no
    ``ask_supervisor``); a back-and-forth belongs on the sequential tool.
    """
    _ = task_summary  # surfaced via approval; tool body doesn't need it
    cap = get_sub_agent_capability()
    if cap is None:
        raise JacConfigError(
            "spawn_sub_agents is not available in this session — no profile "
            "is active. Run with `--profile NAME` to enable sub-agents."
        )
    if not spawns:
        raise JacConfigError(
            "spawn_sub_agents requires at least one spawn spec; got an empty list."
        )

    # Resolve tiers up front so the outer span can show what was actually
    # picked. Resolution failures are captured per-spawn rather than killing
    # the whole batch — the rendered output flags them as `exit=error`.
    resolved: list[_ResolvedTier | Exception] = []
    for spec in spawns:
        try:
            resolved.append(resolve_tier(cap.profile, spec.tier))
        except JacConfigError as exc:
            resolved.append(exc)

    resolved_tiers = [r.resolved if isinstance(r, _ResolvedTier) else "<error>" for r in resolved]
    with logfire.span(
        "spawn_sub_agents",
        count=len(spawns),
        requested_tiers=[spec.tier for spec in spawns],
        resolved_tiers=resolved_tiers,
        parallel=True,
    ) as span:
        # Launch every successful resolution under asyncio.gather. Failed
        # resolutions become synthetic exceptions in the results list — they
        # never fire a child task. Approvals raised by sub-agents serialize
        # at the bus level (the renderer reads the queue one event at a
        # time), so HITL multiplexing is correct by construction.
        async def _run_one(spec: SubAgentSpawnSpec, r: _ResolvedTier) -> SubAgentResult:
            # Each parallel spawn mints its own ID so the approval panel
            # can tell the user which sub-agent is asking.
            spawn_id = _mint_spawn_id()
            # E.3: emit lifecycle events so the user sees each parallel
            # spawn appear ("▶ minion-N") and complete ("✓ minion-N done")
            # individually instead of waiting for the whole batch to land
            # as one combined output block. ``ask_main_agent_count`` is
            # always 0 for parallel spawns (no ask_supervisor in their
            # toolset; that's a sequential bidirectional feature).
            await _emit_sub_agent_event(
                SubAgentSpawned(
                    spawn_id=spawn_id,
                    tier=r.resolved,
                    model=r.model,
                    objective=spec.task_packet.objective[:200],
                )
            )
            try:
                result = await _run_sub_agent(cap, spec.task_packet, r, spawn_id=spawn_id)
            except BaseException:
                # Cancellation / out-of-band — still surface a Completed so
                # the renderer doesn't leave a "▶" panel orphaned.
                await _emit_sub_agent_event(
                    SubAgentCompleted(
                        spawn_id=spawn_id,
                        exit_status="error",
                        turns_used=0,
                        ask_main_agent_count=0,
                    )
                )
                raise
            await _emit_sub_agent_event(
                SubAgentCompleted(
                    spawn_id=spawn_id,
                    exit_status=result.exit_status,
                    turns_used=result.turns_used,
                    ask_main_agent_count=0,
                )
            )
            return result

        tasks: list[asyncio.Task[SubAgentResult] | None] = [
            asyncio.create_task(_run_one(spec, r)) if isinstance(r, _ResolvedTier) else None
            for spec, r in zip(spawns, resolved, strict=True)
        ]

        results: list[SubAgentResult | Exception] = []
        for r, t in zip(resolved, tasks, strict=True):
            if t is None:
                # Carry the resolution error through to the renderer.
                assert isinstance(r, Exception)
                results.append(r)
                continue
            try:
                results.append(await t)
            except Exception as exc:
                # _run_sub_agent catches the model's exceptions internally,
                # but cancellation / out-of-band errors still bubble here.
                results.append(exc)

        ok_count = sum(
            1 for r in results if isinstance(r, SubAgentResult) and r.exit_status == "ok"
        )
        span.set_attribute("ok_count", ok_count)
        span.set_attribute("error_count", len(results) - ok_count)

        blocks: list[str] = [f"[parallel spawn: {len(spawns)} sub-agents]"]
        for idx, (spec, r, res) in enumerate(zip(spawns, resolved, results, strict=True), start=1):
            resolved_arg = r if isinstance(r, _ResolvedTier) else None
            blocks.append(_render_spawn_block(idx, spec, res, resolved_arg))
        return "\n\n".join(blocks)
