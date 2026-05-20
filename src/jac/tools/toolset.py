"""JAC toolset construction with structural enforcement.

:func:`jac_function_toolset` is the **only** sanctioned way to assemble a
``FunctionToolset`` of JAC tools. It asserts that every function went through
:func:`jac.tools.decorator.jac_tool` — catching tools registered via other
paths at construction time, not runtime. See ARCHITECTURE.md §6a.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic_ai.toolsets import FunctionToolset

from jac.tools.decorator import is_jac_tool


def jac_function_toolset(*funcs: Callable[..., Any]) -> FunctionToolset:
    """Build a ``FunctionToolset`` from JAC tools.

    Every function must have been decorated with :func:`jac_tool`. The check
    runs at construction time so import errors surface at module load, not
    during the agent's first tool call.

    Raises:
        TypeError: if any function in ``funcs`` isn't a JAC tool.
    """
    for f in funcs:
        if not is_jac_tool(f):
            raise TypeError(
                f"{f.__qualname__} is not decorated with @jac_tool. "
                "Every JAC tool must justify its call. See ARCHITECTURE.md §6a."
            )
    return FunctionToolset(tools=list(funcs))
