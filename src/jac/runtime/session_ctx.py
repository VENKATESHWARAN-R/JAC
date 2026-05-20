"""Current-session context — thin ContextVar holder for the active session id.

The ``remember`` / ``forget`` memory tools stamp the active session id into
each entry's audit comment so the user can later answer "where did this
fact come from?" without our needing to thread a session object through
every tool call.

A ``ContextVar`` (rather than a module-level global) keeps this safe under
concurrent runs — each asyncio task inherits its parent's value, and
setting from a child task doesn't leak back. For JAC today there is only
one active session at a time, but the contextvar makes that assumption
unnecessary.

The session id is **optional**: tools work fine if it has never been set,
just without the ``session:`` field in the audit comment. That keeps unit
tests and headless scripts simple — they don't have to fake a session.
"""

from __future__ import annotations

from contextvars import ContextVar

_current_session_id: ContextVar[str | None] = ContextVar(
    "jac_current_session_id", default=None
)


def set_current_session_id(session_id: str | None) -> None:
    """Set (or clear) the active session id for this context.

    Called by the REPL once per session immediately after the ``Session``
    is constructed or resumed. Pass ``None`` to clear (e.g. for tests).
    """
    _current_session_id.set(session_id)


def get_current_session_id() -> str | None:
    """Return the active session id, or ``None`` if unset."""
    return _current_session_id.get()
