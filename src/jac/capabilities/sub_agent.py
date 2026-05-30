"""Sub-agent capabilities — exposes the spawn + bidirectional tools.

Thin wrappers around the implementations in :mod:`jac.runtime.sub_agent`.
This file exists to plug the tools into standard Pydantic AI
``AbstractCapability`` classes and apply the right approval posture.

**Depth cap = 1 (D40) is structurally enforced** by the
``SubAgentCapability`` factory in :mod:`jac.runtime.sub_agent`: the
capabilities passed to a spawned sub-agent never include
``SubAgentToolCapability`` or ``RespondToSubAgentCapability``. Sub-agents
therefore cannot recurse, even via the bidirectional channel.

**Bidirectional posture (Phase 4 suspend/resume).** ``respond_to_sub_agent``
is *not* approval-gated. The user already approved the parent
``spawn_sub_agent`` call; adding a prompt for every reply inside that
approved conversation would be noise. Visibility is provided by the renderer
markers ``[sub-agent → main]`` / ``[main → sub-agent]`` emitted on the event
bus. The worker side (``ask_supervisor``) is no longer a JAC ``Capability``:
it's an *external* tool the runner attaches in
:func:`jac.runtime.sub_agent._build_worker_agent`, because suspending on it
requires ``DeferredToolRequests`` in the worker Agent's output types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.runtime.sub_agent import (
    respond_to_sub_agent,
    spawn_sub_agent,
    spawn_sub_agents,
)
from jac.tools import jac_function_toolset


def _always_require(*_args: Any, **_kwargs: Any) -> bool:
    return True


@dataclass
class SubAgentToolCapability(AbstractCapability[Any]):
    """Registers both spawn tools on the main agent. Always approval-gated."""

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(spawn_sub_agent, spawn_sub_agents)
        return toolset.approval_required(_always_require)


@dataclass
class RespondToSubAgentCapability(AbstractCapability[Any]):
    """Main-agent side of bidirectional comms (D41). Registers
    ``respond_to_sub_agent`` without approval — the parent spawn was
    already approved. Attached only when ``cost.sub_agent_bidirectional``
    is enabled (the wiring decision lives in :mod:`jac.runtime.gru`)."""

    def get_toolset(self) -> Any:
        return jac_function_toolset(respond_to_sub_agent)
