"""Sub-agent runtime (Phase B + the Phase 4 suspend/resume comms redesign).

The main agent delegates context-heavy work to an isolated sub-agent via the
``spawn_sub_agent`` tool. The sub-agent runs in its *own* Agent loop with its
*own* message history, so the intermediate 50k-200k tokens of file reads,
shell output, web fetches, etc. stay in the sub-agent's context — only the
final result returns to the main agent.

Design: see ``docs/design/cost-efficient-orchestration.md`` §4 and the review
tracker ``docs/design/audit/2026-05-30-review.md`` (R7a split + R7b transport).

This package was split out of the former 1k-line ``sub_agent.py`` (R7a):

- :mod:`~jac.runtime.sub_agent.tiers` — tier names + cascade resolution.
- :mod:`~jac.runtime.sub_agent.packet` — the task packet / spec / result models.
- :mod:`~jac.runtime.sub_agent.state` — session-scoped state (capability factory,
  event-bus indirection, the ``minion-N`` agent-label contextvar + counter).
- :mod:`~jac.runtime.sub_agent.runner` — worker-Agent construction (incl. the
  ``allowed_tools`` filter and the external ``ask_supervisor`` tool) and the
  simple single-run path.
- :mod:`~jac.runtime.sub_agent.suspend` — the suspend/resume transport
  (PendingSpawn registry + drive loop), modeled on A2A ``input-required``.
- :mod:`~jac.runtime.sub_agent.tools` — the ``spawn_sub_agent`` /
  ``spawn_sub_agents`` / ``respond_to_sub_agent`` tools.

Everything the rest of the codebase imported from the old module is re-exported
here, so ``from jac.runtime.sub_agent import X`` keeps working unchanged.
"""

from __future__ import annotations

from jac.runtime.sub_agent.packet import (
    ExitStatus,
    SubAgentResult,
    SubAgentSpawnSpec,
    SubAgentTaskPacket,
)
from jac.runtime.sub_agent.runner import (
    _ALWAYS_ALLOWED_SUB_AGENT_TOOLS,
    _ASK_SUPERVISOR_DEF,
    _ask_supervisor_toolset,
    _build_worker_agent,
    _extract_question,
    _first_ask_call,
    _make_allowed_tools_filter,
    _record_run_usage,
    _render_final_result,
    _render_packet,
    _render_question,
    _run_sub_agent,
)
from jac.runtime.sub_agent.state import (
    SubAgentCapability,
    _current_agent_label,
    _emit_sub_agent_event,
    _mint_spawn_id,
    _reset_spawn_counter,
    get_current_agent_label,
    get_sub_agent_capability,
    set_sub_agent_capability,
    set_sub_agent_event_bus,
)
from jac.runtime.sub_agent.suspend import (
    _BIDIRECTIONAL_FINALIZE_DIRECTIVE,
    _BIDIRECTIONAL_ROUND_TRIP_CAP,
    _BIDIRECTIONAL_WARN_AT,
    _MAX_WORKER_RUNS,
    PendingSpawn,
    _drive_worker,
    _force_complete,
    _get_pending_spawn,
    _pending_spawns,
    _reset_pending_spawns,
    _spawn_with_suspension,
)
from jac.runtime.sub_agent.tiers import (
    _TIER_CASCADE,
    TierName,
    _ResolvedTier,
    resolve_tier,
)
from jac.runtime.sub_agent.tools import (
    _render_spawn_block,
    respond_to_sub_agent,
    spawn_sub_agent,
    spawn_sub_agents,
)

__all__ = [  # noqa: RUF022 — grouped by submodule for readability, not alphabetical
    # packet
    "ExitStatus",
    "SubAgentResult",
    "SubAgentSpawnSpec",
    "SubAgentTaskPacket",
    # tiers
    "TierName",
    "resolve_tier",
    "_ResolvedTier",
    "_TIER_CASCADE",
    # state
    "SubAgentCapability",
    "set_sub_agent_capability",
    "get_sub_agent_capability",
    "set_sub_agent_event_bus",
    "get_current_agent_label",
    "_current_agent_label",
    "_emit_sub_agent_event",
    "_mint_spawn_id",
    "_reset_spawn_counter",
    # runner
    "_ALWAYS_ALLOWED_SUB_AGENT_TOOLS",
    "_make_allowed_tools_filter",
    "_ASK_SUPERVISOR_DEF",
    "_ask_supervisor_toolset",
    "_render_packet",
    "_build_worker_agent",
    "_record_run_usage",
    "_run_sub_agent",
    "_render_final_result",
    "_render_question",
    "_extract_question",
    "_first_ask_call",
    # suspend
    "PendingSpawn",
    "_pending_spawns",
    "_get_pending_spawn",
    "_reset_pending_spawns",
    "_drive_worker",
    "_force_complete",
    "_spawn_with_suspension",
    "_BIDIRECTIONAL_ROUND_TRIP_CAP",
    "_BIDIRECTIONAL_WARN_AT",
    "_MAX_WORKER_RUNS",
    "_BIDIRECTIONAL_FINALIZE_DIRECTIVE",
    # tools
    "spawn_sub_agent",
    "spawn_sub_agents",
    "respond_to_sub_agent",
    "_render_spawn_block",
]
