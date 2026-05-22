"""Outcome types a slash handler can return to the REPL.

The REPL inspects the returned :class:`SlashResult` after every dispatch and
acts on it: continue to the next prompt, exit, swap to a different session,
or (PR3) rebuild Gru against a new model/profile. Side effects live in the
REPL, not the handlers — handlers are otherwise pure-ish.
"""

from __future__ import annotations

from dataclasses import dataclass

from jac.runtime.session import Session


@dataclass(frozen=True)
class Handled:
    """Default: command did its work, REPL continues to the next prompt."""


@dataclass(frozen=True)
class Exit:
    """REPL should exit gracefully (same as typing ``exit``)."""


@dataclass(frozen=True)
class SwitchSession:
    """REPL should replace its current session with ``session``.

    Used by ``/clear`` (fresh session) and ``/resume`` (existing session).
    The REPL is responsible for updating ``set_current_session_id`` and
    re-priming ``message_history`` from ``session.message_history``.
    """

    session: Session


SlashResult = Handled | Exit | SwitchSession
