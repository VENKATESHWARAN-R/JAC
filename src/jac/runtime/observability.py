"""Logfire instrumentation setup for JAC.

We configure Logfire once at process startup so spans have a destination
(local stdout if ``LOGFIRE_TOKEN`` is absent, the cloud if it's present).
The per-agent OTel spans themselves come from Pydantic AI's
:class:`pydantic_ai.capabilities.Instrumentation` capability — wired into
the default capability set in :mod:`jac.runtime.gru` (and the A2A guest
builder). That's the architecture-mandated D8 pattern; this module only
sets up the global Logfire pipeline.

Per-span fields documented in CLAUDE.md (``template``, ``task_id``,
``parent_run_id``, ``token_cost``, ``duration``, ``exit_status``) are
attached by other capabilities via
``opentelemetry.trace.get_current_span().set_attribute(...)`` inside the
spans the Instrumentation capability opens.
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
        console=False,
    )
