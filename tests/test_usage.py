"""Tests for Phase 1.7.f — UsageTracker (D25)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from jac.runtime.bus import EventBus
from jac.runtime.events import BudgetHardStop, BudgetWarning
from jac.runtime.usage import (
    BudgetLimits,
    UsageTracker,
    load_project_baseline,
    make_usage_tracker,
)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _drain(bus: EventBus) -> list[Any]:
    events: list[Any] = []
    while not bus._queue.empty():  # type: ignore[attr-defined]
        events.append(bus._queue.get_nowait())  # type: ignore[attr-defined]
    return events


def _limits(
    *,
    session_input: int | None = None,
    session_total: int | None = None,
    project_total: int | None = None,
    warn_pct: int = 80,
    hardstop_pct: int = 100,
) -> BudgetLimits:
    return BudgetLimits(
        session_input_tokens=session_input,
        session_total_tokens=session_total,
        project_total_tokens=project_total,
        warn_pct=warn_pct,
        hardstop_pct=hardstop_pct,
    )


# ---------- counter accumulation ----------


def test_record_accumulates_session_counters(tmp_path: Path) -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1",
        bus=bus,
        usage_file=tmp_path / "usage.jsonl",
        limits=_limits(),
    )
    _run(tracker.record(input_tokens=100, output_tokens=50))
    _run(tracker.record(input_tokens=200, output_tokens=75))
    assert tracker.counters.input_tokens == 300
    assert tracker.counters.output_tokens == 125
    assert tracker.counters.total_tokens == 425


def test_record_ignores_negative_token_counts(tmp_path: Path) -> None:
    """RunUsage shouldn't go negative, but clamp defensively so we never
    silently subtract from the running total."""
    tracker = UsageTracker(
        session_id="s1",
        bus=None,
        usage_file=tmp_path / "usage.jsonl",
        limits=_limits(),
    )
    _run(tracker.record(input_tokens=-5, output_tokens=10))
    assert tracker.counters.input_tokens == 0
    assert tracker.counters.output_tokens == 10


# ---------- JSONL persistence ----------


def test_record_appends_jsonl_line(tmp_path: Path) -> None:
    usage_file = tmp_path / "usage.jsonl"
    tracker = UsageTracker(
        session_id="s1", bus=None, usage_file=usage_file, limits=_limits()
    )
    _run(tracker.record(input_tokens=42, output_tokens=8))

    lines = usage_file.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["session_id"] == "s1"
    assert entry["input_tokens"] == 42
    assert entry["output_tokens"] == 8
    assert isinstance(entry["ts"], int)


def test_record_no_persistence_when_file_is_none() -> None:
    """Headless / test callers can opt out of JSONL writes."""
    tracker = UsageTracker(
        session_id="s1", bus=None, usage_file=None, limits=_limits()
    )
    _run(tracker.record(input_tokens=100, output_tokens=50))
    # No exception; counters still tick.
    assert tracker.counters.input_tokens == 100


def test_load_project_baseline_sums_other_sessions(tmp_path: Path) -> None:
    usage_file = tmp_path / "usage.jsonl"
    usage_file.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"session_id": "old1", "ts": 1, "input_tokens": 100, "output_tokens": 50},
                {"session_id": "old2", "ts": 2, "input_tokens": 200, "output_tokens": 75},
                {"session_id": "current", "ts": 3, "input_tokens": 1, "output_tokens": 1},
            ]
        )
    )
    baseline = load_project_baseline(usage_file, exclude_session_id="current")
    # 100 + 50 + 200 + 75 = 425; current is excluded.
    assert baseline == 425


def test_load_project_baseline_skips_malformed_lines(tmp_path: Path) -> None:
    """Per the 1.7.g discipline: bad lines never block startup."""
    usage_file = tmp_path / "usage.jsonl"
    usage_file.write_text(
        "not valid json\n"
        + json.dumps({"session_id": "old", "input_tokens": 50, "output_tokens": 25})
        + "\n"
        + "{partial json\n"
    )
    baseline = load_project_baseline(usage_file, exclude_session_id="current")
    assert baseline == 75


def test_load_project_baseline_returns_zero_when_no_file(tmp_path: Path) -> None:
    assert load_project_baseline(tmp_path / "missing.jsonl", "any") == 0


def test_make_usage_tracker_preloads_baseline(tmp_path: Path) -> None:
    usage_file = tmp_path / "usage.jsonl"
    usage_file.write_text(
        json.dumps({"session_id": "old", "input_tokens": 100, "output_tokens": 50})
        + "\n"
    )
    tracker = make_usage_tracker(
        session_id="new",
        bus=None,
        usage_file=usage_file,
        limits=_limits(),
    )
    assert tracker.project_baseline == 150
    assert tracker.project_total_tokens == 150  # current session contributes 0


# ---------- threshold events ----------


def test_record_emits_warn_at_warn_pct(tmp_path: Path) -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1",
        bus=bus,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    # 60% of 1000 — no event yet.
    _run(tracker.record(input_tokens=300, output_tokens=300))
    assert _drain(bus) == []
    # 80% of 1000 — warning fires.
    _run(tracker.record(input_tokens=100, output_tokens=100))
    events = _drain(bus)
    assert len(events) == 1
    assert isinstance(events[0], BudgetWarning)
    assert events[0].kind == "session_total"
    assert events[0].pct == 80


def test_record_dedups_warn_across_subsequent_turns(tmp_path: Path) -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1",
        bus=bus,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    _run(tracker.record(input_tokens=400, output_tokens=400))  # 80%
    _drain(bus)  # consume warn
    _run(tracker.record(input_tokens=50, output_tokens=50))  # 90%
    assert _drain(bus) == []  # no second warn


def test_record_emits_hardstop_at_100_pct(tmp_path: Path) -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1",
        bus=bus,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    _run(tracker.record(input_tokens=500, output_tokens=500))  # 100%
    events = _drain(bus)
    # 100% crosses BOTH thresholds — warn first, then hardstop, on the same turn.
    kinds = [type(e).__name__ for e in events]
    assert "BudgetHardStop" in kinds
    hardstop = next(e for e in events if isinstance(e, BudgetHardStop))
    assert hardstop.kind == "session_total"
    assert hardstop.budget == 1000


def test_record_separate_kinds_emit_independently(tmp_path: Path) -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1",
        bus=bus,
        usage_file=None,
        limits=_limits(session_input=500, session_total=2000),
    )
    # 400 input crosses session_input warn (80% of 500); session_total is 800/2000 (40%).
    _run(tracker.record(input_tokens=400, output_tokens=400))
    events = _drain(bus)
    kinds = [(type(e).__name__, getattr(e, "kind", None)) for e in events]
    assert ("BudgetWarning", "session_input") in kinds
    assert all(k[1] != "session_total" for k in kinds if k[0] == "BudgetWarning")


def test_no_events_when_no_budget_configured(tmp_path: Path) -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1", bus=bus, usage_file=None, limits=_limits()
    )
    _run(tracker.record(input_tokens=10_000_000, output_tokens=10_000_000))
    assert _drain(bus) == []


# ---------- is_over_hardstop (used by REPL pre-turn check) ----------


def test_is_over_hardstop_returns_none_when_under_budget() -> None:
    tracker = UsageTracker(
        session_id="s1",
        bus=None,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    _run(tracker.record(input_tokens=400, output_tokens=400))  # 80%
    assert tracker.is_over_hardstop() is None


def test_is_over_hardstop_reports_tripped_kind() -> None:
    tracker = UsageTracker(
        session_id="s1",
        bus=None,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    _run(tracker.record(input_tokens=500, output_tokens=500))  # 100%
    result = tracker.is_over_hardstop()
    assert result is not None
    kind, used, budget = result
    assert kind == "session_total"
    assert used == 1000
    assert budget == 1000


def test_is_over_hardstop_returns_none_when_no_budget() -> None:
    tracker = UsageTracker(
        session_id="s1", bus=None, usage_file=None, limits=_limits()
    )
    _run(tracker.record(input_tokens=999_999, output_tokens=999_999))
    assert tracker.is_over_hardstop() is None


# ---------- status_pct (status bar) ----------


def test_status_pct_returns_none_when_no_budget() -> None:
    tracker = UsageTracker(
        session_id="s1", bus=None, usage_file=None, limits=_limits()
    )
    assert tracker.status_pct() is None


def test_status_pct_returns_max_across_budgets() -> None:
    tracker = UsageTracker(
        session_id="s1",
        bus=None,
        usage_file=None,
        limits=_limits(session_input=1000, session_total=10000),
    )
    _run(tracker.record(input_tokens=600, output_tokens=100))
    # session_input = 600/1000 = 60%; session_total = 700/10000 = 7%. Max is 60.
    assert tracker.status_pct() == 60


# ---------- extend ----------


def test_extend_raises_the_limit_and_resets_dedup() -> None:
    bus = EventBus()
    tracker = UsageTracker(
        session_id="s1",
        bus=bus,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    _run(tracker.record(input_tokens=500, output_tokens=500))  # 100%, warn + hardstop fire
    _drain(bus)
    new_limit = tracker.extend("session_total", 1000)
    assert new_limit == 2000
    assert tracker.is_over_hardstop() is None
    # After extension, next threshold crossing should fire again.
    _run(tracker.record(input_tokens=300, output_tokens=300))  # 1600/2000 = 80%
    events = _drain(bus)
    assert any(isinstance(e, BudgetWarning) for e in events)


def test_extend_rejects_non_positive_amount() -> None:
    tracker = UsageTracker(
        session_id="s1",
        bus=None,
        usage_file=None,
        limits=_limits(session_total=1000),
    )
    try:
        tracker.extend("session_total", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("extend should reject non-positive amounts")
