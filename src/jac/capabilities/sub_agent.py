"""Sub-agent capability — exposes ``spawn_sub_agent`` / ``spawn_sub_agents``.

Thin wrapper around the two spawn tools in :mod:`jac.runtime.sub_agent`.
The real implementation, models, and tier cascade live there; this file
exists to plug the tools into a standard Pydantic AI ``AbstractCapability``
and make them approval-gated.

**Depth cap = 1 (D40) is structurally enforced** by the
``SubAgentCapability`` factory in :mod:`jac.runtime.sub_agent`: the
capabilities passed to a spawned sub-agent never include this module.
Sub-agents therefore cannot call either spawn tool — they literally
aren't in the sub-agent toolset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.runtime.sub_agent import spawn_sub_agent, spawn_sub_agents
from jac.tools import jac_function_toolset


def _always_require(*_args: Any, **_kwargs: Any) -> bool:
    return True


@dataclass
class SubAgentToolCapability(AbstractCapability[Any]):
    """Registers both spawn tools on the main agent. Always approval-gated."""

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(spawn_sub_agent, spawn_sub_agents)
        return toolset.approval_required(_always_require)
