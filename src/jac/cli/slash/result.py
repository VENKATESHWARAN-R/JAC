"""Outcome types a slash handler can return to the REPL.

The REPL inspects the returned :class:`SlashResult` after every dispatch and
acts on it: continue to the next prompt, exit, swap to a different session,
inject synthesized text as a turn, or force a compaction.

Runtime *mutations* (switch model/profile, toggle/reload MCP, reload skills,
switch mode) are **not** modeled as result types — handlers call the surface-
agnostic control plane (``ctx.controller``) directly, which mutates the live
runtime in place; the REPL re-syncs its display from the runtime after every
dispatch. Side effects that the REPL must drive in its own event loop or that
touch loop-local state (session, message history) stay as result types here.
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
class InjectUserText:
    """REPL should run an agent turn as if the user had typed ``text``.

    Returned by slash commands that want to push synthesized content into
    the conversation — today only ``/skill use NAME``, which injects the
    body of a loaded skill so the model can act on its guidance without
    the user re-pasting it.

    The injected text is **not** echoed as a prompt line; the REPL
    proceeds straight to a turn. Budget and compaction pre-flight checks
    still apply, so a skill body that pushes context over the refuse
    threshold gets blocked the same way a user paste would.

    Attributes:
        text: Verbatim string to feed to the agent as the next user message.
    """

    text: str


@dataclass(frozen=True)
class CompactNow:
    """REPL should force a summarizing compaction of the live history now.

    Returned by ``/compact``. The handler can't do the work itself — the
    message history lives in the REPL loop and summarization is async — so it
    defers to the REPL, which runs :func:`jac.capabilities.history.force_compact`
    against the active history, swaps in the result, persists it, and reports
    how many messages were folded into the summary. A no-op (nothing old
    enough to drop) is reported as such; the history is left untouched.
    """


SlashResult = Handled | Exit | SwitchSession | InjectUserText | CompactNow
