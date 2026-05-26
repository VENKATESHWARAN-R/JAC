"""Tests for ``/budget`` and ``/tokens`` slash handlers (Phase 1.7.f)."""

from __future__ import annotations

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from jac.cli.slash import SlashContext, dispatch
from jac.profiles import Profile
from jac.runtime.events import EventBus
from jac.runtime.session import Session
from jac.runtime.usage import BudgetLimits, UsageTracker


def _limits(
    *,
    session_input: int | None = None,
    session_total: int | None = None,
    project_total: int | None = None,
) -> BudgetLimits:
    return BudgetLimits(
        session_input_tokens=session_input,
        session_total_tokens=session_total,
        project_total_tokens=project_total,
        warn_pct=80,
        hardstop_pct=100,
    )


@pytest.fixture
def ctx_with_tracker() -> tuple[SlashContext, UsageTracker, StringIO]:
    """A SlashContext wired with a real UsageTracker."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    tracker = UsageTracker(
        session_id="s1",
        bus=EventBus(),
        usage_file=None,
        limits=_limits(session_total=10_000),
    )
    profile = Profile(
        tiers={"medium": ["anthropic:claude-sonnet-4-5"]},
        active_tier="medium",
    )
    ctx = SlashContext(
        console=console,
        session=Session(session_id="s1", message_history=[]),
        profile_name="claude",
        profile=profile,
        model_id="anthropic:claude-sonnet-4-5",
        usage_tracker=tracker,
    )
    return ctx, tracker, buf


# ---------- /budget ----------


def test_budget_no_args_shows_table_when_configured(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    asyncio.run(tracker.record(input_tokens=4_000, output_tokens=4_000))  # 80%
    dispatch("/budget", ctx)
    out = buf.getvalue()
    assert "session_total" in out
    assert "10,000" in out
    assert "80%" in out


def test_budget_no_args_no_budget_configured_shows_hint() -> None:
    """When no budget is set, the handler points the user at the config block."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    tracker = UsageTracker(session_id="s1", bus=None, usage_file=None, limits=_limits())
    asyncio.run(tracker.record(input_tokens=500, output_tokens=200))
    ctx = SlashContext(
        console=console,
        session=Session(session_id="s1", message_history=[]),
        profile_name=None,
        profile=None,
        model_id="anthropic:claude-sonnet-4-5",
        usage_tracker=tracker,
    )
    dispatch("/budget", ctx)
    out = buf.getvalue()
    assert "no token budget configured" in out
    assert "budget:" in out
    # Falls back to showing raw counters so the user sees something.
    assert "input=500" in out
    assert "output=200" in out


def test_budget_extend_defaults_to_session_total(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    asyncio.run(tracker.record(input_tokens=5_000, output_tokens=5_000))  # 100%
    dispatch("/budget extend 5000", ctx)
    assert tracker.limits.session_total_tokens == 15_000
    assert "session_total" in buf.getvalue()
    assert "15,000" in buf.getvalue()


def test_budget_extend_accepts_explicit_kind(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, _buf = ctx_with_tracker
    dispatch("/budget extend session_input 50000", ctx)
    assert tracker.limits.session_input_tokens == 50_000


def test_budget_extend_accepts_commas_and_underscores(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, _buf = ctx_with_tracker
    dispatch("/budget extend 50,000", ctx)
    assert tracker.limits.session_total_tokens == 60_000
    dispatch("/budget extend 1_000", ctx)
    assert tracker.limits.session_total_tokens == 61_000


def test_budget_extend_rejects_zero(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    dispatch("/budget extend 0", ctx)
    # No state change; error rendered.
    assert tracker.limits.session_total_tokens == 10_000
    assert "must be positive" in buf.getvalue()


def test_budget_extend_rejects_unknown_kind(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    dispatch("/budget extend bogus 1000", ctx)
    assert tracker.limits.session_total_tokens == 10_000
    assert "unknown budget kind" in buf.getvalue()


def test_budget_extend_rejects_non_numeric_amount(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    dispatch("/budget extend abc", ctx)
    assert tracker.limits.session_total_tokens == 10_000
    assert "could not parse" in buf.getvalue()


def test_budget_unknown_verb(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, _tracker, buf = ctx_with_tracker
    dispatch("/budget reset", ctx)
    assert "unknown /budget verb" in buf.getvalue()


# ---------- /tokens ----------


def test_tokens_shows_session_and_project_counters(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    tracker.project_baseline = 1234
    asyncio.run(tracker.record(input_tokens=100, output_tokens=50))
    dispatch("/tokens", ctx)
    out = buf.getvalue()
    assert "input=100" in out
    assert "output=50" in out
    assert "total=150" in out
    # Project total includes baseline.
    assert "total=1,384" in out
    assert "baseline=1,234" in out


def test_tokens_ignores_arguments(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, _tracker, buf = ctx_with_tracker
    dispatch("/tokens extra args", ctx)
    assert "takes no arguments" in buf.getvalue()


def test_tokens_shows_cache_when_provider_reports(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    asyncio.run(
        tracker.record(
            input_tokens=200,
            output_tokens=50,
            cache_read_tokens=800,
            cache_write_tokens=400,
        )
    )
    dispatch("/tokens", ctx)
    out = buf.getvalue()
    assert "cache:" in out
    assert "read=800" in out
    assert "write=400" in out
    assert "hit_rate=80%" in out  # 800 / (200 + 800)


def test_tokens_hides_cache_when_provider_silent(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    ctx, tracker, buf = ctx_with_tracker
    asyncio.run(tracker.record(input_tokens=100, output_tokens=50))
    dispatch("/tokens", ctx)
    assert "cache:" not in buf.getvalue()


def test_tokens_shows_summarizer_after_activity(
    ctx_with_tracker: tuple[SlashContext, UsageTracker, StringIO],
) -> None:
    from jac.runtime.tool_summarize import get_summarizer_stats, reset_summarizer_stats

    reset_summarizer_stats()
    stats = get_summarizer_stats()
    stats.calls = 2
    stats.original_tokens = 20_000
    stats.summary_tokens = 1_500
    stats.summarizer_input_tokens = 20_100
    stats.summarizer_output_tokens = 1_500
    try:
        ctx, _tracker, buf = ctx_with_tracker
        dispatch("/tokens", ctx)
        out = buf.getvalue()
        assert "summarize:" in out
        assert "calls=2" in out
        assert "original=20,000" in out
        assert "saved=18,500" in out
    finally:
        reset_summarizer_stats()
