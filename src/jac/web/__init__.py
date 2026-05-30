"""JAC web surface — a local-first, single-user browser UI (D48).

A third surface alongside the CLI REPL and the A2A server. It is a *renderer +
management API* over the same engine the CLI uses (``jac.sdk``) — it adds no new
runtime mode. Slice 1 (shipped) is a control panel for profiles, providers,
secrets, and sessions; chat + HITL streaming follow in later slices.

**Local-first, single-user by charter.** Binds ``127.0.0.1`` by default; the
loopback boundary is the security model. It is not a multi-tenant service — see
[`docs/design/web-surface.md`](../../../docs/design/web-surface.md).

``create_app`` is the embeddable Starlette factory; ``app`` is the Typer command
group wired into the CLI as ``jac web``. The factory is imported lazily (via
module ``__getattr__``) so merely importing this package for the Typer command
doesn't pull Starlette + Jinja2 into every ``jac`` invocation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jac.web.app import app

if TYPE_CHECKING:
    from jac.web.server import create_app

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from jac.web.server import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
