"""The sub-agent tools exposed to the agents.

- ``spawn_sub_agent`` / ``spawn_sub_agents`` — main-agent tools to delegate
  one or many context-heavy tasks to isolated workers.
- ``respond_to_sub_agent`` — main-agent reply that resumes a suspended worker.

The worker-side ``ask_supervisor`` is *not* here: it's an external tool the
runner attaches (:mod:`jac.runtime.sub_agent.runner`), because suspending on
it needs ``DeferredToolRequests`` in the worker Agent's output types.
"""

from __future__ import annotations

import asyncio
from typing import Any

import logfire

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.runtime.events import SubAgentCompleted, SubAgentQuestion, SubAgentSpawned
from jac.runtime.sub_agent.packet import SubAgentResult, SubAgentSpawnSpec, SubAgentTaskPacket
from jac.runtime.sub_agent.runner import (
    _render_final_result,
    _render_question,
    _run_sub_agent,
)
from jac.runtime.sub_agent.state import (
    _emit_sub_agent_event,
    _mint_spawn_id,
    get_sub_agent_capability,
)
from jac.runtime.sub_agent.suspend import (
    _drive_worker,
    _pending_spawns,
    _spawn_with_suspension,
)
from jac.runtime.sub_agent.tiers import _ResolvedTier, resolve_tier
from jac.tools import jac_tool


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
    from jac.runtime.events import SubAgentAnswer

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
