"""Shared pytest hooks for the JAC test suite."""

from __future__ import annotations

import pytest

from jac.runtime.observability import setup_observability


@pytest.fixture(scope="session", autouse=True)
def _logfire_configured() -> None:
    """Match production: sub-agent and tool-summarize spans need a pipeline.

    :func:`setup_observability` is idempotent — safe if a test later calls it
    again. Without this, any test that hits ``logfire.span`` emits
    ``LogfireNotConfiguredWarning`` even though the REPL configures Logfire
    at startup.
    """
    setup_observability()
