"""Token-aware message-history compaction (D20).

Today's processor measures size in *tokens*, not in user-prompt exchanges
like the original implementation. It runs against a **resolved budget**
(:func:`jac.config.resolve_context_budget` — session override → per-model
entry → ``compaction.max_context_tokens`` default, 256k) rather than the
model's published context window: recent models advertise 1M+ but quality
degrades past ~200-300k, so a conservative cap is a safer default the user
can raise (up to the 512k ceiling).

The ``compaction.strategy`` setting picks one of three behaviours once the
budget fills:

- **auto** (default) — at ``auto_compact_pct`` the oldest slice is
  summarized via the small-tier model and replaced with one synthetic
  message. Ladder: ``<warn_pct`` pass through · ``warn_pct..auto_compact_pct``
  warn only · ``auto_compact_pct..`` summarize · ``≥refuse_pct`` the *REPL*
  refuses the next turn (pre-flight in :mod:`jac.cli.repl`).
- **sliding** — never summarizes (no model call, no cost) and never refuses.
  At ``auto_compact_pct`` the oldest turns are *dropped* to fit
  ``target_pct_after_compact`` and a :class:`ContextOverflow` event + a
  persistent red status-bar marker flag the trim. Cheapest mode; forgets
  old context rather than compressing it.
- **manual** — never compacts on its own (warns from ``warn_pct`` upward so
  you know to act). ``/compact`` is the only compaction lever; the REPL
  still refuses at ``refuse_pct``.

In every mode dropped originals are preserved to
``<session>/compacted/<n>.json`` for replay/debugging, and ``/compact``
forces a summarizing compaction on demand regardless of strategy or fill.
This processor never raises; refuse is policy, not transport.

Token counts come from a char-based heuristic (3 chars/token, conservative
for code-heavy contexts). PAI's :class:`pydantic_ai.RunUsage` reports
exact whole-turn counts *after* a request, but compaction runs *before*
the next request and needs per-message attribution to decide where to
slice — which PAI doesn't expose. The heuristic errs on the side of
compacting slightly earlier than needed, which is the safe direction
(triggering at 70% of budget against a conservative-low estimate means
we never blow past a model's real context window). Don't replace this
without an exact per-message attribution primitive.

Compaction is best-effort. If the summarizer call fails (no profile, no
small tier, network error), we **drop-only** the slice — losing detail but
preserving forward progress. The dropped slice still goes to disk.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.direct import model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from jac.config import get_settings, resolve_context_budget
from jac.runtime.events import (
    CompactionTriggered,
    CompactionWarning,
    ContextOverflow,
    EventBus,
)
from jac.workspace.paths import project_sessions_dir
from jac.workspace.session_ctx import get_current_session_id

_CHARS_PER_TOKEN = 3
"""Conservative chars-per-token heuristic. English averages ~4; code packs
denser. Erring low means we compact slightly earlier, which is safer than
hitting the real model limit."""

_SUMMARY_PROMPT = (
    "You are summarizing an in-progress AI coding session for context-window "
    "management. Produce a concise summary capturing:\n"
    "- the user's overall goal(s)\n"
    "- key facts, decisions, and constraints established so far\n"
    "- files inspected or modified, with the relevant state\n"
    "- outstanding TODOs or unresolved questions\n"
    "Skip greetings, small-talk, and verbatim tool output. Use bullet points. "
    "This summary will replace the original conversation slice in the agent's "
    "history."
)


# ---------- token estimation ----------


def _part_chars(part: Any) -> int:
    """Best-effort char count for any pydantic-ai message part."""
    for attr in ("content", "args", "tool_name", "tool_call_id"):
        value = getattr(part, attr, None)
        if value is None:
            continue
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (list, tuple)):
            return sum(len(str(item)) for item in value)
        return len(str(value))
    return 0


def _message_chars(message: ModelMessage) -> int:
    parts = getattr(message, "parts", ()) or ()
    return sum(_part_chars(p) for p in parts)


def estimate_tokens(messages: Sequence[ModelMessage]) -> int:
    """Char-based heuristic at 3 chars/token. Conservative; underestimates rare."""
    return sum(_message_chars(m) for m in messages) // _CHARS_PER_TOKEN


def estimate_text_tokens(text: str) -> int:
    """Same heuristic for a raw string — used by the REPL's pre-flight refuse check."""
    return len(text) // _CHARS_PER_TOKEN


# ---------- exchange boundaries ----------


def _has_user_prompt(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(isinstance(part, UserPromptPart) for part in message.parts)


def _user_prompt_indices(messages: Sequence[ModelMessage]) -> list[int]:
    return [i for i, m in enumerate(messages) if _has_user_prompt(m)]


# ---------- compaction primitives ----------


def _build_summary_message(summary_text: str) -> ModelRequest:
    """Wrap the summary in a single synthetic ModelRequest.

    UserPromptPart is portable across providers (unlike CompactionPart,
    which is provider-tagged). The marker text makes the role explicit
    to the model and to a future human reading the JSON.
    """
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    content = (
        f"<<conversation_summary timestamp={ts}>>\n"
        "The earlier portion of this conversation was auto-compacted by JAC "
        "to free context budget. Here is the summary of what happened:\n\n"
        f"{summary_text}\n\n"
        "<<end_summary>>\n"
        "Continue from this point."
    )
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _find_drop_index(messages: Sequence[ModelMessage], target_tokens: int) -> int:
    """Return the index to keep from so the remaining history fits in ``target_tokens``.

    Drops on user-prompt boundaries so we never split a tool-call/return pair.
    If no boundary brings us under the target, we keep only the very last
    exchange (the active turn) — anything else risks losing the current ask.
    """
    user_indices = _user_prompt_indices(messages)
    if not user_indices:
        return 0
    # Try each candidate keep_from; stop at the oldest that fits.
    for idx in user_indices:
        if estimate_tokens(messages[idx:]) <= target_tokens:
            return idx
    return user_indices[-1]


def _persist_dropped_slice(messages: Sequence[ModelMessage]) -> int | None:
    """Write the dropped slice to ``<session>/compacted/<n>.json``.

    Returns ``n`` (1-indexed), or ``None`` if no session id is set (e.g. tests
    without :func:`set_current_session_id`).
    """
    session_id = get_current_session_id()
    if not session_id:
        return None
    compacted_dir = project_sessions_dir() / session_id / "compacted"
    compacted_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(int(p.stem) for p in compacted_dir.glob("*.json") if p.stem.isdigit())
    n = (existing[-1] + 1) if existing else 1
    target = compacted_dir / f"{n}.json"
    target.write_bytes(ModelMessagesTypeAdapter.dump_json(list(messages), indent=2))
    return n


def _extract_response_text(response: ModelResponse) -> str:
    chunks: list[str] = []
    for part in response.parts:
        if isinstance(part, TextPart) and part.content:
            chunks.append(part.content)
    return "\n".join(chunks).strip()


async def _summarize(messages: Sequence[ModelMessage], summarizer_model: str) -> str | None:
    """Call ``model_request`` with the small-tier model. Returns ``None`` on failure."""
    try:
        request = [
            *messages,
            ModelRequest(parts=[UserPromptPart(content=_SUMMARY_PROMPT)]),
        ]
        response = await model_request(summarizer_model, request)
    except Exception:
        return None
    text = _extract_response_text(response)
    return text or None


# ---------- shared compaction core ----------


async def _do_compaction(
    messages: Sequence[ModelMessage],
    target_tokens: int,
    summarizer_model: str | None,
    *,
    summarize: bool,
) -> tuple[list[ModelMessage], int, int]:
    """Drop (and optionally summarize) the oldest slice down to ``target_tokens``.

    Returns ``(new_history, dropped_count, summary_tokens)``. When nothing can
    be dropped the input is returned unchanged with counts of ``0``. The
    dropped slice is always archived to ``<session>/compacted/<n>.json``.

    ``summarize=True`` replaces the dropped slice with a small-tier summary
    message (auto strategy / ``/compact``); ``summarize=False`` is a pure drop
    (sliding strategy) — no model call, no cost.
    """
    drop_at = _find_drop_index(messages, target_tokens)
    if drop_at <= 0:
        return list(messages), 0, 0

    dropped = messages[:drop_at]
    kept = messages[drop_at:]
    _persist_dropped_slice(dropped)

    summary_text: str | None = None
    if summarize and summarizer_model is not None:
        summary_text = await _summarize(dropped, summarizer_model)

    if summary_text:
        new_history: list[ModelMessage] = [_build_summary_message(summary_text), *kept]
        summary_tokens = estimate_tokens([new_history[0]])
    else:
        new_history = list(kept)
        summary_tokens = 0
    return new_history, len(dropped), summary_tokens


async def force_compact(
    messages: Sequence[ModelMessage],
    summarizer_model: str | None,
) -> tuple[list[ModelMessage], int, int]:
    """Force a summarizing compaction now, regardless of strategy or fill level.

    Backs the ``/compact`` slash command. Compacts down to
    ``target_pct_after_compact`` of the resolved budget. Returns
    ``(new_history, dropped_count, summary_tokens)`` — ``dropped_count == 0``
    means there was nothing old enough to drop.
    """
    settings = get_settings().compaction
    budget = resolve_context_budget()
    target_tokens = int(budget * settings.target_pct_after_compact / 100) if budget > 0 else 0
    return await _do_compaction(messages, target_tokens, summarizer_model, summarize=True)


# ---------- the capability ----------


def make_history_capability(
    bus: EventBus | None = None,
    summarizer_model: str | None = None,
) -> ProcessHistory:
    """Build a ``ProcessHistory`` that token-aware-compacts the message list.

    Args:
        bus: optional event bus; warnings + compaction notifications are
            published here. When ``None`` (e.g. headless tests) events are
            silently dropped.
        summarizer_model: model id used for auto-compaction summarization,
            typically the active profile's ``small`` tier. When ``None`` or
            when the call fails, compaction falls back to drop-only — still
            shrinks the history, but without a summary placeholder.
    """

    async def process(messages: list[ModelMessage]) -> list[ModelMessage]:
        if not messages:
            return messages

        settings = get_settings().compaction
        budget = resolve_context_budget()
        if budget <= 0:
            return messages
        current = estimate_tokens(messages)
        pct = int((current / budget) * 100)

        if pct < settings.warn_pct:
            return messages

        # Manual: never modify history here — warn from warn_pct up so the
        # user knows to /compact, but /compact (or the refuse threshold) is
        # the only thing that ever changes the history.
        if settings.strategy == "manual":
            if bus is not None:
                await bus.emit(CompactionWarning(usage_pct=pct))
            return messages

        if pct < settings.auto_compact_pct:
            if bus is not None:
                await bus.emit(CompactionWarning(usage_pct=pct))
            return messages

        target_tokens = int(budget * settings.target_pct_after_compact / 100)

        if settings.strategy == "sliding":
            new_history, dropped_count, _ = await _do_compaction(
                messages, target_tokens, None, summarize=False
            )
            if dropped_count and bus is not None:
                await bus.emit(ContextOverflow(dropped_count=dropped_count, usage_pct=pct))
            return new_history

        # auto: summarize the oldest slice (drop-only fallback when no summarizer).
        new_history, dropped_count, summary_tokens = await _do_compaction(
            messages, target_tokens, summarizer_model, summarize=True
        )
        if dropped_count == 0:
            # Nothing old enough to drop — let the refuse threshold catch it
            # next turn if it keeps growing.
            return new_history
        if bus is not None:
            post_pct = int((estimate_tokens(new_history) / budget) * 100)
            await bus.emit(
                CompactionTriggered(
                    dropped_count=dropped_count,
                    summary_tokens=summary_tokens,
                    usage_pct=post_pct,
                )
            )
        return new_history

    return ProcessHistory(process)
