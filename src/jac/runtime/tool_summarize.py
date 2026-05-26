"""Tool result post-processor (Phase A.1).

Large outputs from tools opted in via ``@jac_tool(summarizable=True)`` are
routed through the active profile's *small* tier model and replaced with
a summary before they enter the main agent's context. The original output
is always written to ``<project>/.agents/cache/tool-results/<session>/<id>.txt``
so the agent can re-read it via the existing ``read_file`` tool when it
needs the exact bytes.

Summarization is **skipped** (raw passthrough) when any of these hold:

- the tool didn't opt in *and* isn't force-listed in ``cost.summarize_tools``
- the tool is in ``cost.no_summarize_tools``
- the output is below ``cost.tool_result_threshold_tokens``
- no summarizer model has been configured for this session
  (see :func:`set_summarizer_model`)
- the small-tier model isn't strictly cheaper per output token than the
  current tier (pricing comes from :mod:`jac.providers.registry` — unknown
  pricing on either side is treated as "skip", never as "guess")

Why this skip-when-uncertain stance: the goal is to *reduce* cost, never
risk increasing it. Crude-but-safe beats clever-but-iffy.
"""

from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logfire
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelRequest, TextPart, UserPromptPart

from jac.config import CostSettings, get_settings
from jac.providers.registry import get_provider_registry
from jac.workspace import paths
from jac.workspace.session_ctx import get_current_session_id

_CHARS_PER_TOKEN = 4
"""Char heuristic for output sizing. Looser than compaction's ``3`` because
this is a should-we-summarize gate, not a context-window safety check —
overestimating triggers needless summarization, underestimating skips it.
``4`` matches English/JSON-heavy tool output reasonably well."""

_TAG_PREFIX = "[AI-summarized via "


# ---------- session-scoped summarizer telemetry ----------


@dataclass
class SummarizerStats:
    """Per-session counters surfaced via ``/tokens``."""

    calls: int = 0
    original_tokens: int = 0  # input that would have hit the main loop
    summary_tokens: int = 0  # output that actually hit the main loop
    summarizer_input_tokens: int = 0  # what we spent on the small-tier call
    summarizer_output_tokens: int = 0

    @property
    def saved_tokens(self) -> int:
        """Tokens removed from the main agent's context window."""
        return max(0, self.original_tokens - self.summary_tokens)


_stats = SummarizerStats()


def get_summarizer_stats() -> SummarizerStats:
    """Return the live counters for the current session."""
    return _stats


def reset_summarizer_stats() -> None:
    """Zero the counters in place (called on session start / switch).

    Mutates the existing instance rather than rebinding so callers that
    captured a reference (e.g. from ``get_summarizer_stats()``) keep
    seeing live updates. JAC runs one session at a time, so a module-level
    singleton is sufficient.
    """
    _stats.calls = 0
    _stats.original_tokens = 0
    _stats.summary_tokens = 0
    _stats.summarizer_input_tokens = 0
    _stats.summarizer_output_tokens = 0


# ---------- session-scoped summarizer model id ----------

_summarizer_model: ContextVar[str | None] = ContextVar("jac_summarizer_model", default=None)


def set_summarizer_model(model_id: str | None) -> None:
    """Set the small-tier model id used to summarize large tool outputs.

    Called once per session by the REPL after profile activation. ``None``
    disables summarization for the rest of the context (used by tests).
    """
    _summarizer_model.set(model_id)


def get_summarizer_model() -> str | None:
    """Return the configured summarizer model id, or ``None``."""
    return _summarizer_model.get()


# ---------- helpers ----------


def estimate_tokens(text: str) -> int:
    """Char-based heuristic — see :data:`_CHARS_PER_TOKEN`."""
    return len(text) // _CHARS_PER_TOKEN


def _coerce_to_text(value: Any) -> str:
    """Render an arbitrary tool return value as a string for sizing/summarization."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace")
    try:
        return json.dumps(value, default=str, indent=2)
    except (TypeError, ValueError):
        return str(value)


def should_summarize(tool_name: str, *, tool_summarizable: bool, settings: CostSettings) -> bool:
    """Apply the decorator default plus user overrides.

    - ``cost.no_summarize_tools`` wins over everything (force-off).
    - ``cost.summarize_tools`` wins over the decorator default (force-on).
    - Otherwise the decorator's ``summarizable`` flag decides.
    """
    if tool_name in settings.no_summarize_tools:
        return False
    if tool_name in settings.summarize_tools:
        return True
    return tool_summarizable


def is_strictly_cheaper(small_model: str, current_model: str) -> bool:
    """True when both models have pricing data and ``small`` is strictly
    cheaper per output token than ``current``.

    The gate uses *output* token price because the small model spends most
    of its budget producing the summary (the input is the raw output we'd
    have to send to the current model anyway). Equal pricing returns False
    — there's no upside to the extra round trip.

    Unknown pricing on either side returns False. We never guess.
    """
    if small_model == current_model:
        return False
    registry = get_provider_registry()
    small = registry.get_pricing(small_model)
    current = registry.get_pricing(current_model)
    if small is None or current is None:
        return False
    return small.output < current.output


# ---------- disk cache ----------


def _cache_dir() -> Path:
    session = get_current_session_id() or "headless"
    path = paths.find_project_root() / ".agents" / "cache" / "tool-results" / session
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_cache(call_id: str, content: str) -> Path:
    target = _cache_dir() / f"{call_id}.txt"
    target.write_text(content, encoding="utf-8")
    return target


# ---------- the summarizer ----------


async def _summarize_text(
    text: str, tool_name: str, model_id: str, template: str
) -> tuple[str, int, int] | None:
    """Run the small-tier model.

    Returns ``(summary, input_tokens, output_tokens)`` or ``None`` on any
    failure. The token counts come from the model response when available
    (Anthropic/OpenAI/etc populate ``usage``) — they're the basis for the
    ``/tokens`` summarizer line.
    """
    prompt = template.format(
        tool_name=tool_name,
        original_tokens=estimate_tokens(text),
        output=text,
    )
    try:
        response = await model_request(
            model_id, [ModelRequest(parts=[UserPromptPart(content=prompt)])]
        )
    except Exception:
        return None
    chunks = [p.content for p in response.parts if isinstance(p, TextPart) and p.content]
    summary = "\n".join(chunks).strip()
    if not summary:
        return None
    usage = getattr(response, "usage", None)
    in_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    out_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return summary, in_tokens, out_tokens


async def maybe_summarize_tool_result(
    tool_name: str,
    raw_output: Any,
    *,
    tool_summarizable: bool,
    current_model: str | None,
    tool_call_id: str | None = None,
) -> Any:
    """Return either ``raw_output`` untouched or a summarized string.

    Args:
        tool_name: The tool's name, used for opt-in/out lookups + telemetry.
        raw_output: Whatever the tool returned. Non-strings are JSON-serialized
            for sizing and summarization; below-threshold non-strings pass
            through with their original type intact.
        tool_summarizable: The decorator's ``summarizable`` flag.
        current_model: The active model id; ``None`` when unknown (tests).
        tool_call_id: The pydantic-ai tool call id, used to name the cache
            file. A random uuid is used when missing.

    Returns:
        ``raw_output`` (any type) when the gate skips, or a tagged summary
        string when summarization ran.
    """
    settings = get_settings().cost

    if not should_summarize(tool_name, tool_summarizable=tool_summarizable, settings=settings):
        return raw_output

    text = _coerce_to_text(raw_output)
    original_tokens = estimate_tokens(text)
    if original_tokens < settings.tool_result_threshold_tokens:
        return raw_output

    summarizer = get_summarizer_model()
    if summarizer is None:
        return raw_output

    if current_model is not None and not is_strictly_cheaper(summarizer, current_model):
        return raw_output

    call_id = tool_call_id or uuid.uuid4().hex[:12]
    cache_path = _write_cache(call_id, text)

    with logfire.span(
        "tool_summarize",
        tool_name=tool_name,
        original_tokens=original_tokens,
        summarizer_model=summarizer,
        current_model=current_model,
        cache_path=str(cache_path),
    ) as span:
        outcome = await _summarize_text(
            text, tool_name, summarizer, settings.summarize_prompt_template
        )
        if outcome is None:
            span.set_attribute("status", "summarize_failed")
            return raw_output
        summary, sum_in, sum_out = outcome
        summary_tokens = estimate_tokens(summary)
        saved = max(0, original_tokens - summary_tokens)
        span.set_attribute("summary_tokens", summary_tokens)
        span.set_attribute("saved_tokens", saved)
        span.set_attribute("summarizer_input_tokens", sum_in)
        span.set_attribute("summarizer_output_tokens", sum_out)

    stats = get_summarizer_stats()
    stats.calls += 1
    stats.original_tokens += original_tokens
    stats.summary_tokens += summary_tokens
    stats.summarizer_input_tokens += sum_in
    stats.summarizer_output_tokens += sum_out

    try:
        rel = cache_path.relative_to(paths.find_project_root())
        cache_ref = str(rel)
    except ValueError:
        cache_ref = str(cache_path)

    header = (
        f"{_TAG_PREFIX}{summarizer} — original {original_tokens} tokens, "
        f"full output at {cache_ref}]"
    )
    return f"{header}\n\n{summary}"
