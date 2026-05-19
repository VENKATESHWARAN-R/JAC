"""Construct the Gru agent.

Phase 0: a bare ``Agent``. No tools, no memory persistence, no capabilities
beyond the global Logfire instrumentation set up in
``jac.capabilities.observability``. Subsequent phases attach the filesystem,
shell, memory, and minion-factory capabilities here.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent

from jac.config import settings

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "gru_system.md"


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_gru(model_override: str | None = None) -> Agent[None, str]:
    """Build the Gru agent.

    Args:
        model_override: optional model id (e.g. ``"anthropic:claude-opus-4-6"``).
            Falls back to ``JAC_MODEL`` env / the pydantic-settings default.

    Returns:
        A ready-to-run ``Agent``. The caller owns the run loop.
    """
    model = model_override or settings.model
    return Agent(
        model,
        instructions=_load_system_prompt(),
    )
