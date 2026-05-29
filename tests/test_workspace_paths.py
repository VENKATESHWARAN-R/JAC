"""Tests for project-root resolution and the loose-mode state fallback.

The contract:

- A directory is a project if it has ``.git`` **or** ``.agents/``.
- ``project_root`` returns the nearest such ancestor, or ``None`` when loose.
- ``find_project_root`` (working root for tools) falls back to CWD when loose.
- ``project_state_root`` (where JAC writes sessions/usage/cache/a2a) is the
  project's ``.agents`` in a project, but the **user workspace** when loose —
  so we never scatter ``.agents/`` into unrelated folders.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jac.workspace import paths


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run each test from a clean tmp dir (cache clearing is autouse in conftest)."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# ---------- project_root detection ----------


def test_project_root_detects_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert paths.project_root() == tmp_path
    assert paths.in_project() is True


def test_project_root_detects_agents_dir(tmp_path: Path) -> None:
    """A bare .agents/ (no .git) is the explicit opt-in for non-git folders."""
    (tmp_path / ".agents").mkdir()
    assert paths.project_root() == tmp_path
    assert paths.in_project() is True


def test_project_root_walks_up_to_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert paths.project_root() == tmp_path


def test_project_root_none_when_loose(tmp_path: Path) -> None:
    assert paths.project_root() is None
    assert paths.in_project() is False


# ---------- find_project_root (working root) ----------


def test_find_project_root_returns_project_when_present(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert paths.find_project_root() == tmp_path


def test_find_project_root_falls_back_to_cwd_when_loose(tmp_path: Path) -> None:
    """Tools still operate where the user is, even with no project."""
    assert paths.find_project_root() == tmp_path.resolve()


# ---------- project_state_root (where JAC writes) ----------


def test_state_root_is_dot_agents_in_a_project(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert paths.project_state_root() == tmp_path / ".agents"
    assert paths.project_sessions_dir() == tmp_path / ".agents" / "sessions"
    assert paths.project_usage_file() == tmp_path / ".agents" / "usage.jsonl"


def test_state_root_is_user_workspace_when_loose(tmp_path: Path) -> None:
    """The core fix: loose folders persist under ~/.jac, not <cwd>/.agents."""
    assert paths.project_state_root() == paths.USER_WORKSPACE
    assert paths.project_sessions_dir() == paths.USER_WORKSPACE / "sessions"
    # And crucially, nothing resolves under the random CWD.
    assert tmp_path not in paths.project_sessions_dir().parents


def test_overlay_workspace_tracks_working_root(tmp_path: Path) -> None:
    """Overlay resources (config/memory/prompts) stay keyed to the working
    root — in a project that's .agents; loose it's <cwd>/.agents (which is
    simply absent and skipped, never created)."""
    (tmp_path / ".git").mkdir()
    assert paths.project_workspace() == tmp_path / ".agents"
    assert paths.project_memory_file() == tmp_path / ".agents" / "memory.md"


# ---------- init_project_workspace (the opt-in) ----------


def test_init_project_workspace_makes_loose_folder_a_project(tmp_path: Path) -> None:
    from jac.workspace.bootstrap import init_project_workspace

    assert paths.in_project() is False  # loose to start
    created = init_project_workspace(tmp_path)
    assert created == tmp_path / ".agents"
    assert created.is_dir()
    # Immediately recognised as a project (cache was cleared), and state now
    # anchors here rather than the global workspace.
    assert paths.in_project() is True
    assert paths.project_state_root() == tmp_path / ".agents"


def test_init_project_workspace_is_idempotent(tmp_path: Path) -> None:
    from jac.workspace.bootstrap import init_project_workspace

    first = init_project_workspace(tmp_path)
    second = init_project_workspace(tmp_path)
    assert first == second
    assert second.is_dir()
