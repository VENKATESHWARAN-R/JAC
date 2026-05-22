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

from jac.profiles import Profile
from jac.runtime.session import Session


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
