"""Token-usage tracking and budget enforcement (D25).

Cost guardrail for paid providers — *token*-based, never dollar-based.
Pydantic AI exposes per-turn usage via :meth:`pydantic_ai.AgentRunResult.usage`
(``RunUsage`` with ``input_tokens``, ``output_tokens``, ``total_tokens``);
the REPL feeds that into :meth:`UsageTracker.record` after every successful
turn. The tracker:

1. Accumulates per-session counters (input / output / total).
2. Appends one JSONL line per turn to ``<repo>/.agents/usage.jsonl`` so
   ``project_total_tokens`` budgets survive crash recovery and span across
   sessions (mirrors 1.7.g's "crash recovery is first-class" stance).
3. Compares the new totals against the three configured budgets
   (``session_input``, ``session_total``, ``project_total``) and emits
   :class:`BudgetWarning` once per ``(kind, threshold)`` pair when the
   warn threshold is crossed.
4. Surfaces :class:`BudgetHardStop` events when a hard-stop threshold is
   crossed; the REPL's pre-turn check refuses subsequent turns until the
   user runs ``/budget extend``.

Budgets are **opt-in only** — when all three knobs in
:class:`jac.config.BudgetSettings` are ``None``, the tracker is effectively
inert (it still counts and persists, but no threshold checks fire). This is
the documented zero-surprise default: first-time users never get
mid-conversation hard-stops they didn't ask for.

``/cost`` is deliberately absent — D25 refuses to ship a dollar-conversion
table. The user maps tokens to whatever pricing they negotiated.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jac.runtime.events import BudgetHardStop, BudgetKind, BudgetWarning, EventBus


@dataclass
class _SessionCounters:
    """Per-session running totals."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class BudgetLimits:
    """Snapshot of budget knobs the tracker checks against.

    Pulled from :class:`jac.config.BudgetSettings` at construction. Held
    on the tracker (not the settings singleton) so ``/budget extend`` can
    mutate the live limit without writing to disk — config stays the
    declared baseline; the override is session-scoped.
    """

    session_input_tokens: int | None
    session_total_tokens: int | None
    project_total_tokens: int | None
    warn_pct: int
    hardstop_pct: int

    def limit_for(self, kind: BudgetKind) -> int | None:
        if kind == "session_input":
            return self.session_input_tokens
        if kind == "session_total":
            return self.session_total_tokens
        return self.project_total_tokens

    def any_configured(self) -> bool:
        return any(
            limit is not None
            for limit in (
                self.session_input_tokens,
                self.session_total_tokens,
                self.project_total_tokens,
            )
        )


@dataclass
class UsageTracker:
    """Per-session usage counters + JSONL persistence + budget guardrails.

    Constructed once per REPL session by the loop. Holds a reference to
    the event bus so it can emit :class:`BudgetWarning` /
    :class:`BudgetHardStop` directly — no separate plumbing required.
    """

    session_id: str
    bus: EventBus | None
    usage_file: Path | None
    limits: BudgetLimits
    counters: _SessionCounters = field(default_factory=_SessionCounters)
    project_baseline: int = 0
    """Sum of input+output across **every prior session** in this project.
    Loaded once on construction from ``usage.jsonl``; never mutated again
    (this session's contributions live in ``counters``)."""
    _warned: set[BudgetKind] = field(default_factory=set)
    """Dedup set so each ``(kind, warn)`` event fires at most once."""
    _stopped: set[BudgetKind] = field(default_factory=set)
    """Dedup set so each ``(kind, hardstop)`` event fires at most once."""

    @property
    def project_total_tokens(self) -> int:
        """Live project total: prior-session baseline + current-session usage."""
        return self.project_baseline + self.counters.total_tokens

    def usage_for(self, kind: BudgetKind) -> int:
        if kind == "session_input":
            return self.counters.input_tokens
        if kind == "session_total":
            return self.counters.total_tokens
        return self.project_total_tokens

    async def record(self, input_tokens: int, output_tokens: int) -> None:
        """Record one completed turn.

        Appends to :attr:`usage_file` if set, bumps in-memory counters,
        then checks every configured budget for a freshly-crossed warn or
        hardstop threshold and emits the appropriate event.
        """
        self.counters.input_tokens += max(0, input_tokens)
        self.counters.output_tokens += max(0, output_tokens)
        self._append_jsonl(input_tokens, output_tokens)
        await self._check_thresholds()

    def _append_jsonl(self, input_tokens: int, output_tokens: int) -> None:
        if self.usage_file is None:
            return
        line = json.dumps(
            {
                "session_id": self.session_id,
                "ts": int(time.time()),
                "input_tokens": int(max(0, input_tokens)),
                "output_tokens": int(max(0, output_tokens)),
            }
        )
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    async def _check_thresholds(self) -> None:
        if self.bus is None or not self.limits.any_configured():
            return
        for kind in ("session_input", "session_total", "project_total"):
            kind_typed: BudgetKind = kind  # type: ignore[assignment]
            limit = self.limits.limit_for(kind_typed)
            if limit is None or limit <= 0:
                continue
            used = self.usage_for(kind_typed)
            pct = int((used / limit) * 100)
            if pct >= self.limits.hardstop_pct and kind_typed not in self._stopped:
                self._stopped.add(kind_typed)
                await self.bus.emit(BudgetHardStop(kind=kind_typed, used=used, budget=limit))
            elif pct >= self.limits.warn_pct and kind_typed not in self._warned:
                self._warned.add(kind_typed)
                await self.bus.emit(
                    BudgetWarning(kind=kind_typed, used=used, budget=limit, pct=pct)
                )

    def is_over_hardstop(self) -> tuple[BudgetKind, int, int] | None:
        """Return the first ``(kind, used, budget)`` that's at/above hardstop.

        Used by the REPL's pre-turn refusal check — distinct from the
        event-driven warn/hardstop flow because refusal needs a *synchronous*
        decision before ``agent.run()`` is invoked.
        """
        if not self.limits.any_configured():
            return None
        for kind in ("session_input", "session_total", "project_total"):
            kind_typed: BudgetKind = kind  # type: ignore[assignment]
            limit = self.limits.limit_for(kind_typed)
            if limit is None or limit <= 0:
                continue
            used = self.usage_for(kind_typed)
            if int((used / limit) * 100) >= self.limits.hardstop_pct:
                return kind_typed, used, limit
        return None

    def status_pct(self) -> int | None:
        """Return the highest-used percent across configured budgets.

        Drives the status-bar ``bud:`` segment. Returns ``None`` when no
        budget is configured so the status bar can hide the segment.
        """
        if not self.limits.any_configured():
            return None
        max_pct = 0
        for kind in ("session_input", "session_total", "project_total"):
            kind_typed: BudgetKind = kind  # type: ignore[assignment]
            limit = self.limits.limit_for(kind_typed)
            if limit is None or limit <= 0:
                continue
            used = self.usage_for(kind_typed)
            max_pct = max(max_pct, int((used / limit) * 100))
        return max_pct

    def extend(self, kind: BudgetKind, additional_tokens: int) -> int:
        """Add ``additional_tokens`` to the named budget for the rest of
        this session. Returns the new limit. Resets the dedup state for
        the kind so warn/hardstop can fire again at the new threshold.

        Does **not** persist — config stays the declared baseline; the
        override is session-scoped.
        """
        if additional_tokens <= 0:
            raise ValueError("extend amount must be positive.")
        current = self.limits.limit_for(kind) or 0
        new_limit = current + additional_tokens
        if kind == "session_input":
            self.limits.session_input_tokens = new_limit
        elif kind == "session_total":
            self.limits.session_total_tokens = new_limit
        else:
            self.limits.project_total_tokens = new_limit
        self._warned.discard(kind)
        self._stopped.discard(kind)
        return new_limit


def load_project_baseline(usage_file: Path, exclude_session_id: str) -> int:
    """Sum ``input_tokens + output_tokens`` across every JSONL line in
    ``usage_file`` whose ``session_id`` differs from ``exclude_session_id``.

    Caller passes the currently-active session id so we don't double-count
    this session's lines (those are tracked in-memory by
    :class:`UsageTracker`). Malformed lines are skipped — never block REPL
    startup on a bad row, mirrors :meth:`Session.load_plan` discipline.
    """
    if not usage_file.is_file():
        return 0
    total = 0
    with usage_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                entry: Any = json.loads(line)
                if entry.get("session_id") == exclude_session_id:
                    continue
                total += int(entry.get("input_tokens", 0))
                total += int(entry.get("output_tokens", 0))
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                continue
    return total


def make_usage_tracker(
    *,
    session_id: str,
    bus: EventBus | None,
    usage_file: Path | None,
    limits: BudgetLimits,
) -> UsageTracker:
    """Construct a tracker with the project baseline preloaded from
    ``usage_file`` (excluding any lines for the current session)."""
    baseline = load_project_baseline(usage_file, session_id) if usage_file else 0
    return UsageTracker(
        session_id=session_id,
        bus=bus,
        usage_file=usage_file,
        limits=limits,
        project_baseline=baseline,
    )
