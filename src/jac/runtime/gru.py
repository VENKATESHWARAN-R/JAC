"""Construct the Gru agent.

Phase 1: bare ``Agent`` with a layered system prompt, AGENTS.md session
context, and optional extra capabilities passed in by the caller (e.g.
the event-bus hooks installed by the CLI). No tools yet — those land in
Phase 1 step 2+, attached via ``extra_capabilities`` so this function
doesn't need to know about them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.workspace.context import load_session_context
from jac.workspace.prompts import load_prompt


def _compose_instructions() -> str:
    base = load_prompt("gru_system").strip()
    context = load_session_context()
    return f"{base}\n\n---\n\n# Session context\n\n{context}"


def build_gru(
    model_override: str | None = None,
    extra_capabilities: Sequence[Any] | None = None,
) -> Agent[None, str]:
    """Build the Gru agent.

    Args:
        model_override: optional model id (e.g. ``"anthropic:claude-opus-4-6"``).
            Falls back to the layered config (env / project / user / package).
            **No package default** — fail-first if nothing is configured.
        extra_capabilities: additional Pydantic AI capabilities to attach.
            The CLI uses this to wire ``Hooks`` → :class:`EventBus` → renderer;
            later phases will pass tool capabilities, memory, etc.

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
    return Agent(model, instructions=_compose_instructions(), capabilities=capabilities)
