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


@dataclass(frozen=True)
class RefreshToolsets:
    """REPL should rebuild Gru in place against the *current* model/profile.

    Returned by ``/mcp reload|enable|disable`` (Phase F). Unlike
    :class:`RebuildGru` there's no model or profile change — the active
    model stays bound; we only need the agent reconstructed so a capability
    whose toolset changed (the reused :class:`MCPCapability`, whose
    ``get_toolset`` reads its live catalog) is re-consulted. No env dance,
    no "switched model" message.

    Attributes:
        note: short status line the REPL prints after the rebuild
            (e.g. ``"reloaded MCP servers"``).
    """

    note: str = ""


@dataclass(frozen=True)
class StartA2AServer:
    """REPL should start the A2A guest server (D24, Phase 4.a).

    Returned by ``/a2a serve``. The slash handler validates args and
    parses flags; the REPL drives the async ``A2ACapability.start_server``
    call in its own event loop so the uvicorn task's lifetime matches
    the REPL's. Failures (port in use, bind error, missing model) are
    rendered by the REPL as a friendly message; the server stays down.
    """

    host: str
    port: int
    unsafe: bool


@dataclass(frozen=True)
class StopA2AServer:
    """REPL should stop the A2A guest server (D24, Phase 4.a).

    Returned by ``/a2a stop``. The REPL awaits ``A2ACapability.stop_server``
    in its own event loop and renders the outcome.
    """


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


SlashResult = (
    Handled
    | Exit
    | SwitchSession
    | RebuildGru
    | RefreshToolsets
    | StartA2AServer
    | StopA2AServer
    | InjectUserText
    | CompactNow
)
