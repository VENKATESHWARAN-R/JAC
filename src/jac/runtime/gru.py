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
from jac.errors import JacConfigError

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "gru_system.md"


def _load_system_prompt() -> str:
    # Phase 0.5 will replace this with a workspace-aware loader
    # (project → user → package). Today: package only.
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_gru(model_override: str | None = None) -> Agent[None, str]:
    """Build the Gru agent.

    Args:
        model_override: optional model id (e.g. ``"anthropic:claude-opus-4-6"``).
            Falls back to ``JAC_MODEL`` env / config file. **No package
            default** — fail-first if nothing is configured.

    Returns:
        A ready-to-run ``Agent``. The caller owns the run loop.

    Raises:
        JacConfigError: if no model is configured anywhere in the layered config.
    """
    model = model_override or settings.model
    if not model:
        raise JacConfigError(
            "No model is configured. Set one of: "
            "(1) JAC_MODEL in your environment (see .env.template), "
            "(2) the --model flag on the CLI, "
            "or (3) `model = \"...\"` in ~/.jac/config.toml "
            "(workspace config arrives in Phase 0.5; use env or --model for now)."
        )
    return Agent(
        model,
        instructions=_load_system_prompt(),
    )
