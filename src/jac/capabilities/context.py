"""Session context capability — dynamic AGENTS.md + memory.md injection.

The base Gru prompt + user/project AGENTS.md + user/project memory.md
form Gru's system context. Older versions of JAC baked all of this into a
static ``instructions=`` string at agent construction time, which meant
mid-session ``remember()`` writes were invisible until the agent was
rebuilt.

This capability uses Pydantic AI's ``get_instructions()`` to return a
callable: every model request re-reads the files and re-renders the
clock-line. The Gru agent sees the latest memory.md without any rebuild.

The base prompt (``prompts/gru_system.md`` and friends) is loaded once at
construction and concatenated with the dynamic context — base prompts
don't change at runtime, so re-reading them per request would be wasted
I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from jac.workspace.context import load_session_context


@dataclass
class ContextCapability(AbstractCapability[Any]):
    """Inject base prompt + dynamic session context into Gru's instructions.

    ``base_prompt`` is the static prefix (the ``gru_system.md`` body
    loaded once at construction). The dynamic suffix
    (:func:`load_session_context`) is re-evaluated per model request so
    fresh ``remember()`` writes are visible immediately.
    """

    base_prompt: str

    def get_instructions(self) -> Any:
        base = self.base_prompt

        def _instructions(_ctx: Any) -> str:
            # === Prompt cache boundary (Phase A.2) ===
            # Everything above this point is the static gru_system.md body,
            # captured once at capability construction. Everything below is
            # dynamic but must stay STABLE across turns or the prompt cache
            # invalidates every request:
            #   - `load_session_context()` returns date (day granularity) +
            #     AGENTS.md + memory.md. Files re-read each turn so fresh
            #     `remember()` writes land immediately; cache invalidates
            #     only on actual file change, not on a clock tick.
            #   - If you add anything time-varying here (timestamps, run
            #     ids, RNG strings), move it to the per-turn user prompt
            #     instead, not the instructions block.
            context = load_session_context()
            return f"{base}\n\n---\n\n# Session context\n\n{context}"

        return _instructions


def make_context_capability(base_prompt: str) -> ContextCapability:
    """Build a fresh :class:`ContextCapability` with the given base prompt."""
    return ContextCapability(base_prompt=base_prompt)
