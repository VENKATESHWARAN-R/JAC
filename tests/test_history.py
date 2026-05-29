"""Tests for token-aware history compaction (D20, 1.7.a)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from jac.capabilities import history as hist
from jac.capabilities.history import (
    _find_drop_index,
    estimate_text_tokens,
    estimate_tokens,
    make_history_capability,
)
from jac.config import reset_settings_cache
from jac.runtime.events import CompactionTriggered, CompactionWarning, EventBus
from jac.workspace import paths
from jac.workspace.session_ctx import set_current_session_id

# anyio's pytest plugin runs async test functions; mark the whole module.
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------- helpers ----------


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _exchange(user_text: str, assistant_text: str) -> list:
    return [_user(user_text), _assistant(assistant_text)]


def _drain(bus: EventBus) -> list:
    """Pull every queued event out of ``bus`` synchronously (test-only)."""
    out: list = []
    queue = bus._queue
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect project_sessions_dir + clear find_project_root cache.

    Also resets the cached Settings so per-test env overrides take effect.
    """
    sessions = tmp_path / ".agents" / "sessions"
    sessions.mkdir(parents=True)
    # Patch project_root so project_state_root (and thus the directly-imported
    # project_sessions_dir in session.py / history.py) resolves under tmp_path.
    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    set_current_session_id("test-session")
    reset_settings_cache()
    yield
    set_current_session_id(None)
    reset_settings_cache()


# ---------- estimator ----------


def test_estimate_tokens_3_chars_per_token() -> None:
    msg = _user("a" * 30)  # 30 chars / 3 = 10 tokens
    assert estimate_tokens([msg]) == 10


def test_estimate_text_tokens_consistent() -> None:
    assert estimate_text_tokens("a" * 30) == 10


def test_estimate_tokens_sums_across_messages() -> None:
    msgs = _exchange("a" * 30, "b" * 60)  # 10 + 20 = 30
    assert estimate_tokens(msgs) == 30


def test_estimate_tokens_handles_tool_call_parts() -> None:
    """Tool calls have args/tool_name attributes — should still count."""
    from pydantic_ai.messages import ToolCallPart

    msg = ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={"path": "x" * 30})])
    # Args dict gets str()-ified
    assert estimate_tokens([msg]) > 0


# ---------- drop boundary ----------


def test_find_drop_index_keeps_under_target() -> None:
    msgs: list = []
    for i in range(5):
        msgs.extend(_exchange("u" * 300, "a" * 300))  # ~200 tokens per exchange
    # Target 250 tokens — should drop everything but the last exchange.
    idx = _find_drop_index(msgs, 250)
    assert estimate_tokens(msgs[idx:]) <= 250


def test_find_drop_index_drops_on_user_boundary() -> None:
    """Resulting kept slice must start at a UserPromptPart so tool pairs stay paired."""
    msgs: list = []
    for i in range(4):
        msgs.extend(_exchange("u" * 300, "a" * 300))
    idx = _find_drop_index(msgs, 200)
    # The first kept message must be a ModelRequest with a UserPromptPart.
    first = msgs[idx]
    assert isinstance(first, ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in first.parts)


def test_find_drop_index_empty_history() -> None:
    assert _find_drop_index([], 100) == 0


# ---------- threshold ladder ----------


@pytest.fixture
def small_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the budget to something easily exceeded in tests."""
    monkeypatch.setenv("JAC_COMPACTION__MAX_CONTEXT_TOKENS", "1000")
    monkeypatch.setenv("JAC_COMPACTION__WARN_PCT", "60")
    monkeypatch.setenv("JAC_COMPACTION__AUTO_COMPACT_PCT", "70")
    monkeypatch.setenv("JAC_COMPACTION__REFUSE_PCT", "85")
    monkeypatch.setenv("JAC_COMPACTION__TARGET_PCT_AFTER_COMPACT", "40")
    reset_settings_cache()


async def test_processor_passes_through_below_warn(small_budget: None) -> None:
    cap = make_history_capability()
    msgs = [_user("a" * 30)]  # ~10 tokens, well under 60% of 1000
    result = await cap.processor(msgs)
    assert result == msgs


async def test_processor_emits_warning_between_warn_and_compact(
    small_budget: None,
) -> None:
    bus = EventBus()
    cap = make_history_capability(bus=bus)
    # Target: between 60% and 70% of 1000 — i.e. 600-700 tokens
    msgs = [_user("a" * 1900)]  # ~633 tokens
    result = await cap.processor(msgs)
    assert result == msgs  # not compacted
    # Drain bus to find the warning event.
    events = _drain(bus)
    assert any(isinstance(e, CompactionWarning) for e in events)


async def test_processor_compacts_when_over_threshold(
    small_budget: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At >=70% the oldest slice is compacted via the summarizer; events fire."""

    async def fake_summarize(msgs, summarizer_model):
        return "Earlier messages: user asked stuff, assistant responded."

    monkeypatch.setattr(hist, "_summarize", fake_summarize)

    bus = EventBus()
    cap = make_history_capability(bus=bus, summarizer_model="fake:model")

    msgs: list = []
    for i in range(6):
        msgs.extend(_exchange("u" * 240, "a" * 240))  # ~160 tokens each, 960 total
    # Add another exchange to push over 70%
    msgs.extend(_exchange("u" * 240, "a" * 240))  # now ~1120 tokens > 700 (70%)

    result = await cap.processor(msgs)
    assert len(result) < len(msgs)
    # Summary message should be first.
    assert isinstance(result[0], ModelRequest)
    first_part = result[0].parts[0]
    assert isinstance(first_part, UserPromptPart)
    assert (
        "<<conversation_summary>>" in first_part.content
        or "conversation_summary" in first_part.content
    )

    events = _drain(bus)
    triggered = [e for e in events if isinstance(e, CompactionTriggered)]
    assert len(triggered) == 1
    assert triggered[0].dropped_count > 0


async def test_processor_drop_only_when_summarizer_fails(
    small_budget: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the summarizer returns None / raises, we still shrink the history."""

    async def fake_summarize(msgs, summarizer_model):
        return None

    monkeypatch.setattr(hist, "_summarize", fake_summarize)

    cap = make_history_capability(summarizer_model="fake:model")
    msgs: list = []
    for i in range(7):
        msgs.extend(_exchange("u" * 240, "a" * 240))
    original_len = len(msgs)

    result = await cap.processor(msgs)
    assert len(result) < original_len
    # No synthetic summary message — first message is the original user turn we kept.
    first = result[0]
    assert isinstance(first, ModelRequest)
    first_content = first.parts[0].content if first.parts else ""
    assert "<<conversation_summary>>" not in first_content


async def test_processor_no_summarizer_means_drop_only(small_budget: None) -> None:
    """summarizer_model=None must not crash; drop-only path is exercised."""
    cap = make_history_capability(summarizer_model=None)
    msgs: list = []
    for i in range(7):
        msgs.extend(_exchange("u" * 240, "a" * 240))
    result = await cap.processor(msgs)
    assert len(result) < len(msgs)


async def test_processor_persists_dropped_slice_to_disk(
    small_budget: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_summarize(msgs, summarizer_model):
        return "summary"

    monkeypatch.setattr(hist, "_summarize", fake_summarize)
    cap = make_history_capability(summarizer_model="fake:model")

    msgs: list = []
    for i in range(7):
        msgs.extend(_exchange("u" * 240, "a" * 240))

    await cap.processor(msgs)
    compacted_dir = paths.project_sessions_dir() / "test-session" / "compacted"
    assert compacted_dir.is_dir()
    snapshots = list(compacted_dir.glob("*.json"))
    assert len(snapshots) == 1
    assert snapshots[0].name == "1.json"
    assert snapshots[0].stat().st_size > 0


# ---------- Settings env overrides ----------


def test_settings_env_override_works(monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.config import get_settings

    monkeypatch.setenv("JAC_COMPACTION__MAX_CONTEXT_TOKENS", "500000")
    reset_settings_cache()
    settings = get_settings()
    assert settings.compaction.max_context_tokens == 500000


def test_default_budget_is_256k() -> None:
    from jac.config import get_settings

    assert get_settings().compaction.max_context_tokens == 256_000


def test_ceiling_rejects_oversized_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.config import get_settings

    monkeypatch.setenv("JAC_COMPACTION__MAX_CONTEXT_TOKENS", "600000")  # > 512k
    reset_settings_cache()
    with pytest.raises(Exception):  # pydantic ValidationError surfaced at load
        get_settings()


# ---------- budget resolution ----------


def test_resolve_context_budget_session_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.config import resolve_context_budget, set_session_context_budget

    monkeypatch.setenv("JAC_COMPACTION__MAX_CONTEXT_TOKENS", "256000")
    reset_settings_cache()
    assert resolve_context_budget() == 256_000
    set_session_context_budget(400_000)
    try:
        assert resolve_context_budget() == 400_000
    finally:
        set_session_context_budget(None)
    assert resolve_context_budget() == 256_000


def test_resolve_context_budget_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.config import resolve_context_budget

    monkeypatch.setenv("JAC_MODEL", "anthropic:test-model")
    monkeypatch.setenv("JAC_COMPACTION__MODEL_CONTEXT_TOKENS", '{"anthropic:test-model": 384000}')
    reset_settings_cache()
    assert resolve_context_budget("anthropic:test-model") == 384_000
    # A model with no entry falls back to the default.
    assert resolve_context_budget("anthropic:other") == 256_000


def test_session_budget_override_clamped_to_ceiling() -> None:
    from jac.config import MAX_CONTEXT_CEILING, set_session_context_budget

    try:
        stored = set_session_context_budget(9_000_000)
        assert stored == MAX_CONTEXT_CEILING
    finally:
        set_session_context_budget(None)


# ---------- strategy branching ----------


@pytest.fixture
def sliding_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_COMPACTION__STRATEGY", "sliding")
    monkeypatch.setenv("JAC_COMPACTION__MAX_CONTEXT_TOKENS", "1000")
    monkeypatch.setenv("JAC_COMPACTION__WARN_PCT", "60")
    monkeypatch.setenv("JAC_COMPACTION__AUTO_COMPACT_PCT", "70")
    monkeypatch.setenv("JAC_COMPACTION__TARGET_PCT_AFTER_COMPACT", "40")
    reset_settings_cache()


@pytest.fixture
def manual_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_COMPACTION__STRATEGY", "manual")
    monkeypatch.setenv("JAC_COMPACTION__MAX_CONTEXT_TOKENS", "1000")
    monkeypatch.setenv("JAC_COMPACTION__WARN_PCT", "60")
    monkeypatch.setenv("JAC_COMPACTION__AUTO_COMPACT_PCT", "70")
    reset_settings_cache()


async def test_sliding_drops_without_summary_and_emits_overflow(
    sliding_budget: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jac.runtime.events import ContextOverflow

    async def boom(msgs, summarizer_model):  # must NOT be called in sliding mode
        raise AssertionError("sliding strategy must not summarize")

    monkeypatch.setattr(hist, "_summarize", boom)

    bus = EventBus()
    # summarizer_model set but should be ignored by the sliding path.
    cap = make_history_capability(bus=bus, summarizer_model="fake:model")
    msgs: list = []
    for _ in range(7):
        msgs.extend(_exchange("u" * 240, "a" * 240))  # ~1120 tokens > 70% of 1000

    result = await cap.processor(msgs)
    assert len(result) < len(msgs)
    # No synthetic summary inserted — pure drop.
    first_content = result[0].parts[0].content if result[0].parts else ""
    assert "<<conversation_summary>>" not in first_content
    events = _drain(bus)
    overflow = [e for e in events if isinstance(e, ContextOverflow)]
    assert len(overflow) == 1
    assert overflow[0].dropped_count > 0


async def test_manual_never_compacts_but_warns(manual_budget: None) -> None:
    bus = EventBus()
    cap = make_history_capability(bus=bus, summarizer_model="fake:model")
    msgs: list = []
    for _ in range(7):
        msgs.extend(_exchange("u" * 240, "a" * 240))  # well over auto threshold

    result = await cap.processor(msgs)
    assert result == msgs  # untouched in manual mode
    events = _drain(bus)
    assert any(isinstance(e, CompactionWarning) for e in events)


async def test_force_compact_summarizes_regardless_of_fill(
    small_budget: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jac.capabilities.history import force_compact

    async def fake_summarize(msgs, summarizer_model):
        return "forced summary"

    monkeypatch.setattr(hist, "_summarize", fake_summarize)

    # ~600 tokens: 60% of the 1000 budget — below the 70% auto threshold, so
    # the auto processor would NOT fire, but /compact should still shrink it.
    msgs: list = []
    for _ in range(6):
        msgs.extend(_exchange("u" * 150, "a" * 150))  # ~100 tokens each

    new_history, dropped, summary_tokens = await force_compact(msgs, "fake:model")
    assert dropped > 0
    assert summary_tokens > 0
    assert len(new_history) < len(msgs)
    assert "forced summary" in new_history[0].parts[0].content
