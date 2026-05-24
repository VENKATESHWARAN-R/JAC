"""Tests for ContextCapability — dynamic per-request instructions (Phase 5a).

The static ``_compose_instructions()`` path baked memory.md into a string
at agent-construction time, so mid-session ``remember()`` writes were
invisible until the agent rebuilt. The capability uses PAI's
``get_instructions()`` callable form to re-read on every request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jac.capabilities.context import make_context_capability
from jac.workspace import paths


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point user + project workspaces at tmp_path so memory writes don't pollute."""
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_WORKSPACE", user_jac)
    monkeypatch.setattr(paths, "USER_CONTEXT_FILE", user_jac / "AGENTS.md")
    monkeypatch.setattr(paths, "USER_MEMORY_FILE", user_jac / "memory.md")
    monkeypatch.chdir(tmp_path)
    paths.find_project_root.cache_clear()


def test_get_instructions_returns_callable_that_reads_files_at_call_time() -> None:
    """The capability returns a callable; each call re-reads memory.md."""
    cap = make_context_capability(base_prompt="BASE")
    instructions_fn = cap.get_instructions()
    assert callable(instructions_fn), (
        "get_instructions() must return a callable for dynamic re-read"
    )

    # First call — no memory.md yet.
    first = instructions_fn(None)
    assert "BASE" in first
    assert "# Session context" in first

    # Write user memory; next call must include it.
    paths.USER_MEMORY_FILE.write_text("- user prefers terse output\n")
    second = instructions_fn(None)
    assert "user prefers terse output" in second
    assert "user memory" in second  # the provenance header

    # Append to memory; third call must reflect the append.
    paths.USER_MEMORY_FILE.write_text("- user prefers terse output\n- user uses zsh\n")
    third = instructions_fn(None)
    assert "user uses zsh" in third


def test_base_prompt_does_not_get_re_read() -> None:
    """The static prefix is captured once; only the dynamic suffix re-evaluates."""
    cap = make_context_capability(base_prompt="STATIC_BASE")
    fn = cap.get_instructions()
    out1 = fn(None)
    out2 = fn(None)
    assert "STATIC_BASE" in out1
    assert "STATIC_BASE" in out2
