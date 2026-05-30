"""Surface-agnostic session turn driver (R5 / R17).

The turn-driving pipeline used to live as private functions inside
``cli/repl.py``, fused to ``rich``: ``_run_turn`` constructed a
``CliRenderer`` *inside itself*, so a browser UI or SDK author had no
``Session.run_turn()`` to call. :class:`SessionDriver` extracts that pipeline
into the runtime so every surface (CLI today, browser/SDK later) reuses it.

The driver owns ``(gru, bus, usage_tracker)`` and exposes:

- :meth:`SessionDriver.run_turn` — run one agent turn, emitting lifecycle
  events onto the bus and returning the updated history. It does **not**
  construct or consume a renderer; that's the caller's job (the CLI creates a
  ``CliRenderer`` and consumes the bus concurrently). On a hard failure it
  recovers a resumable history instead of discarding the user's turn.
- :meth:`SessionDriver.check_context_budget` / :meth:`check_token_budget` —
  pre-flight guards. Each emits its refusal event (carrying plain-text
  ``suggested_action`` guidance, R5c) and returns it so a synchronous caller
  can react without subscribing to the bus; ``None`` means proceed.

Rendering is entirely the caller's concern — the driver only emits onto the
bus and returns data. This is what makes a non-CLI surface possible without
copying ``_repl_loop``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from jac.capabilities.history import estimate_text_tokens, estimate_tokens
from jac.config import get_settings, resolve_context_budget
from jac.runtime.events import (
    BudgetHardStop,
    CompactionRefused,
    EventBus,
    RunCompleted,
    RunFailed,
    TextDelta,
)
from jac.runtime.usage import UsageTracker

_CONTEXT_REFUSE_ACTION = (
    "Free up context with /compact (summarize now), /clear (start fresh), "
    "/context <N> (raise the budget), or switch compaction.strategy to sliding."
)
_TOKEN_HARDSTOP_ACTION = (
    "Raise it with /budget extend N for this session, or edit the budget: block in your config."
)


@dataclass(slots=True)
class TurnResult:
    """Outcome of one :meth:`SessionDriver.run_turn`.

    ``message_history`` is always resumable — on failure it's the recovered
    history (the user's turn + captured work, dangling tool calls closed), so
    the next turn still has context instead of a blank slate.
    """

    message_history: list[ModelMessage]
    failed: bool = False
    output: str = ""


@dataclass(slots=True)
class _RunOutcome:
    """Uniform view over a streamed run so :meth:`SessionDriver.run_turn` can
    read ``.output`` / ``.usage`` / ``.all_messages()`` the same way it reads a
    plain ``AgentRunResult`` (``StreamedRunResult`` lacks an ``.output`` attr)."""

    output: str
    usage: Any
    _messages: list[ModelMessage]

    def all_messages(self) -> list[ModelMessage]:
        return self._messages


@dataclass
class SessionDriver:
    """Drives agent turns for one session, surface-agnostically.

    Construct with the assembled ``gru`` Agent, the session ``bus``, and
    (optionally) a ``usage_tracker``. The CLI builds these in its bootstrap
    and hands them over; a future browser/SDK surface does the same. The
    driver never imports a renderer.
    """

    gru: Agent
    bus: EventBus
    usage_tracker: UsageTracker | None = None

    async def run_turn(
        self,
        text: str,
        message_history: list[ModelMessage],
        *,
        stream: bool = False,
    ) -> TurnResult:
        """Run one agent turn through the bus; return the updated history.

        Emits :class:`RunCompleted` (or :class:`RunFailed`) so a concurrently
        consuming renderer knows the turn ended. Records the turn's token
        counts into ``usage_tracker`` on success (D25). On a hard failure
        (a tool exhausting retries, an MCP server failing, a model error) the
        captured messages are recovered into a resumable history rather than
        discarded — that bug made Gru "forget" the conversation.

        ``stream=True`` drives the model via the streaming path, emitting
        :class:`TextDelta` chunks as tokens arrive (for a browser/chat
        surface); ``stream=False`` (the CLI default) runs to completion and
        emits only the terminal events. Either way the returned history and
        the recorded usage are identical.
        """
        captured: list[ModelMessage] = []
        with capture_run_messages() as msgs:
            try:
                if stream:
                    result = await self._run_streamed(text, message_history)
                else:
                    result = await self.gru.run(text, message_history=message_history)
            except Exception as exc:
                captured = list(msgs)
                await self.bus.emit(RunFailed(error=str(exc)))
                return TurnResult(
                    message_history=_recover_failed_history(message_history, captured, text),
                    failed=True,
                )

        output = str(result.output)
        await self.bus.emit(RunCompleted(output=output))
        if self.usage_tracker is not None:
            usage = result.usage
            await self.usage_tracker.record(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_tokens", 0),
                cache_write_tokens=getattr(usage, "cache_write_tokens", 0),
            )
        return TurnResult(message_history=result.all_messages(), output=output)

    async def _run_streamed(self, text: str, message_history: list[ModelMessage]) -> _RunOutcome:
        """Drive the model via ``run_stream``, emitting :class:`TextDelta` for
        each new chunk of output text. Returns a :class:`_RunOutcome` so the
        caller's usage + history bookkeeping is identical to the plain path
        (``StreamedRunResult`` exposes output via ``get_output()``, not a
        ``.output`` attribute)."""
        async with self.gru.run_stream(text, message_history=message_history) as stream_result:
            seen = 0
            # ``stream_text(delta=False)`` yields the cumulative text; we diff
            # against what we've already emitted so each TextDelta is the new
            # suffix only.
            async for text_so_far in stream_result.stream_text(delta=False):
                if len(text_so_far) > seen:
                    await self.bus.emit(TextDelta(content=text_so_far[seen:]))
                    seen = len(text_so_far)
            output = await stream_result.get_output()
            return _RunOutcome(
                output=str(output),
                usage=stream_result.usage,
                _messages=stream_result.all_messages(),
            )

    async def check_token_budget(self) -> BudgetHardStop | None:
        """Pre-flight: refuse the turn if any token budget is at hardstop (D25).

        Strict — only refuses when already past the line (the 80% warn already
        nudged). Emits :class:`BudgetHardStop` and returns it; ``None`` = ok.
        """
        if self.usage_tracker is None:
            return None
        tripped = self.usage_tracker.is_over_hardstop()
        if tripped is None:
            return None
        kind, used, budget = tripped
        event = BudgetHardStop(
            kind=kind, used=used, budget=budget, suggested_action=_TOKEN_HARDSTOP_ACTION
        )
        await self.bus.emit(event)
        return event

    async def check_context_budget(
        self, message_history: list[ModelMessage], user_text: str
    ) -> CompactionRefused | None:
        """Pre-flight: refuse the turn if context is already above the refuse
        pct. Estimates ``history + next prompt`` against the resolved budget.

        Returns the emitted :class:`CompactionRefused` (carrying guidance) on
        refusal, else ``None``. The ``sliding`` strategy never refuses — it
        drops oldest turns at send time and flags overflow in the status bar.
        """
        settings = get_settings().compaction
        if settings.strategy == "sliding":
            return None
        budget = resolve_context_budget()
        if budget <= 0:
            return None
        projected = estimate_tokens(message_history) + estimate_text_tokens(user_text)
        pct = int((projected / budget) * 100)
        if pct < settings.refuse_pct:
            return None
        event = CompactionRefused(usage_pct=pct, suggested_action=_CONTEXT_REFUSE_ACTION)
        await self.bus.emit(event)
        return event


def _close_open_tool_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Append synthetic returns for any tool call left unanswered by a crash.

    pydantic-ai refuses to resume a history that ends with an unprocessed
    tool call ("Cannot provide a new user prompt when the message history
    contains unprocessed tool calls"). A run that died mid-tool (retries
    exhausted, server disconnected) leaves exactly that. We pair every open
    ``ToolCallPart`` with a ``ToolReturnPart`` marking it aborted so the next
    turn can continue.
    """
    answered: set[str] = set()
    calls: dict[str, str] = {}
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = part.tool_name
            elif isinstance(part, ToolReturnPart | RetryPromptPart):
                tcid = getattr(part, "tool_call_id", None)
                if tcid:
                    answered.add(tcid)
    open_calls = [(cid, name) for cid, name in calls.items() if cid not in answered]
    if not open_calls:
        return list(messages)
    returns = [
        ToolReturnPart(
            tool_name=name,
            content="(tool call aborted: the turn failed before it returned)",
            tool_call_id=cid,
        )
        for cid, name in open_calls
    ]
    return [*messages, ModelRequest(parts=returns)]


def _recover_failed_history(
    original: list[ModelMessage], captured: list[ModelMessage], text: str
) -> list[ModelMessage]:
    """Build a resumable history after a turn crashed.

    Prefers the messages captured during the failed run (they include the
    user's prompt + whatever the model/tools produced), with dangling tool
    calls closed. If nothing was captured (the run died before recording the
    turn — e.g. an MCP server that failed to connect at run start), we
    synthesize the user turn plus a short failure note onto the prior history
    so the user's message and context survive.
    """
    if captured:
        return _close_open_tool_calls(captured)
    return [
        *original,
        ModelRequest(parts=[UserPromptPart(content=text)]),
        ModelResponse(
            parts=[TextPart(content="(the previous turn failed before it could complete)")]
        ),
    ]
