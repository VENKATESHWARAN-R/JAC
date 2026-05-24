"""Plan capability — Gru's visible multi-step todo list.

Gru can declare an intended plan with :func:`plan`, then update step
statuses as work progresses with :func:`update_plan`. The renderer draws
the plan as a checklist panel, so the user can see what Gru is *about to
do* — not just what it's doing right now.

Design notes:

- **State lives on the capability instance**, not in a module global.
  One ``PlanCapability`` per agent. Minions intentionally don't get this
  capability; their work is scoped via a task packet, not a freeform plan.
- **No approval required.** The plan is a side-effect-free todo list.
  Adding a HITL gate here would be a double prompt without value.
- **Persists on every mutation (D27).** When constructed with a
  ``plan_file=`` path, the capability writes the full plan to disk after
  each ``plan(...)`` / ``update_plan(...)`` call. On resume, the REPL
  loads the file via :meth:`jac.runtime.session.Session.load_plan` and
  bootstraps the store via ``initial_steps=``. Without a ``plan_file``
  the capability is ephemeral (tests, headless callers).
- **Bus is optional.** Construct with ``make_plan_capability(bus)`` when
  you want renderer integration; pass ``None`` (or use ``PlanCapability()``
  directly) for headless / test contexts.

Events :class:`PlanReplaced` and :class:`PlanStepUpdated` carry enough
state for the renderer to redraw without reaching into the capability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic_ai.capabilities import AbstractCapability

from jac.runtime.bus import EventBus
from jac.runtime.events import (
    PlanReplaced,
    PlanStepStatus,
    PlanStepUpdated,
    PlanStepView,
)
from jac.tools import jac_function_toolset, jac_tool

_VALID_STATUSES: frozenset[PlanStepStatus] = frozenset({"pending", "in_progress", "completed"})
_MAX_STEPS = 25
_MAX_STEP_CHARS = 240
_PLAN_SCHEMA_VERSION = 1
_STATUS_GLYPH: dict[PlanStepStatus, str] = {
    "pending": "○",
    "in_progress": "◐",
    "completed": "●",
}


@dataclass
class _PlanStep:
    text: str
    status: PlanStepStatus = "pending"


@dataclass
class PlanStore:
    """In-memory holder for the current plan.

    Pure data — no I/O, no event emission. The capability wires bus
    emission and disk persistence around store mutations so the store
    stays trivial to test.
    """

    steps: list[_PlanStep] = field(default_factory=list)

    def replace(self, steps: list[str]) -> list[_PlanStep]:
        if not steps:
            raise ValueError("plan must contain at least one step.")
        if len(steps) > _MAX_STEPS:
            raise ValueError(
                f"plan has {len(steps)} steps; max is {_MAX_STEPS}. "
                "Split long plans into phases or drop low-value steps."
            )
        cleaned: list[_PlanStep] = []
        for raw in steps:
            text = raw.strip()
            if not text:
                raise ValueError("plan steps must be non-empty strings.")
            if len(text) > _MAX_STEP_CHARS:
                raise ValueError(f"plan step exceeds {_MAX_STEP_CHARS} chars: {text[:60]}…")
            cleaned.append(_PlanStep(text=text))
        self.steps = cleaned
        return list(self.steps)

    def update(self, index: int, status: PlanStepStatus) -> _PlanStep:
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; must be one of {sorted(_VALID_STATUSES)}."
            )
        if not self.steps:
            raise ValueError("no plan to update; call `plan` first to declare your steps.")
        if not (1 <= index <= len(self.steps)):
            raise ValueError(
                f"step index {index} out of range; plan has {len(self.steps)} step(s) (1-based)."
            )
        step = self.steps[index - 1]
        step.status = status
        return step

    def load(self, items: list[dict[str, str]]) -> None:
        """Bootstrap the store from already-validated step dicts.

        Used by the REPL on resume to seed the capability with the
        previous session's checklist. ``items`` is expected to be the
        output of :meth:`jac.runtime.session.Session.load_plan` —
        per-step ``{"text", "status"}`` with statuses already normalized
        (``in_progress`` flipped to ``pending``).
        """
        # `status` has already been validated by Session.load_plan, but the
        # dict's value type is plain `str` — cast to satisfy the Literal.
        self.steps = [
            _PlanStep(text=i["text"], status=cast(PlanStepStatus, i["status"])) for i in items
        ]

    def clear(self) -> None:
        """Drop all steps. Used on ``/clear`` / ``/resume`` session swap.

        Mutates in place so the capability's tool closures (which captured
        ``store`` by reference) keep working against the same object.
        """
        self.steps = []

    def render(self) -> str:
        if not self.steps:
            return "(no plan — Gru hasn't declared one yet)"
        lines: list[str] = []
        for i, step in enumerate(self.steps, start=1):
            glyph = _STATUS_GLYPH[step.status]
            lines.append(f"{i}. {glyph} {step.text}  [{step.status}]")
        return "\n".join(lines)

    def snapshot(self) -> tuple[PlanStepView, ...]:
        return tuple(
            PlanStepView(index=i, text=s.text, status=s.status)
            for i, s in enumerate(self.steps, start=1)
        )

    def to_json(self) -> str:
        """Serialize the store to the persisted ``plan.json`` shape."""
        return json.dumps(
            {
                "version": _PLAN_SCHEMA_VERSION,
                "steps": [{"text": s.text, "status": s.status} for s in self.steps],
            },
            indent=2,
        )


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (tempfile + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


@dataclass
class PlanCapability(AbstractCapability[Any]):
    """Capability exposing ``plan`` / ``update_plan`` / ``get_plan`` tools.

    Pass a ``bus`` to get renderer-visible events. With ``bus=None`` the
    tools still work; they just don't broadcast.

    Pass ``plan_file`` to enable disk persistence (D27): every mutation
    rewrites the file atomically. Without it the capability is
    ephemeral — fine for tests and headless callers.
    """

    bus: EventBus | None = None
    store: PlanStore = field(default_factory=PlanStore)
    plan_file: Path | None = None

    def get_toolset(self) -> Any:
        return jac_function_toolset(*self._build_tools())

    def _persist(self) -> None:
        """Persist the current store to ``plan_file`` if configured."""
        if self.plan_file is not None:
            _atomic_write(self.plan_file, self.store.to_json())

    async def switch_session(
        self,
        new_plan_file: Path | None,
        restored_steps: list[dict[str, str]] | None,
    ) -> None:
        """Repoint the capability at a different session's ``plan.json``.

        Called by the REPL on ``/clear`` and ``/resume`` so subsequent
        mutations write to the correct file and the renderer paints the
        new session's checklist on the next turn.

        Mutates ``self.store`` in place (rather than replacing it) so the
        tool closures from :meth:`_build_tools` — which captured the
        store object by reference — keep working against the new state.
        """
        self.plan_file = new_plan_file
        self.store.clear()
        if restored_steps:
            self.store.load(restored_steps)
            if self.bus is not None:
                await self.bus.emit(PlanReplaced(steps=self.store.snapshot()))

    def _build_tools(self) -> list[Any]:
        bus = self.bus
        store = self.store
        persist = self._persist

        async def _emit(event: Any) -> None:
            if bus is not None:
                await bus.emit(event)

        @jac_tool
        async def plan(reason: str, steps: list[str]) -> str:
            """Declare a multi-step plan.

            Replaces any prior plan with ``steps`` — every step starts as
            ``pending``. Use this whenever the work in front of you needs
            more than one tool call and the user benefits from seeing your
            intent. For one-shot tasks, skip the plan and just do the work.

            Args:
                reason: One-sentence justification (e.g. "user asked for
                    feature X; outlining the four-step refactor first").
                steps: Ordered list of short imperative phrases — what
                    you will do, in order. 1-25 steps, each <=240 chars.

            Returns:
                Rendered plan as a numbered checklist.
            """
            store.replace(steps)
            persist()
            await _emit(PlanReplaced(steps=store.snapshot()))
            return store.render()

        @jac_tool
        async def update_plan(reason: str, step: int, status: PlanStepStatus) -> str:
            """Update one step's status. ``step`` is 1-based.

            Use this immediately when starting a step (``in_progress``)
            and again when it finishes (``completed``). The user is
            watching the checklist — keep it accurate.

            Args:
                reason: One-sentence justification (e.g. "starting the
                    refactor of the auth module").
                step: 1-based index into the current plan.
                status: ``pending`` | ``in_progress`` | ``completed``.

            Returns:
                Rendered plan as a numbered checklist.
            """
            updated = store.update(step, status)
            persist()
            await _emit(PlanStepUpdated(index=step, status=updated.status, text=updated.text))
            return store.render()

        @jac_tool
        def get_plan(reason: str) -> str:
            """Return the current plan as a numbered checklist.

            Use sparingly — your own tool-call history already shows you
            what plan you set. Useful mostly after resuming a session
            where you've forgotten the prior intent.
            """
            return store.render()

        return [plan, update_plan, get_plan]


def make_plan_capability(
    bus: EventBus | None = None,
    *,
    plan_file: Path | None = None,
    initial_steps: list[dict[str, str]] | None = None,
) -> PlanCapability:
    """Build a fresh :class:`PlanCapability`. One per agent / session.

    Args:
        bus: Optional event bus for renderer integration. ``None`` is
            valid for headless / test contexts; events are simply not
            emitted.
        plan_file: Optional path to persist the plan to. When set, every
            mutation atomically rewrites the file. Without it the
            capability is ephemeral.
        initial_steps: Pre-existing steps to bootstrap the store with
            (typically from :meth:`jac.runtime.session.Session.load_plan`
            on resume). Each entry is ``{"text", "status"}``. Statuses
            should already be normalized — ``in_progress`` flipped to
            ``pending`` — by the loader.
    """
    store = PlanStore()
    if initial_steps:
        store.load(initial_steps)
    return PlanCapability(bus=bus, store=store, plan_file=plan_file)
