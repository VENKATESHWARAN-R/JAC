"""Sub-agent capability — exposes ``spawn_sub_agent`` to the main agent.

Thin wrapper around :func:`jac.runtime.sub_agent.spawn_sub_agent`. The
real implementation, models, and tier cascade live in
:mod:`jac.runtime.sub_agent`; this file exists to plug the tool into a
standard Pydantic AI ``AbstractCapability`` and make it approval-gated.

**Depth cap = 1 (D40) is structurally enforced** by the
``SubAgentCapability`` factory in :mod:`jac.runtime.sub_agent`: the
capabilities passed to a spawned sub-agent never include this module.
Sub-agents therefore cannot call ``spawn_sub_agent`` — the tool
literally isn't in their toolset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.runtime.sub_agent import spawn_sub_agent
from jac.tools import jac_function_toolset


def _always_require(*_args: Any, **_kwargs: Any) -> bool:
    return True


@dataclass
class SubAgentToolCapability(AbstractCapability[Any]):
    """Registers ``spawn_sub_agent`` on the main agent. Always approval-gated."""

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(spawn_sub_agent)
        return toolset.approval_required(_always_require)
