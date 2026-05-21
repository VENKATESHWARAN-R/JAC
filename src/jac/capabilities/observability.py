"""Logfire instrumentation setup for JAC.

We send to Logfire's cloud only when ``LOGFIRE_TOKEN`` is present in the
environment. Otherwise tracing stays local — Logfire still produces structured
spans, you just don't ship them anywhere.

Per-span fields documented in CLAUDE.md (``template``, ``task_id``,
``parent_run_id``, ``token_cost``, ``duration``, ``exit_status``) are attached
in later phases as JAC adds richer flows (sessions, minions, memory writes).
"""

from __future__ import annotations

import logfire

from jac import __version__


def setup_observability() -> None:
    """Configure Logfire once at process startup.

    Idempotent: safe to call multiple times in tests.
    """
    logfire.configure(
        send_to_logfire="if-token-present",
        service_name="jac",
        service_version=__version__,
        # Local-first: the user owns their machine and their logs. Don't
        # silently redact things they might want to see.
        scrubbing=False,
        # console=logfire.ConsoleOptions(include_timestamps=False, span_style="simple")
    )
    logfire.instrument_pydantic_ai()
