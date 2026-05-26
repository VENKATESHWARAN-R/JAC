"""Session-scoped sub-agent token counters.

Why a separate module: ``sub_agent.py`` records usage at the end of every
spawn, but the REPL is the only thing that knows about the active
``UsageTracker``. To avoid a circular import, the REPL registers a
recorder function here at session start; the sub-agent module calls it
through this indirection.

Mirrors the ``tool_summarize`` stats pattern — a tiny module-level
singleton plus a setter the REPL drives.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# Type of the recorder the REPL installs. Async because the underlying
# ``UsageTracker.add_sub_agent`` is async (it emits bus events on
# budget thresholds).
_Recorder = Callable[[int, int, str], Awaitable[None]]
"""``await record(input_tokens, output_tokens, tier)``."""


@dataclass
class SubAgentStats:
    """Per-session counters surfaced via ``/tokens``."""

    spawns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    """Total tokens per tier (input + output combined)."""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_stats = SubAgentStats()
_recorder: _Recorder | None = None


def get_sub_agent_stats() -> SubAgentStats:
    """Return the live per-session counters."""
    return _stats


def reset_sub_agent_stats() -> None:
    """Zero the counters in place (called on session start / switch)."""
    _stats.spawns = 0
    _stats.input_tokens = 0
    _stats.output_tokens = 0
    _stats.by_tier.clear()


def set_sub_agent_usage_recorder(recorder: _Recorder | None) -> None:
    """Install (or clear) the per-spawn recorder.

    The REPL passes a closure that forwards into the active
    ``UsageTracker.add_sub_agent`` — that's how spawn cost rolls into
    ``session_total`` and the project budget. ``None`` disables
    forwarding (tests / headless).
    """
    global _recorder
    _recorder = recorder


async def record_sub_agent_usage(*, input_tokens: int, output_tokens: int, tier: str) -> None:
    """Record one completed sub-agent spawn.

    Always updates the in-process counters (read by ``/tokens``).
    Additionally forwards to the REPL-installed recorder when set, so
    the spawn's tokens count toward the session/project budget
    guardrails.
    """
    _stats.spawns += 1
    _stats.input_tokens += max(0, input_tokens)
    _stats.output_tokens += max(0, output_tokens)
    _stats.by_tier[tier] = _stats.by_tier.get(tier, 0) + max(0, input_tokens + output_tokens)
    if _recorder is not None:
        await _recorder(input_tokens, output_tokens, tier)
