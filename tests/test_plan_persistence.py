"""Tests for Phase 1.7.g — plan persistence on resume (D27).

Coverage:

- The capability writes ``plan.json`` atomically on every mutation when
  constructed with a ``plan_file``.
- :meth:`jac.runtime.session.Session.load_plan` round-trips through
  ``plan.json``, flipping ``in_progress`` → ``pending`` on resume.
- Missing / malformed files degrade with a warning rather than failing
  the resume.
- ``switch_session`` re-points an existing capability at a new session's
  file and reseeds its store in place (so the tool closures stay valid).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest

from jac.capabilities.plan import PlanCapability, make_plan_capability
from jac.runtime.bus import EventBus
from jac.runtime.events import PlanReplaced
from jac.runtime.session import Session
from jac.workspace import paths


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ---------- fixtures ----------


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point session machinery at a tmp project root.

    Mirrors the pattern in ``tests/test_slash.py`` — clears the cached
    ``find_project_root`` then monkeypatches it + ``project_sessions_dir``.
    """
    sessions_dir = tmp_path / ".agents" / "sessions"
    sessions_dir.mkdir(parents=True)
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(paths, "project_sessions_dir", lambda: sessions_dir)
    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    yield tmp_path


def _collect_events(bus: EventBus) -> list[Any]:
    """Drain whatever the bus has *now* (non-blocking) into a list."""
    events: list[Any] = []
    while not bus._queue.empty():  # type: ignore[attr-defined]
        events.append(bus._queue.get_nowait())  # type: ignore[attr-defined]
    return events


# ---------- capability persistence ----------


def test_plan_capability_writes_file_on_replace(tmp_path: Path) -> None:
    plan_file = tmp_path / "session" / "plan.json"
    cap = make_plan_capability(bus=None, plan_file=plan_file)
    tools = cap._build_tools()
    plan_tool = tools[0]

    _run(plan_tool(reason="initial plan", steps=["read", "edit", "test"]))

    assert plan_file.is_file()
    data = json.loads(plan_file.read_text())
    assert data["version"] == 1
    assert [s["text"] for s in data["steps"]] == ["read", "edit", "test"]
    assert all(s["status"] == "pending" for s in data["steps"])


def test_plan_capability_writes_file_on_update(tmp_path: Path) -> None:
    plan_file = tmp_path / "session" / "plan.json"
    cap = make_plan_capability(bus=None, plan_file=plan_file)
    plan_tool, update_tool, _get_tool = cap._build_tools()

    _run(plan_tool(reason="setup", steps=["read", "edit"]))
    _run(update_tool(reason="starting", step=1, status="in_progress"))

    data = json.loads(plan_file.read_text())
    assert data["steps"][0]["status"] == "in_progress"
    assert data["steps"][1]["status"] == "pending"


def test_plan_capability_ephemeral_when_no_file(tmp_path: Path) -> None:
    """Without ``plan_file``, the capability never touches disk."""
    cap = make_plan_capability(bus=None)
    plan_tool = cap._build_tools()[0]
    _run(plan_tool(reason="ephemeral", steps=["one", "two"]))
    # No file should be written anywhere.
    assert not any(tmp_path.rglob("plan.json"))


def test_plan_capability_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    plan_file = tmp_path / "session" / "plan.json"
    cap = make_plan_capability(bus=None, plan_file=plan_file)
    plan_tool = cap._build_tools()[0]
    _run(plan_tool(reason="setup", steps=["one"]))
    assert not (plan_file.parent / "plan.json.tmp").exists()


def test_initial_steps_seeds_the_store_without_writing(tmp_path: Path) -> None:
    """A capability built with ``initial_steps`` reflects them in
    ``get_plan`` but doesn't immediately rewrite the file — the file is
    already the source of truth."""
    plan_file = tmp_path / "session" / "plan.json"
    cap = make_plan_capability(
        bus=None,
        plan_file=plan_file,
        initial_steps=[
            {"text": "step a", "status": "completed"},
            {"text": "step b", "status": "pending"},
        ],
    )
    get_tool = cap._build_tools()[2]
    rendered = get_tool(reason="check seeded")
    assert "step a" in rendered and "step b" in rendered
    assert "completed" in rendered
    # File was never written because no mutation happened.
    assert not plan_file.is_file()


# ---------- session load round-trip ----------


def test_session_load_plan_returns_empty_when_no_file(isolated_project: Path) -> None:
    session = Session.new()
    steps, warning = session.load_plan()
    assert steps == []
    assert warning is None


def test_session_load_plan_round_trip_flips_in_progress(isolated_project: Path) -> None:
    session = Session.new()
    session.session_dir.mkdir(parents=True, exist_ok=True)
    cap = make_plan_capability(bus=None, plan_file=session.plan_file)
    plan_tool, update_tool, _get_tool = cap._build_tools()
    _run(plan_tool(reason="setup", steps=["one", "two", "three"]))
    _run(update_tool(reason="starting two", step=2, status="in_progress"))
    _run(update_tool(reason="done one", step=1, status="completed"))

    restored, warning = session.load_plan()
    assert warning is None
    statuses = {s["text"]: s["status"] for s in restored}
    # Completed survives the round-trip; in_progress flips to pending.
    assert statuses == {"one": "completed", "two": "pending", "three": "pending"}


def test_session_load_plan_malformed_json_warns_and_returns_empty(
    isolated_project: Path,
) -> None:
    session = Session.new()
    session.session_dir.mkdir(parents=True, exist_ok=True)
    session.plan_file.write_text("{not valid json")
    steps, warning = session.load_plan()
    assert steps == []
    assert warning is not None
    assert "unreadable" in warning


def test_session_load_plan_wrong_shape_warns_and_returns_empty(
    isolated_project: Path,
) -> None:
    session = Session.new()
    session.session_dir.mkdir(parents=True, exist_ok=True)
    session.plan_file.write_text(json.dumps({"steps": "not a list"}))
    steps, warning = session.load_plan()
    assert steps == []
    assert warning is not None


def test_session_load_plan_unknown_status_warns_and_returns_empty(
    isolated_project: Path,
) -> None:
    session = Session.new()
    session.session_dir.mkdir(parents=True, exist_ok=True)
    session.plan_file.write_text(
        json.dumps({"version": 1, "steps": [{"text": "x", "status": "bogus"}]})
    )
    steps, warning = session.load_plan()
    assert steps == []
    assert warning is not None
    assert "bogus" in warning


def test_session_load_plan_empty_steps_text_warns(isolated_project: Path) -> None:
    session = Session.new()
    session.session_dir.mkdir(parents=True, exist_ok=True)
    session.plan_file.write_text(
        json.dumps({"version": 1, "steps": [{"text": "  ", "status": "pending"}]})
    )
    steps, warning = session.load_plan()
    assert steps == []
    assert warning is not None


# ---------- capability switch_session ----------


def test_switch_session_repoints_file_and_clears_store(isolated_project: Path) -> None:
    """The capability's tool closures keep working across a switch because
    the store is mutated in place rather than replaced."""
    bus = EventBus()
    old = Session(session_id="20260523T10-00-00", message_history=[])
    old.session_dir.mkdir(parents=True, exist_ok=True)
    cap: PlanCapability = make_plan_capability(bus=bus, plan_file=old.plan_file)
    plan_tool, _update_tool, get_tool = cap._build_tools()

    _run(plan_tool(reason="old plan", steps=["a", "b"]))
    assert old.plan_file.is_file()

    new = Session(session_id="20260523T11-00-00", message_history=[])
    new.session_dir.mkdir(parents=True, exist_ok=True)
    _run(cap.switch_session(new.plan_file, restored_steps=None))

    assert cap.plan_file == new.plan_file
    rendered = get_tool(reason="check empty")
    assert "no plan" in rendered.lower()

    # Subsequent mutation writes to the NEW file, not the old one's leftover state.
    _run(plan_tool(reason="new plan", steps=["x"]))
    new_data = json.loads(new.plan_file.read_text())
    assert [s["text"] for s in new_data["steps"]] == ["x"]
    # Old session's file is untouched by the post-switch write.
    old_data = json.loads(old.plan_file.read_text())
    assert [s["text"] for s in old_data["steps"]] == ["a", "b"]


def test_switch_session_with_restored_steps_emits_event(isolated_project: Path) -> None:
    bus = EventBus()
    cap = make_plan_capability(bus=bus, plan_file=None)

    new = Session(session_id="20260523T12-00-00", message_history=[])
    new.session_dir.mkdir(parents=True, exist_ok=True)
    restored = [{"text": "alpha", "status": "completed"}, {"text": "beta", "status": "pending"}]
    _run(cap.switch_session(new.plan_file, restored_steps=restored))

    events = _collect_events(bus)
    assert len(events) == 1
    assert isinstance(events[0], PlanReplaced)
    assert [s.text for s in events[0].steps] == ["alpha", "beta"]


def test_switch_session_empty_does_not_emit_event() -> None:
    """Clearing to an empty plan shouldn't broadcast a (no-op) panel paint."""
    bus = EventBus()
    cap = make_plan_capability(bus=bus, plan_file=None)
    _run(cap.switch_session(new_plan_file=None, restored_steps=None))
    assert _collect_events(bus) == []


# ---------- end-to-end ----------


def test_full_cycle_persist_load_seed_continue(isolated_project: Path) -> None:
    """Persist with one capability, load via Session, seed a fresh capability,
    continue updating. This is the on-resume flow the REPL drives."""
    bus = EventBus()
    session = Session.new()
    session.session_dir.mkdir(parents=True, exist_ok=True)

    cap_a = make_plan_capability(bus=bus, plan_file=session.plan_file)
    plan_a, update_a, _get_a = cap_a._build_tools()
    _run(plan_a(reason="run 1", steps=["read", "edit", "test"]))
    _run(update_a(reason="started edit", step=2, status="in_progress"))
    _run(update_a(reason="finished read", step=1, status="completed"))

    # Simulate process death + resume — load via the (cold) Session API.
    restored, warning = session.load_plan()
    assert warning is None

    bus2 = EventBus()
    cap_b = make_plan_capability(bus=bus2, plan_file=session.plan_file, initial_steps=restored)
    _plan_b, update_b, get_b = cap_b._build_tools()
    rendered = get_b(reason="see what survived")
    assert "[completed]" in rendered
    # The mid-step actor died — its in_progress flipped back to pending.
    assert "[in_progress]" not in rendered

    # Continue the work on resume.
    _run(update_b(reason="resuming edit", step=2, status="in_progress"))
    after_resume = json.loads(session.plan_file.read_text())
    statuses = {s["text"]: s["status"] for s in after_resume["steps"]}
    assert statuses["read"] == "completed"
    assert statuses["edit"] == "in_progress"
    assert statuses["test"] == "pending"
