"""Shared pytest hooks for the JAC test suite."""

from __future__ import annotations

import pytest

from jac.runtime.observability import setup_observability
from jac.workspace import paths


@pytest.fixture(autouse=True)
def _clear_root_caches() -> None:
    """Clear the cached project-root resolution before every test.

    ``project_root`` / ``find_project_root`` are ``@cache``-d on the (CWD-
    derived) start arg. Tests freely ``chdir`` and monkeypatch these, so a
    value cached by one test must never leak into the next. Clearing here —
    in one autouse place — frees individual fixtures from re-deriving the
    discipline and removes the cross-test pollution landmine.
    """
    paths.project_root.cache_clear()  # type: ignore[attr-defined]
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture(scope="session", autouse=True)
def _logfire_configured() -> None:
    """Match production: sub-agent and tool-summarize spans need a pipeline.

    :func:`setup_observability` is idempotent — safe if a test later calls it
    again. Without this, any test that hits ``logfire.span`` emits
    ``LogfireNotConfiguredWarning`` even though the REPL configures Logfire
    at startup.
    """
    setup_observability()
