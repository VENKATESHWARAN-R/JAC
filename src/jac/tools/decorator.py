"""The ``@jac_tool`` decorator.

Every tool exposed to Gru or a minion **must** be decorated with
``@jac_tool``. The decorator validates structurally that the function's
first non-``ctx`` parameter is annotated ``reason: str`` — see
ARCHITECTURE.md §6a.

Decoration is the fail-first guard. A future ``JacToolset`` wrapper will
additionally assert that every tool in a JAC toolset went through this
decorator, catching tools registered via other paths.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])

_JAC_TOOL_MARKER = "__jac_tool__"


def jac_tool(func: F) -> F:
    """Mark ``func`` as a JAC tool and validate its signature.

    The first parameter (or first parameter after a leading ``ctx``) must
    be named ``reason`` and annotated ``str``. Validation is at decoration
    time, so the error surfaces when the module loads, not at runtime.

    Raises:
        TypeError: if ``func`` is missing the ``reason: str`` parameter or
            it isn't where expected.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    # Tolerate a leading RunContext-style ``ctx`` parameter.
    if params and params[0].name == "ctx":
        params = params[1:]

    if not params:
        raise TypeError(
            f"@jac_tool {func.__qualname__}: missing required `reason: str` parameter. "
            "Every JAC tool must justify its call — see ARCHITECTURE.md §6a."
        )

    first = params[0]
    if first.name != "reason":
        raise TypeError(
            f"@jac_tool {func.__qualname__}: first non-ctx parameter must be named "
            f"`reason`, got `{first.name}`. See ARCHITECTURE.md §6a."
        )
    if first.annotation is not str:
        raise TypeError(
            f"@jac_tool {func.__qualname__}: `reason` must be annotated `str`, "
            f"got `{first.annotation!r}`. See ARCHITECTURE.md §6a."
        )

    setattr(func, _JAC_TOOL_MARKER, True)
    return func


def is_jac_tool(func: object) -> bool:
    """True if ``func`` was decorated with :func:`jac_tool`."""
    return getattr(func, _JAC_TOOL_MARKER, False) is True
