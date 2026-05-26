"""The ``@jac_tool`` decorator.

Every tool exposed to Gru or a sub-agent **must** be decorated with
``@jac_tool``. The decorator validates structurally that the function's
first non-``ctx`` parameter is annotated ``reason: str`` — see
docs/architecture.md §6a.

Two call forms:

    @jac_tool
    def foo(reason: str, ...): ...

    @jac_tool(summarizable=True)
    def run_shell(reason: str, ...): ...

``summarizable=True`` opts the tool into AI summarization of large outputs
via :mod:`jac.runtime.tool_summarize` (Phase A.1). Default is ``False`` —
tools whose output is structurally exact (``read_file``, ``list_dir``, …)
must never be summarized.

Decoration is the fail-first guard. ``jac_function_toolset`` additionally
asserts every tool went through here, catching tools registered via other
paths at agent construction.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable

_JAC_TOOL_MARKER = "__jac_tool__"
_SUMMARIZABLE_MARKER = "__jac_tool_summarizable__"


def _tool_qualname(func: Callable[..., object]) -> str:
    return getattr(func, "__qualname__", repr(func))


def _resolve_annotation(func: Callable[..., object], name: str, raw: object) -> object:
    """Resolve a parameter annotation that may be a string (PEP 563)."""
    if raw is inspect.Parameter.empty:
        return raw
    # When ``from __future__ import annotations`` is in effect, annotations are
    # strings. Try to evaluate them against the function's module globals.
    try:
        resolved = typing.get_type_hints(func)
    except Exception:  # NameError, etc. — fall back to the raw value
        return raw
    return resolved.get(name, raw)


def _validate_and_mark[F: Callable[..., object]](func: F, *, summarizable: bool) -> F:
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    # Tolerate a leading RunContext-style ``ctx`` parameter.
    if params and params[0].name == "ctx":
        params = params[1:]

    if not params:
        raise TypeError(
            f"@jac_tool {_tool_qualname(func)}: missing required `reason: str` parameter. "
            "Every JAC tool must justify its call — see docs/architecture.md §6a."
        )

    first = params[0]
    if first.name != "reason":
        raise TypeError(
            f"@jac_tool {_tool_qualname(func)}: first non-ctx parameter must be named "
            f"`reason`, got `{first.name}`. See docs/architecture.md §6a."
        )
    annotation = _resolve_annotation(func, first.name, first.annotation)
    if annotation is not str:
        raise TypeError(
            f"@jac_tool {_tool_qualname(func)}: `reason` must be annotated `str`, "
            f"got `{annotation!r}`. See docs/architecture.md §6a."
        )

    setattr(func, _JAC_TOOL_MARKER, True)
    setattr(func, _SUMMARIZABLE_MARKER, bool(summarizable))
    return func


def jac_tool[F: Callable[..., object]](
    func: F | None = None,
    /,
    *,
    summarizable: bool = False,
) -> F | Callable[[F], F]:
    """Mark ``func`` as a JAC tool and validate its signature.

    Supports both ``@jac_tool`` (bare) and ``@jac_tool(summarizable=True)``.
    The first parameter (or first parameter after a leading ``ctx``) must be
    named ``reason`` and annotated ``str``. Validation runs at decoration
    time, so any breach surfaces when the module loads.

    Args:
        func: Function being decorated (only when called bare).
        summarizable: When ``True``, large outputs from this tool may be
            routed through the small-tier summarizer (see
            :mod:`jac.runtime.tool_summarize`). Default ``False`` — tools
            whose output is structurally exact must keep this off.

    Raises:
        TypeError: if ``func`` is missing the ``reason: str`` parameter
            or it isn't where expected.
    """
    if func is not None:
        # Bare form: @jac_tool
        return _validate_and_mark(func, summarizable=summarizable)

    # Parameterized form: @jac_tool(summarizable=True)
    def _decorator(fn: F) -> F:
        return _validate_and_mark(fn, summarizable=summarizable)

    return _decorator


def is_jac_tool(func: object) -> bool:
    """True if ``func`` was decorated with :func:`jac_tool`."""
    return getattr(func, _JAC_TOOL_MARKER, False) is True


def is_summarizable(func: object) -> bool:
    """True if the tool opted into summarization via ``summarizable=True``."""
    return getattr(func, _SUMMARIZABLE_MARKER, False) is True
