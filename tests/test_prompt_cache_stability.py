"""Phase A.2 smoke test — instructions byte-stable across turns.

The prompt-cache fix in `jac.workspace.context` requires that the system
instructions Gru sees are identical across consecutive turns when nothing
*actually* changed (no `remember()` write, no AGENTS.md edit, no date
rollover). A cache-busting field — second-precision time, a fresh uuid,
a per-call counter — would invalidate Anthropic's prompt cache every
turn, doubling token cost on long sessions.

This test is the regression net: it calls the instructions callable
twice in quick succession and asserts the result is byte-identical.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jac.capabilities.context import make_context_capability
from jac.workspace import paths
from jac.workspace.context import format_session_datetime


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stub the workspace paths so neither user nor project AGENTS.md leaks in."""
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    monkeypatch.setattr(paths, "USER_CONTEXT_FILE", tmp_path / "_missing_user_ctx.md")
    monkeypatch.setattr(paths, "USER_MEMORY_FILE", tmp_path / "_missing_user_mem.md")
    yield


def test_session_datetime_omits_time_of_day() -> None:
    """Regression: second-precision time in the system prompt was the
    Phase A.2 cache-buster. Date + weekday are fine (day granularity →
    at most one miss per midnight), but ``HH:MM:SS`` must be gone."""
    rendered = format_session_datetime()
    # No colon-separated clock pieces, no AM/PM, no timezone label.
    assert ":" not in rendered.split("\n", 1)[1], (
        f"format_session_datetime() still contains a clock: {rendered!r}"
    )
    assert " AM" not in rendered and " PM" not in rendered


def test_context_instructions_are_byte_stable_across_calls() -> None:
    """Two back-to-back calls with no underlying file change must produce
    byte-identical instructions. This is what Anthropic's prompt cache
    keys on — any drift invalidates the cached prefix."""
    cap = make_context_capability("BASE PROMPT BODY")
    callable_instructions = cap.get_instructions()
    first = callable_instructions(None)
    second = callable_instructions(None)
    assert first == second, (
        "ContextCapability.get_instructions() returned drifting output — "
        "something in load_session_context() is varying per call and will "
        "bust the prompt cache every turn.\n\n"
        f"--- first ---\n{first}\n\n--- second ---\n{second}"
    )


def test_context_instructions_change_when_memory_changes(tmp_path: Path) -> None:
    """The flip side: when memory.md actually changes, the cache *should*
    invalidate. Confirms we haven't over-cached and frozen the model out
    of mid-session `remember()` writes."""
    mem_file = tmp_path / ".agents" / "memory.md"
    mem_file.parent.mkdir(parents=True, exist_ok=True)
    mem_file.write_text("fact one\n", encoding="utf-8")

    cap = make_context_capability("BASE")
    callable_instructions = cap.get_instructions()
    before = callable_instructions(None)

    mem_file.write_text("fact one\nfact two\n", encoding="utf-8")
    after = callable_instructions(None)

    assert before != after
    assert "fact two" in after and "fact two" not in before
