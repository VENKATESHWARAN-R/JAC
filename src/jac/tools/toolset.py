"""JAC toolset construction with structural enforcement + post-processing.

:func:`jac_function_toolset` is the **only** sanctioned way to assemble a
toolset of JAC tools. Two things happen here that don't happen with a plain
``FunctionToolset``:

1. Every function is asserted to carry the :func:`jac_tool` marker — see
   docs/architecture.md §6a. Catches tools registered via other paths at
   construction time, not runtime.
2. The toolset is wrapped in :class:`SummarizingToolset` so that large
   outputs from tools opted into ``@jac_tool(summarizable=True)`` get
   routed through the small-tier model. See
   :mod:`jac.runtime.tool_summarize`.

Capabilities that need approval still chain ``.approval_required(...)`` on
top — that wraps the already-wrapped toolset, so the layering is
``ApprovalRequired → Summarizing → Function``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

from jac.tools.decorator import is_jac_tool, is_summarizable


@dataclass
class SummarizingToolset(WrapperToolset[Any]):
    """Wrap a toolset so large outputs route through the summarizer.

    Holds the set of tool names whose decorator opted in via
    ``@jac_tool(summarizable=True)``. User overrides
    (``cost.summarize_tools`` / ``cost.no_summarize_tools``) still apply
    on top — see :func:`jac.runtime.tool_summarize.should_summarize`.
    """

    summarizable_tools: frozenset[str] = field(default_factory=frozenset)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: Any,
    ) -> Any:
        # Defer the import — tool_summarize pulls in logfire and pydantic_ai.direct,
        # and we want the toolset module to stay cheap.
        from jac.runtime.tool_summarize import maybe_summarize_tool_result

        result = await super().call_tool(name, tool_args, ctx, tool)

        current_model: str | None = None
        if ctx.model is not None:
            system = getattr(ctx.model, "system", None)
            model_name = getattr(ctx.model, "model_name", None)
            if system and model_name:
                current_model = f"{system}:{model_name}"

        return await maybe_summarize_tool_result(
            tool_name=name,
            raw_output=result,
            tool_summarizable=name in self.summarizable_tools,
            current_model=current_model,
            tool_call_id=ctx.tool_call_id,
        )


def jac_function_toolset(*funcs: Callable[..., Any]) -> SummarizingToolset:
    """Build a JAC toolset from decorated tool functions.

    Every function must have been decorated with :func:`jac_tool`. The
    structural check runs at construction time so import errors surface at
    module load, not during the agent's first tool call.

    Returns a :class:`SummarizingToolset` wrapping the underlying
    ``FunctionToolset``. Capabilities can still chain
    ``.approval_required(...)``; the resulting stack runs approval first,
    then summarization on the inner return value.

    Raises:
        TypeError: if any function in ``funcs`` isn't a JAC tool.
    """
    for f in funcs:
        if not is_jac_tool(f):
            raise TypeError(
                f"{getattr(f, '__qualname__', repr(f))} is not decorated with @jac_tool. "
                "Every JAC tool must justify its call. See docs/architecture.md §6a."
            )
    inner = FunctionToolset(tools=list(funcs))
    summarizable = frozenset(getattr(f, "__name__", "") for f in funcs if is_summarizable(f))
    return SummarizingToolset(wrapped=inner, summarizable_tools=summarizable)
