"""Per-dispatch context handed to every slash handler.

Handlers receive a :class:`SlashContext` so they can render to the right
console, read or mutate the session state, and (in PR3) know which profile
and tier are active for ``/model`` and ``/profile`` behavior.

The context is constructed fresh each turn by the REPL — handlers should
not stash references across dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from jac.capabilities.a2a import A2ACapability
from jac.capabilities.mcp import MCPCapability
from jac.capabilities.skills import SkillsCapability
from jac.profiles import Profile
from jac.runtime.session import Session
from jac.runtime.usage import UsageTracker


@dataclass
class SlashContext:
    """State a slash handler is allowed to touch."""

    console: Console
    """Rich console for any user-visible output."""

    session: Session
    """The active session at dispatch time. Handlers that switch sessions
    return :class:`~jac.cli.slash.result.SwitchSession` rather than mutating
    this in place — the REPL owns lifecycle changes."""

    profile_name: str | None
    """Active profile name. ``None`` when the REPL was started with
    ``--model PROVIDER:ID`` (no profile in play)."""

    profile: Profile | None
    """Loaded profile object — ``None`` mirrors ``profile_name``'s case.
    The ``/model`` no-arg picker reads ``profile.tiers`` to enumerate
    candidate models grouped by tier."""

    model_id: str
    """The model id currently bound to Gru — surfaces in ``/help`` output
    and in ``/model`` no-arg display (with an ``(active)`` marker)."""

    usage_tracker: UsageTracker | None = None
    """Live token-usage tracker (D25). ``/budget`` and ``/tokens`` read
    from it; ``/budget extend N`` mutates its in-memory limits. ``None``
    only in tests that don't exercise the budget surface."""

    a2a: A2ACapability | None = None
    """A2A subsystem capability (D24, Phase 4). ``/a2a serve|stop|status|token``
    read and mutate its state. ``None`` in tests that don't exercise the
    A2A surface and in non-REPL contexts."""

    skills: SkillsCapability | None = None
    """Skill loader capability (D21, Phase D). ``/skill list|use|reload``
    read from it; ``/skill reload`` mutates its in-memory catalog. ``None``
    in tests that don't exercise the skills surface."""

    mcp: MCPCapability | None = None
    """MCP server loader capability (Phase F, D28). ``/mcp list|reload|
    enable|disable`` read and mutate its catalog. ``None`` in tests that
    don't exercise the MCP surface."""
