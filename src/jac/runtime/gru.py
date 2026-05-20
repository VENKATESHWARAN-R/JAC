"""Construct the Gru agent.

Phase 2a: Gru ships with filesystem, search, shell, **memory**, and
history capabilities. Risky tools (``write_file``, ``edit_file``,
``run_shell``, ``remember``) are approval-required; the CLI's
:class:`make_approval_handler` capability turns those deferred calls into
bus-mediated approval prompts.

Callers can pass ``extra_capabilities`` to attach hooks (event bus),
approval handlers, or future minion-factory capabilities without touching
this function. Set ``include_default_tools=False`` for headless / test
contexts that don't want filesystem access.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent

from jac.capabilities.filesystem import FilesystemCapability
from jac.capabilities.history import make_history_capability
from jac.capabilities.memory import MemoryCapability
from jac.capabilities.search import SearchCapability
from jac.capabilities.shell import ShellCapability
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.workspace.context import load_session_context
from jac.workspace.prompts import load_prompt


def _compose_instructions() -> str:
    base = load_prompt("gru_system").strip()
    context = load_session_context()
    return f"{base}\n\n---\n\n# Session context\n\n{context}"


def _default_tool_capabilities() -> list[Any]:
    """The standard tool + history capabilities every interactive JAC session gets."""
    return [
        FilesystemCapability(),
        SearchCapability(),
        ShellCapability(),
        MemoryCapability(),
        make_history_capability(),
    ]


def build_gru(
    model_override: str | None = None,
    extra_capabilities: Sequence[Any] | None = None,
    include_default_tools: bool = True,
) -> Agent[None, str]:
    """Build the Gru agent.

    Args:
        model_override: optional model id (e.g. ``"anthropic:claude-opus-4-6"``).
            Falls back to the layered config (env / project / user / package).
            **No package default** — fail-first if nothing is configured.
        extra_capabilities: capabilities to attach **before** the default tools.
            The CLI passes its ``Hooks`` and approval-handler capabilities here.
        include_default_tools: when ``True`` (default), attaches filesystem,
            search, and shell capabilities. Set ``False`` for tests or headless
            contexts that don't need filesystem access.

    Returns:
        A ready-to-run ``Agent``.

    Raises:
        JacConfigError: if no model is configured anywhere in the layered config.
    """
    model = model_override or get_settings().model
    if not model:
        raise JacConfigError(
            "No model is configured. Set one of: "
            "(1) JAC_MODEL in your environment (see .env.template), "
            "(2) the --model flag on the CLI, "
            '(3) `model: "..."` in ~/.jac/config.yaml, '
            "or (4) run `jac init` for an interactive setup wizard."
        )
    capabilities: list[Any] = list(extra_capabilities or [])
    if include_default_tools:
        capabilities.extend(_default_tool_capabilities())
    return Agent(model, instructions=_compose_instructions(), capabilities=capabilities)
