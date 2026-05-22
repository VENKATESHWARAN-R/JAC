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


@dataclass(frozen=True)
class RebuildGru:
    """REPL should rebuild the active Gru against a new model / profile.

    Returned by ``/model`` and ``/profile``. The REPL is responsible for the
    snapshot-try-rollback dance so a failed env apply (missing credentials,
    malformed profile) doesn't leave the process in a half-switched state —
    on failure Gru stays on the previous model and a warning is rendered.

    Attributes:
        new_model_id: target model identifier (e.g. ``anthropic:claude-opus-4-7``).
        new_profile_name: profile this swap is happening under. ``None`` for
            an ad-hoc ``/model PROVIDER:ID`` override that bypasses the
            configured profile entirely.
    """

    new_model_id: str
    new_profile_name: str | None


SlashResult = Handled | Exit | SwitchSession | RebuildGru
