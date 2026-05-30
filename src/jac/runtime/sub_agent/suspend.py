"""Suspend/resume transport for bidirectional sub-agent comms (Phase 4).

Replaces the D41 live-channel transport. A worker that calls the external
``ask_supervisor`` tool doesn't block — its ``agent.run`` *returns* a
``DeferredToolRequests`` and the run is suspended, its history checkpointed
in a :class:`PendingSpawn` (plain serializable state, no live coroutine).
The main agent answers and ``respond_to_sub_agent`` resumes the worker from
the saved history plus the answer. Modeled on A2A ``input-required``.
"""

from __future__ import annotations

from dataclasses import dataclass

import logfire
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage

from jac.runtime.events import SubAgentCompleted, SubAgentQuestion, SubAgentSpawned
from jac.runtime.sub_agent.packet import ExitStatus, SubAgentResult, SubAgentTaskPacket
from jac.runtime.sub_agent.runner import (
    _build_worker_agent,
    _extract_question,
    _first_ask_call,
    _record_run_usage,
    _render_final_result,
    _render_question,
)
from jac.runtime.sub_agent.state import (
    SubAgentCapability,
    _current_agent_label,
    _emit_sub_agent_event,
    _mint_spawn_id,
    _reset_spawn_counter,
)
from jac.runtime.sub_agent.tiers import _ResolvedTier

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
