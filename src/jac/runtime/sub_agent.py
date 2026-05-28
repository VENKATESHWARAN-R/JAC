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

import asyncio
import contextlib
import contextvars
from dataclasses import dataclass, field
from typing import Any, Literal

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation

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


# --- event bus indirection (D41 renderer hooks) ---
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


# ---------- bidirectional comms channel (D41) ----------


_BIDIRECTIONAL_ROUND_TRIP_CAP = 5
"""Hard ceiling on questions a single sub-agent may ask the main agent.

A sixth ``ask_main_agent`` call returns :data:`_BIDIRECTIONAL_FINALIZE_DIRECTIVE`
directly instead of putting the question on the queue — the sub-agent
always gets a coherent reply, even when it tries to over-converse."""

_BIDIRECTIONAL_WARN_AT = 3
"""Logfire warning fires when round_trips reaches this count, ahead of cap."""

_BIDIRECTIONAL_FINALIZE_DIRECTIVE = (
    "This conversation has reached its round-trip cap "
    f"({_BIDIRECTIONAL_ROUND_TRIP_CAP} questions). Do not ask further "
    "questions — the next ask will return this same message. Finalize "
    "your response now with what you have learned. If you still have open "
    "uncertainties, list them as 'discrepancies' or 'open questions' in "
    "your final output so the main agent can address them directly."
)


@dataclass
class SubAgentChannel:
    """Per-spawn comms channel for bidirectional ``ask_main_agent`` flow.

    Lives in :data:`_pending_channels` keyed by :attr:`spawn_id` for as
    long as the worker task is parked waiting for an answer. Removed
    when the worker completes (success, error, or cancellation).
    """

    spawn_id: str
    """8-char hex id surfaced to the main agent so it can route replies."""

    question_q: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    """Sub-agent → main agent: the sub-agent's pending question."""

    answer_q: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    """Main agent → sub-agent: the main agent's reply."""

    round_trips: int = 0
    """Count of completed (or in-flight) round-trips. Incremented in
    ``ask_main_agent`` *before* the question is queued; the cap check
    fires before increment so a phantom 6th call never queues."""

    cap: int = _BIDIRECTIONAL_ROUND_TRIP_CAP
    """Per-instance copy so tests can override without monkey-patching the
    module constant."""

    worker_task: asyncio.Task[SubAgentResult] | None = None
    """The :func:`_run_sub_agent` invocation, run as a background task so
    the spawn tool can race completion vs the first question arrival."""

    resolved: _ResolvedTier | None = None
    """Captured at construction so result rendering after a question yield
    can show the same header as the non-bidirectional path."""

    objective: str = ""
    """First 200 chars of the packet's objective — surfaced in `/spawns`
    so the user can identify a parked worker without scrolling back."""


_pending_channels: dict[str, SubAgentChannel] = {}
"""Active sub-agent conversations keyed by spawn_id. Survives across
main-agent turns — populated by ``spawn_sub_agent`` (bidirectional path),
read by ``respond_to_sub_agent``, popped when the worker task ends."""


# Session-scoped monotonic counter. The previous design used
# ``secrets.token_hex(4)`` which produced opaque 8-hex IDs (``a3f201b9``)
# — fine for routing but useless for the human in the loop. ``minion-1``,
# ``minion-2``, … give the user a stable label they can correlate across
# the approval panel, the spawn lifecycle events, ``/spawns``, and the
# question/answer panels. The label leans into JAC's Gru-and-minions
# theme; "sub-agent", "minion", and "worker" all refer to the same thing
# in this codebase (the prompt teaches Gru this so casual user phrasing
# routes correctly). Resets when the session ends (via
# ``_reset_pending_channels`` from the REPL teardown path) so each new
# session starts at ``minion-1``.
_spawn_counter: int = 0


def _mint_spawn_id() -> str:
    """Return the next ``minion-N`` ID and bump the counter. Not thread-safe;
    the REPL runs single-threaded and concurrent spawn calls inside one
    ``asyncio.gather`` still serialise through the event loop."""
    global _spawn_counter
    _spawn_counter += 1
    return f"minion-{_spawn_counter}"


def _reset_spawn_counter() -> None:
    """Reset the spawn counter. Called by ``_reset_pending_channels`` so
    /exit + REPL teardown + per-test cleanup all reset together."""
    global _spawn_counter
    _spawn_counter = 0


# Threaded into the worker task's context so the ``ask_main_agent`` tool
# can locate its channel without a closure (capability factories stay
# plain functions). asyncio.Task copies the current context on creation
# and ``set()`` mutates only the copy — no leakage back to the parent.
_current_sub_agent_channel: contextvars.ContextVar[SubAgentChannel | None] = contextvars.ContextVar(
    "_current_sub_agent_channel", default=None
)


# Human-readable label for whoever is currently asking for approval.
# Defaults to ``"Gru"`` (the main agent). Set to the spawn_id during a
# sub-agent's ``Agent.run()`` so the approval handler — shared between
# main and sub-agents — can stamp the right name onto the HITL prompt.
# Per-task copy (same asyncio.Task contextvar semantics as the channel
# binding above), so parallel spawns each see their own label.
_current_agent_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_agent_label", default="Gru"
)


def get_current_agent_label() -> str:
    """Return the label of the agent currently executing — ``"Gru"`` on
    the main loop, ``"minion-N"`` inside a sub-agent's run. Read by
    :func:`jac.runtime.approval.make_approval_handler` to stamp the
    label onto every emitted :class:`ApprovalRequest`."""
    return _current_agent_label.get()


def _current_channel_for_ask() -> SubAgentChannel | None:
    """Lookup hook for :func:`ask_main_agent`. Exposed for tests."""
    return _current_sub_agent_channel.get()


def _bind_channel_for_worker(
    channel: SubAgentChannel,
) -> contextvars.Token[SubAgentChannel | None]:
    """Bind the channel into the current task's context. Returns the token
    so the caller can reset on teardown. Called from the worker side."""
    return _current_sub_agent_channel.set(channel)


def _get_pending_channel(spawn_id: str) -> SubAgentChannel | None:
    """Test-friendly accessor. Production code uses the dict directly."""
    return _pending_channels.get(spawn_id)


def _reset_pending_channels() -> None:
    """Cancel every parked worker, clear the registry, and reset the
    spawn counter. Test fixture hook + safety net for REPL shutdown.
    Resetting the counter here keeps every new session's first spawn at
    ``sub-1`` (and test isolation works for free)."""
    for channel in list(_pending_channels.values()):
        if channel.worker_task and not channel.worker_task.done():
            channel.worker_task.cancel()
    _pending_channels.clear()
    _reset_spawn_counter()


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
    channel: SubAgentChannel | None = None,
    *,
    spawn_id: str | None = None,
) -> SubAgentResult:
    """Build and run a sub-agent. Internal — wrapped by ``spawn_sub_agent``.

    ``channel`` is the bidirectional comms channel for D41. When provided,
    it's passed to the capability factory so ``AskMainAgentCapability``
    can be attached with the right per-spawn queues. Factories that don't
    accept ``channel`` (e.g. tests) are called positionally and never see
    the new kwarg.

    ``spawn_id`` is the human-readable label (``"minion-N"``) used to stamp
    HITL approval requests so the user can tell which agent is asking.
    Optional for back-compat with tests that build their own runner; when
    omitted, the contextvar stays at the default ``"Gru"`` (which is wrong
    for sub-agents but matches the pre-D41 behaviour and keeps simple
    test factories working).
    """
    if channel is not None:
        capabilities = list(cap.capability_factory(packet.allowed_tools, channel=channel))
        # Bind the channel into this task's context so the ask_main_agent
        # tool — which runs synchronously inside Agent.run() below — can
        # find it via contextvars without a closure. Scoped to this task,
        # not the parent's context (asyncio.Task copies on creation; set()
        # mutates only the copy).
        _bind_channel_for_worker(channel)
    else:
        capabilities = list(cap.capability_factory(packet.allowed_tools))
    # Always attach Instrumentation so spans nest under the spawn span.
    capabilities.insert(0, Instrumentation())

    # Bind the agent label so the shared approval handler can stamp
    # "minion-N" (instead of the default "Gru") onto HITL prompts raised by
    # this sub-agent's tools. The bidirectional + parallel paths already
    # invoke us via ``asyncio.create_task`` (which forks the context), but
    # the sequential path awaits us directly — that path would otherwise
    # leak the label back to Gru's context once we return. Save the token
    # and reset in the finally block so every call site is safe.
    label_token: contextvars.Token[str] | None = None
    if spawn_id is not None:
        label_token = _current_agent_label.set(spawn_id)

    instructions = _render_packet(packet, cap.base_prompt)
    sub_agent: Agent[None, str] = Agent(
        resolved.model,
        instructions=instructions,
        capabilities=capabilities,
    )

    try:
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
            bidirectional=channel is not None,
        ) as span:
            try:
                run_result = await sub_agent.run(
                    packet.objective,
                    usage_limits=None,
                )
            except Exception as exc:
                span.set_attribute("exit_status", "error")
                if channel is not None:
                    span.set_attribute("ask_main_agent_count", channel.round_trips)
                span.record_exception(exc)
                return SubAgentResult(
                    output=f"Sub-agent failed: {exc}",
                    turns_used=0,
                    resolved_tier=resolved.resolved,
                    resolved_model=resolved.model,
                    exit_status="error",
                )

            # ``AgentRunResult.usage`` is a property in current pydantic-ai
            # (it used to be a method; the deprecation warning bites the moment
            # we call it). Read once, reuse twice.
            usage = run_result.usage
            turns = int(getattr(usage, "requests", 0))
            # max_turns check is informational here — pydantic-ai's own
            # usage_limits will enforce hard caps in a follow-up. For now we
            # surface the status when the sub-agent burned the budget.
            exit_status: ExitStatus = "max_turns" if turns >= packet.max_turns else "ok"

            # Forward token usage to the main session's tracker so spawn
            # cost rolls up into session_total (per the dashboard).
            from jac.runtime.sub_agent_usage import record_sub_agent_usage

            await record_sub_agent_usage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                tier=resolved.resolved,
            )

            span.set_attribute("turns_used", turns)
            span.set_attribute("exit_status", exit_status)
            if channel is not None:
                span.set_attribute("ask_main_agent_count", channel.round_trips)

            return SubAgentResult(
                output=str(run_result.output),
                turns_used=turns,
                resolved_tier=resolved.resolved,
                resolved_model=resolved.model,
                exit_status=exit_status,
            )
    finally:
        # Restore the previous label so the sequential spawn path doesn't
        # leak "minion-N" back into Gru's context after the spawn tool returns.
        # The bidirectional + parallel paths fork context via
        # ``asyncio.create_task`` so the leak is impossible there, but
        # resetting unconditionally is cheap and keeps every path safe.
        if label_token is not None:
            _current_agent_label.reset(label_token)


def _render_final_result(result: SubAgentResult, resolved: _ResolvedTier) -> str:
    """Compose the tagged header + sub-agent output. Shared by the
    sequential, bidirectional, and respond paths so the main agent always
    sees the same shape regardless of which tool returned the result."""
    cascade_note = f", {resolved.cascade_note}" if resolved.cascaded else ""
    header = (
        f"[sub-agent tier={result.resolved_tier} model={result.resolved_model} "
        f"turns={result.turns_used} exit={result.exit_status}{cascade_note}]"
    )
    return f"{header}\n\n{result.output}"


def _render_question(spawn_id: str, question: str) -> str:
    """Compose the tool-return string surfaced to the main agent when a
    sub-agent yields a question. The ``spawn_id`` token in the body is
    the routing key the main agent must echo back via ``respond_to_sub_agent``.
    """
    return (
        f"[sub-agent → main: question pending] spawn_id={spawn_id}\n\n"
        f"{question}\n\n"
        f"Reply with `respond_to_sub_agent(reason=..., spawn_id={spawn_id!r}, "
        f"answer=...)`. You may call other tools first if you need to look "
        f"something up before answering."
    )


async def _await_completion_or_question(channel: SubAgentChannel) -> str:
    """Race the sub-agent worker finishing vs the next question landing.

    Returns the rendered tool-result string the main agent should see:
    either the final tagged header + output (worker completed), or a
    ``[sub-agent → main: question pending]`` block (worker parked on
    answer_q.get()). Pops the channel from :data:`_pending_channels`
    only when the worker completes — questions keep the channel alive
    so :func:`respond_to_sub_agent` can find it on the next main turn.
    """
    assert channel.worker_task is not None
    assert channel.resolved is not None

    question_task: asyncio.Task[str] = asyncio.create_task(channel.question_q.get())
    done, _pending = await asyncio.wait(
        [channel.worker_task, question_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel whichever wasn't the winner. Worker stays alive when the
    # question won; it'll be awaited on the next round-trip or cleaned
    # up by :func:`_reset_pending_channels` at session shutdown.
    if question_task not in done:
        question_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await question_task

    if channel.worker_task in done:
        # Worker completed first; pop the channel and surface the result.
        _pending_channels.pop(channel.spawn_id, None)
        try:
            result = channel.worker_task.result()
        except Exception as exc:
            # _run_sub_agent normally catches its own exceptions, but
            # cancellation / out-of-band errors still bubble.
            await _emit_sub_agent_event(
                SubAgentCompleted(
                    spawn_id=channel.spawn_id,
                    exit_status="error",
                    turns_used=0,
                    ask_main_agent_count=channel.round_trips,
                )
            )
            return _render_final_result(
                SubAgentResult(
                    output=f"Sub-agent failed: {exc}",
                    turns_used=0,
                    resolved_tier=channel.resolved.resolved,
                    resolved_model=channel.resolved.model,
                    exit_status="error",
                ),
                channel.resolved,
            )
        await _emit_sub_agent_event(
            SubAgentCompleted(
                spawn_id=channel.spawn_id,
                exit_status=result.exit_status,
                turns_used=result.turns_used,
                ask_main_agent_count=channel.round_trips,
            )
        )
        return _render_final_result(result, channel.resolved)

    # Question won the race. Channel stays in the registry; main agent
    # will reply via respond_to_sub_agent.
    question = question_task.result()
    return _render_question(channel.spawn_id, question)


async def _spawn_bidirectional(
    cap: SubAgentCapability,
    packet: SubAgentTaskPacket,
    resolved: _ResolvedTier,
) -> str:
    """Bidirectional path for ``spawn_sub_agent``. Wraps the sub-agent
    in a background task so the spawn tool can return mid-run when the
    sub-agent asks a question."""
    spawn_id = _mint_spawn_id()
    channel = SubAgentChannel(
        spawn_id=spawn_id,
        resolved=resolved,
        objective=packet.objective[:200],
    )
    _pending_channels[spawn_id] = channel
    channel.worker_task = asyncio.create_task(
        _run_sub_agent(cap, packet, resolved, channel, spawn_id=spawn_id)
    )

    # Tell the renderer a worker is now running. Sequential spawns get
    # visibility via the existing ToolCallStarted line; bidirectional
    # spawns need their own marker because they may park mid-run.
    await _emit_sub_agent_event(
        SubAgentSpawned(
            spawn_id=spawn_id,
            tier=resolved.resolved,
            model=resolved.model,
            objective=packet.objective[:200],
        )
    )

    try:
        return await _await_completion_or_question(channel)
    except (asyncio.CancelledError, BaseException):
        # Main agent's run was cancelled while we were parked. Tear down
        # the worker so it doesn't outlive the session.
        if channel.worker_task and not channel.worker_task.done():
            channel.worker_task.cancel()
        _pending_channels.pop(spawn_id, None)
        raise


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
        return await _spawn_bidirectional(cap, packet, resolved)

    spawn_id = _mint_spawn_id()
    result = await _run_sub_agent(cap, packet, resolved, spawn_id=spawn_id)
    return _render_final_result(result, resolved)


# ---------- bidirectional tools (D41) ----------


@jac_tool(summarizable=False)
async def ask_main_agent(
    reason: str,
    question: str,
    context: str = "",
) -> str:
    """Pause this sub-agent and ask the main agent **one** focused question.

    Use this as a *last resort* when:

    - The task packet is genuinely ambiguous about what success looks like
      and guessing wrong would waste your remaining turns, OR
    - You discovered a piece of context the packet didn't tell you about
      and the main agent has the conversation history needed to decide.

    Do NOT use this as a general chat channel. Every round-trip costs an
    extra main-agent turn (its full context + toolset), so a sub-agent
    that asks five questions costs roughly as much as five extra main-agent
    turns. The hard cap is **5 questions per spawn**; a sixth call returns
    a "finalize with what you have" directive instead of asking.

    Args:
        reason: One-sentence justification.
        question: The question for the main agent. Keep it specific and
            answerable in one or two sentences.
        context: Optional extra context the main agent needs to answer.

    Returns:
        The main agent's reply, wrapped in ``[main → sub-agent: ...]``
        markers. If the round-trip cap has been reached, returns the
        finalize directive instead — produce your final answer and list
        any open uncertainties as discrepancies.
    """
    _ = reason  # visible in the audit log; tool body doesn't reason over it

    channel = _current_channel_for_ask()
    if channel is None:
        raise JacConfigError(
            "ask_main_agent is not available — bidirectional comms is "
            "disabled or this tool was called outside a sub-agent context. "
            "Set `cost.sub_agent_bidirectional: true` in config to enable."
        )

    if channel.round_trips >= channel.cap:
        logfire.warning(
            "ask_main_agent.cap_reached",
            spawn_id=channel.spawn_id,
            round_trips=channel.round_trips,
            cap=channel.cap,
        )
        return f"[main → sub-agent: {_BIDIRECTIONAL_FINALIZE_DIRECTIVE}]"

    channel.round_trips += 1
    if channel.round_trips == _BIDIRECTIONAL_WARN_AT:
        logfire.warning(
            "ask_main_agent.warn_threshold",
            spawn_id=channel.spawn_id,
            round_trips=channel.round_trips,
            cap=channel.cap,
        )

    payload = question if not context else f"{question}\n\nAdditional context:\n{context}"
    # Renderer hook: the question is about to land on the main agent's
    # tool result. Emit BEFORE put() so scroll-back order is intuitive.
    await _emit_sub_agent_event(
        SubAgentQuestion(
            spawn_id=channel.spawn_id,
            question=payload,
            round_trip=channel.round_trips,
        )
    )
    await channel.question_q.put(payload)
    answer = await channel.answer_q.get()
    return f"[main → sub-agent: {answer}]"


@jac_tool(summarizable=False)
async def respond_to_sub_agent(
    reason: str,
    spawn_id: str,
    answer: str,
) -> str:
    """Reply to a paused sub-agent's pending question.

    Pair to :func:`ask_main_agent`. Call this when ``spawn_sub_agent``
    returned a ``[sub-agent → main: question pending] spawn_id=...`` block.
    Pass the ``spawn_id`` from that block plus your answer; the sub-agent
    resumes and you'll get either its final result or its next question
    back from this tool.

    Args:
        reason: One-sentence justification.
        spawn_id: The id from the question block (8 hex chars).
        answer: Your reply. Keep it focused — the sub-agent only asked
            one question.

    Returns:
        Either the sub-agent's final tagged result (worker completed),
        or another ``[sub-agent → main: question pending]`` block if it
        has a follow-up question.
    """
    _ = reason
    channel = _pending_channels.get(spawn_id)
    if channel is None:
        return (
            f"[error: no pending sub-agent with spawn_id={spawn_id!r}; it may have "
            f"already finished or never existed. Active spawn_ids: "
            f"{sorted(_pending_channels)!r}]"
        )
    if channel.worker_task is None or channel.worker_task.done():
        _pending_channels.pop(spawn_id, None)
        return (
            f"[error: sub-agent {spawn_id!r} already finished while you were "
            f"composing your reply; your answer was not delivered]"
        )

    # Renderer hook before delivery so the user sees the answer in
    # scroll-back before whatever the sub-agent does with it.
    await _emit_sub_agent_event(SubAgentAnswer(spawn_id=spawn_id, answer=answer))
    await channel.answer_q.put(answer)
    return await _await_completion_or_question(channel)


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


@jac_tool(summarizable=True)
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
    parallel variant.
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
            return await _run_sub_agent(cap, spec.task_packet, r, spawn_id=_mint_spawn_id())

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
