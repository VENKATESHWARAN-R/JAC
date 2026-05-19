"""Construct the Gru agent.

Phase 0.5: bare ``Agent`` with a layered system prompt and auto-loaded
AGENTS.md context. No tools and no per-agent capabilities yet beyond the
global Logfire instrumentation set up in
:mod:`jac.capabilities.observability`.

Subsequent phases attach the filesystem, shell, memory, and minion-factory
capabilities here. The model identifier and prompt source are not
hardcoded — both flow through the layered workspace.
"""

from __future__ import annotations

from pydantic_ai import Agent

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.workspace.context import load_session_context
from jac.workspace.prompts import load_prompt


def _compose_instructions() -> str:
    base = load_prompt("gru_system").strip()
    context = load_session_context()
    return f"{base}\n\n---\n\n# Session context\n\n{context}"


def build_gru(model_override: str | None = None) -> Agent[None, str]:
    """Build the Gru agent.

    Args:
        model_override: optional model id (e.g. ``"anthropic:claude-opus-4-6"``).
            Falls back to the layered config (env / project / user / package).
            **No package default** — fail-first if nothing is configured.

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
    return Agent(model, instructions=_compose_instructions())
