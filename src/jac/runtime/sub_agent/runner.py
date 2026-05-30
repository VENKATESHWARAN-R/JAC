"""Worker-agent construction + the simple (non-suspending) run path.

Everything needed to *build* a sub-agent ``Agent`` from a packet and run it
once to completion lives here: the ``allowed_tools`` filter (R2), the
external ``ask_supervisor`` tool definition, prompt rendering, usage
forwarding, and :func:`_run_sub_agent` (the single-run path used by the
non-bidirectional and parallel spawns). The suspend/resume drive loop that
builds on these lives in :mod:`jac.runtime.sub_agent.suspend`.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Awaitable, Callable
from typing import Any

import logfire
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.capabilities import Instrumentation, PrepareTools
from pydantic_ai.tools import ToolCallPart, ToolDefinition
from pydantic_ai.toolsets import ExternalToolset

from jac.runtime.sub_agent.packet import ExitStatus, SubAgentResult, SubAgentTaskPacket
from jac.runtime.sub_agent.state import (
    SubAgentCapability,
    _current_agent_label,
)
from jac.runtime.sub_agent.tiers import _ResolvedTier
from jac.workspace.context import load_agents_context

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
    The bidirectional single-spawn path uses ``_drive_worker`` instead.

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
