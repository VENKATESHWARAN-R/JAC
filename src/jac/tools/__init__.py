"""JAC tools — capabilities providing concrete tools for Gru and minions.

Every tool exposed to Gru or a minion **must** be decorated with
:func:`jac.tools.decorator.jac_tool`, which enforces the ``reason: str``
first-argument requirement. See docs/architecture.md §6a.

Phase 1 step 1 ships only the decorator; concrete tools (filesystem, shell,
search) arrive in subsequent steps and live as sibling modules here.
"""

from jac.tools.decorator import is_jac_tool, is_summarizable, jac_tool
from jac.tools.toolset import jac_function_toolset, restrict_toolset, summarizing_wrap

__all__ = [
    "is_jac_tool",
    "is_summarizable",
    "jac_function_toolset",
    "jac_tool",
    "restrict_toolset",
    "summarizing_wrap",
]
