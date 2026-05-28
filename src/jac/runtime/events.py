"""JAC runtime event types and the event bus that transports them.

Events flow:

    Pydantic AI hooks (jac.runtime.hooks)
        → EventBus (this module)
            → CLI renderer / other surfaces (jac.cli.renderer)

This is the architectural inversion: the CLI does not poll the agent. It
consumes events the runtime emits. Adding a new surface (TUI, web) means
adding a new consumer of :class:`EventBus` — nothing in the runtime changes.

Per-turn boundaries: every turn ends with a terminal event,
:class:`RunCompleted` or :class:`RunFailed`. Consumers should treat the
arrival of either as "stop reading until the next turn starts."

:class:`ApprovalRequest` is special: it carries a Future the consumer is
expected to resolve. The runtime awaits that Future before continuing the
agent loop — see :mod:`jac.runtime.approval`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class JacEvent:
    """Base class. Don't instantiate directly; use one of the subclasses."""


PlanStepStatus = Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True, slots=True)
class PlanStepView:
    """Snapshot of one plan step. Emitted as part of plan events.

    ``index`` is 1-based to match what the tool surface exposes to Gru and
    what the user sees in the rendered checklist.
    """

    index: int
    text: str
    status: PlanStepStatus


@dataclass(frozen=True, slots=True)
class ModelRequestStarted(JacEvent):
    """Gru is sending a request to the model."""


@dataclass(frozen=True, slots=True)
class ModelRequestCompleted(JacEvent):
    """The model has responded."""


@dataclass(frozen=True, slots=True)
class ToolCallStarted(JacEvent):
    """A tool call is about to execute (validated args, pre-run)."""

    tool_name: str
    reason: str | None
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallCompleted(JacEvent):
    """A tool call returned successfully."""

    tool_name: str
    result_preview: str


@dataclass(frozen=True, slots=True)
class ToolCallFailed(JacEvent):
    """A tool call raised. The agent loop may still recover."""

    tool_name: str
    error: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest(JacEvent):
    """A deferred tool call needs human approval.

    The consumer (renderer) is expected to resolve ``response_future`` with
    an :class:`ApprovalResponse`. The approval handler awaits the future
    before deciding what to send back to the agent loop.

    ``agent_label`` identifies which agent raised the call — ``"Gru"``
    for the main loop, ``"minion-N"`` for a spawned sub-agent. Surfaced
    in the approval panel title so the user can tell *who* is asking
    (one sub-agent? several running in parallel?). Defaults to ``"Gru"``
    to
    keep behaviour unchanged for surfaces that haven't started reading
    the field yet.
    """

    tool_call_id: str
    tool_name: str
    reason: str | None
    args: dict[str, Any]
    response_future: asyncio.Future[ApprovalResponse]
    agent_label: str = "Gru"


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    """Result of an :class:`ApprovalRequest`, supplied by the consumer.

    Three shapes (D26):

    - ``approved=True`` — the call proceeds.
    - ``approved=False, feedback=None`` — plain denial. The model is told the
      user declined and should pick a different approach.
    - ``approved=False, feedback="..."`` — *denied with feedback*. The user's
      redirection ("edit the test file instead") is embedded in the tool
      result, so the model sees a structured signal and doesn't burn a turn
      re-deciding what to do.
    """

    approved: bool
    deny_message: str | None = None
    """Optional message sent back to the model when ``approved`` is False
    and no ``feedback`` is set. ``feedback`` takes precedence when present."""
    feedback: str | None = None
    """In-band redirection the user typed on the deny prompt. When set, the
    approval handler builds a structured tool-result message exposing it to
    the model as ``user_feedback``."""


@dataclass(frozen=True, slots=True)
class ClarifyRequest(JacEvent):
    """The agent needs the user to pick between explicit options.

    Parallels :class:`ApprovalRequest`: the consumer (renderer) prompts
    the user and resolves ``response_future`` with a
    :class:`ClarifyResponse`. The agent loop awaits that future before
    continuing. Unlike approval, the *act* of asking is the side effect —
    no separate HITL gate is layered on top.
    """

    question: str
    options: tuple[str, ...]
    response_future: asyncio.Future[ClarifyResponse]


@dataclass(frozen=True, slots=True)
class ClarifyResponse:
    """Result of a :class:`ClarifyRequest`, supplied by the consumer.

    ``selected_index`` is 1-based to match how the user sees options in
    the prompt; ``selected_text`` is the literal option string. When the
    user cancels (Ctrl-C / empty input), ``cancelled`` is ``True`` and
    both index/text are ``None``.

    When the user picks the renderer's "Type your own answer" affordance
    (D26), ``free_text`` is ``True``, ``selected_index`` is ``None``, and
    ``selected_text`` carries the user's free-form answer verbatim.
    """

    selected_index: int | None
    selected_text: str | None
    cancelled: bool = False
    free_text: bool = False


@dataclass(frozen=True, slots=True)
class ProcessStarted(JacEvent):
    """A background process started running.

    The renderer prints these as muted single-line notifications. Process
    output is NOT streamed onto the bus (it would flood the renderer); Gru
    pulls log lines via ``tail_process`` instead.
    """

    task_id: str
    command: str
    name: str | None


@dataclass(frozen=True, slots=True)
class ProcessExited(JacEvent):
    """A background process exited.

    Carries the exit code so the renderer can color the notification
    (green ==0, yellow nonzero, red for negative — signals).
    """

    task_id: str
    exit_code: int


@dataclass(frozen=True, slots=True)
class PlanReplaced(JacEvent):
    """Gru replaced its current plan with a fresh step list.

    The renderer redraws the checklist panel from scratch on this event.
    """

    steps: tuple[PlanStepView, ...]


@dataclass(frozen=True, slots=True)
class PlanStepUpdated(JacEvent):
    """Gru flipped one step's status. ``index`` is 1-based."""

    index: int
    status: PlanStepStatus
    text: str


@dataclass(frozen=True, slots=True)
class CompactionWarning(JacEvent):
    """History size has crossed the warn threshold but not yet the auto-compact one.

    Renderer flips the status bar / prints an inline yellow notice; no
    structural change to history yet. ``usage_pct`` is the integer percent
    of the configured ``max_context_tokens`` budget the current history
    estimate occupies.
    """

    usage_pct: int


@dataclass(frozen=True, slots=True)
class CompactionTriggered(JacEvent):
    """Auto-compaction just ran: an old slice was summarized + replaced.

    ``dropped_count`` is the number of original messages condensed into a
    single synthetic summary. ``summary_tokens`` is the estimated token
    cost of the synthetic replacement. ``usage_pct`` is the post-compaction
    estimate so the renderer can show the new fill level.
    """

    dropped_count: int
    summary_tokens: int
    usage_pct: int


@dataclass(frozen=True, slots=True)
class CompactionRefused(JacEvent):
    """The pre-flight check refused the next user turn — context is too full.

    Emitted before ``gru.run()`` is invoked, so the model is never called.
    The user is told to ``/clear`` or otherwise free space.
    """

    usage_pct: int


BudgetKind = Literal["session_input", "session_total", "project_total"]


@dataclass(frozen=True, slots=True)
class BudgetWarning(JacEvent):
    """A token budget crossed its warn threshold (D25).

    Emitted once per ``(kind, threshold)`` pair per session — the tracker
    dedups so the user isn't spammed every turn after the threshold is
    crossed. ``kind`` identifies which budget tripped; the status bar /
    renderer use it to label the notice.
    """

    kind: BudgetKind
    used: int
    budget: int
    pct: int


@dataclass(frozen=True, slots=True)
class BudgetHardStop(JacEvent):
    """A token budget crossed its hard-stop threshold (D25).

    Emitted before ``gru.run()`` is invoked when ``used >= budget`` for
    any configured budget. The REPL refuses the turn — no model call.
    Mid-session ``/budget extend N`` is the documented way out.
    """

    kind: BudgetKind
    used: int
    budget: int


@dataclass(frozen=True, slots=True)
class A2AServerStarted(JacEvent):
    """The A2A guest server bound to a port and started accepting calls (D24).

    Emitted by :mod:`jac.capabilities.a2a.server` after the background
    uvicorn task is up. ``url`` is the full base URL peers should use
    (``http://host:port``). ``token_redacted`` is the bearer token with
    the middle truncated for safe display in the renderer; the full token
    is printed once to the user's console on startup (or accessible via
    ``/a2a token``). ``unsafe`` is ``True`` when auth is disabled (the
    renderer paints the line red so it's hard to miss).
    """

    url: str
    token_redacted: str
    unsafe: bool
    bind_host: str


@dataclass(frozen=True, slots=True)
class A2AServerStopped(JacEvent):
    """The A2A guest server shut down (D24).

    ``reason`` is a short tag — ``"user"`` (``/a2a stop``), ``"repl-exit"``
    (REPL teardown reaping), or ``"error: ..."`` for crashes the runtime
    caught before propagating.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class A2AInboundCall(JacEvent):
    """A peer just sent a ``message/send`` to our guest server (D24).

    Emitted *before* the guest Gru runs so the host operator sees what
    peers are doing in real time. ``peer_id`` is the caller identity we
    derived (the bearer token's last 8 chars, or ``"unsafe"`` when auth
    is off — we don't have richer identity until OAuth2 lands in v2).
    ``message_preview`` is the first ~120 chars of the inbound text part.
    """

    peer_id: str
    context_id: str
    task_id: str
    message_preview: str


@dataclass(frozen=True, slots=True)
class A2AInboundCompleted(JacEvent):
    """The guest Gru finished handling an inbound call (D24).

    ``state`` follows the A2A task state (``completed`` / ``failed`` /
    ``canceled``). ``duration_ms`` is wall time. ``tokens_used`` is the
    sum of input + output tokens the guest call consumed (drives PR3's
    budget integration; renderer just shows it as informational).
    """

    peer_id: str
    context_id: str
    task_id: str
    state: str
    duration_ms: int
    tokens_used: int


@dataclass(frozen=True, slots=True)
class A2AOutboundCall(JacEvent):
    """Gru is about to send an A2A call to a peer (D24, Phase 4.b).

    Emitted by ``a2a_call`` / ``a2a_discover`` before the httpx request
    fires. ``target`` is the peer name (when called by name) or the raw
    URL (when called ad-hoc) — whichever the user/agent supplied — so
    the renderer surfaces the same identifier the call site used.
    """

    target: str
    message_preview: str


@dataclass(frozen=True, slots=True)
class A2AOutboundTokenMinted(JacEvent):
    """OAuth2 client_credentials just minted a fresh access token (Phase 4.d).

    Emitted by :class:`OAuth2ClientCredentialsStrategy` right after a
    successful refresh — gives the operator visibility into IDP
    roundtrips. ``token_url`` is the IDP endpoint we hit; ``peer_name``
    is the configured peer that triggered the mint (may be ``None``
    when the strategy is invoked via raw URL); ``expires_in_s`` is
    the published lifetime (or 0 when the IDP returned none).
    """

    token_url: str
    peer_name: str | None
    expires_in_s: int


@dataclass(frozen=True, slots=True)
class A2AOutboundCompleted(JacEvent):
    """An outbound A2A call finished (D24, Phase 4.b).

    ``state`` is binary for outbound: ``"completed"`` (got a response,
    even if that response was a JSON-RPC error) or ``"failed"``
    (network / auth / protocol error before we got a body). ``duration_ms``
    is wall time. Distinct from inbound's A2A-task-lifecycle ``state``
    because outbound completion is from the *client* perspective.
    """

    target: str
    state: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class SubAgentSpawned(JacEvent):
    """A sub-agent worker started. Bidirectional path only — sequential
    spawns rely on the existing ``ToolCallStarted`` line for visibility."""

    spawn_id: str
    tier: str
    model: str
    objective: str


@dataclass(frozen=True, slots=True)
class SubAgentQuestion(JacEvent):
    """A parked sub-agent asked the main agent a question (D41).

    Emitted at the moment the question lands on the channel's queue —
    before the main agent has read the tool result, so the user sees the
    question text in the scroll-back even if Gru's text reply is terse."""

    spawn_id: str
    question: str
    round_trip: int


@dataclass(frozen=True, slots=True)
class SubAgentAnswer(JacEvent):
    """The main agent replied to a sub-agent's question. Emitted from
    ``respond_to_sub_agent`` *before* the answer is delivered to the
    sub-agent so the scroll-back order is question → answer → next event."""

    spawn_id: str
    answer: str


@dataclass(frozen=True, slots=True)
class SubAgentCompleted(JacEvent):
    """A bidirectional sub-agent worker reached its end (success, error,
    or cap-then-finalize). Emitted after the final tagged result is
    rendered so the user gets a clean per-spawn epilogue."""

    spawn_id: str
    exit_status: str
    turns_used: int
    ask_main_agent_count: int


@dataclass(frozen=True, slots=True)
class RunCompleted(JacEvent):
    """Terminal: ``agent.run()`` completed normally. Carries the final output."""

    output: str


@dataclass(frozen=True, slots=True)
class RunFailed(JacEvent):
    """Terminal: ``agent.run()`` raised. The error is rendered to the user."""

    error: str


type JacEventT = (
    ModelRequestStarted
    | ModelRequestCompleted
    | ToolCallStarted
    | ToolCallCompleted
    | ToolCallFailed
    | ApprovalRequest
    | ClarifyRequest
    | PlanReplaced
    | PlanStepUpdated
    | ProcessStarted
    | ProcessExited
    | CompactionWarning
    | CompactionTriggered
    | CompactionRefused
    | BudgetWarning
    | BudgetHardStop
    | A2AServerStarted
    | A2AServerStopped
    | A2AInboundCall
    | A2AInboundCompleted
    | A2AOutboundCall
    | A2AOutboundCompleted
    | A2AOutboundTokenMinted
    | SubAgentSpawned
    | SubAgentQuestion
    | SubAgentAnswer
    | SubAgentCompleted
    | RunCompleted
    | RunFailed
)
"""Discriminated union of all event types. Use ``isinstance`` to dispatch."""


def is_terminal(event: JacEvent) -> bool:
    """True if ``event`` ends the current turn (``RunCompleted`` / ``RunFailed``)."""
    return isinstance(event, (RunCompleted, RunFailed))


class EventBus:
    """Single-producer / single-consumer event channel.

    Thin wrapper around :class:`asyncio.Queue`. The bus is **session-long**:
    one bus per REPL session, reused across every turn. A terminal event
    (:class:`RunCompleted` / :class:`RunFailed`) signals end-of-turn;
    consumers stop iterating then and wait for the next turn to push fresh
    events. Phase 1 assumption: only one renderer at a time. Multi-renderer
    fan-out is straightforward to add later without changing the public API.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[JacEventT] = asyncio.Queue()

    async def emit(self, event: JacEventT) -> None:
        """Put ``event`` onto the queue. Never blocks meaningfully."""
        await self._queue.put(event)

    async def stream(self) -> AsyncIterator[JacEventT]:
        """Yield events as they arrive. Consumer decides when to stop."""
        while True:
            event = await self._queue.get()
            yield event
