"""Shell capability — ``run_shell``.

**Always approval-required.** Shell is the heaviest tool: it can do anything
the user can do on this machine. v1 has no sandbox; HITL is the safety net.
Sandboxing (Monty + ``sandbox-exec`` / ``bwrap``) lands in v2 YOLO mode.

The command runs with the project root as CWD, with a default 30-second
timeout. Output is returned verbatim — large outputs are routed through
the tool result post-processor (:mod:`jac.runtime.tool_summarize`) at the
toolset boundary when a small-tier model is configured and strictly cheaper
than the current tier. See ``cost.*`` settings.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.tools import jac_function_toolset, jac_tool
from jac.workspace.paths import find_project_root

_DEFAULT_TIMEOUT_S = 30.0


@jac_tool(summarizable=True)
def run_shell(reason: str, command: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> str:
    """Execute ``command`` in a shell, CWD = project root.

    Returns a formatted block with exit code, stdout, and stderr.
    **Always approval-required** — surfaces a prompt to the user via the bus.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=find_project_root(),
        )
    except subprocess.TimeoutExpired:
        return f"$ {command}\n[timed out after {timeout_s}s]"

    parts: list[str] = [f"$ {command}", f"[exit={result.returncode}]"]
    if result.stdout:
        parts.append("--- stdout ---")
        parts.append(result.stdout.rstrip())
    if result.stderr:
        parts.append("--- stderr ---")
        parts.append(result.stderr.rstrip())
    return "\n".join(parts)


def _always_require(*_args: Any, **_kwargs: Any) -> bool:
    return True


@dataclass
class ShellCapability(AbstractCapability[Any]):
    """Shell execution — every call needs explicit user approval."""

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(run_shell)
        return toolset.approval_required(_always_require)
