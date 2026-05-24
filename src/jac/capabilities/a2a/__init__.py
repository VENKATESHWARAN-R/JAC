"""A2A subsystem — JAC talks to (and accepts calls from) A2A peers (D24, D30).

Phase 4 surface, split across PRs:

- :class:`A2ACapability` — public capability the REPL wires into a
  session. Owns the lifecycle of one optional :class:`A2AServer` AND
  contributes the outbound tools (``a2a_call`` / ``a2a_discover``) to
  Gru's toolset (Phase 4.b).
- :class:`A2AServer` (in :mod:`.server`) — the actual server, with
  bearer auth, on-disk storage, audit logging, and uvicorn lifecycle.
- :func:`build_guest_gru` (in :mod:`.guest`) — the read-only Gru that
  answers inbound calls.
- :mod:`.client` — ``a2a_discover`` + ``a2a_call`` outbound tools, plus
  peer-name → ``(url, token)`` resolution.
- :mod:`.card`, :mod:`.auth`, :mod:`.storage`, :mod:`.audit` — leaf
  helpers (agent-card generation, bearer middleware + token gen,
  on-disk Storage, inbound JSONL + retention cleanup).

The capability deliberately does NOT auto-start a server — the operator
chooses to expose A2A via ``/a2a serve`` or ``jac a2a serve``. Outbound
tools work regardless: a session with no server running can still call
peers via ``a2a_call`` (a JAC instance acting purely as an A2A *client*
is a real use case for cross-repo coworking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from jac.capabilities.a2a.client import build_outbound_tools
from jac.capabilities.a2a.guest import build_guest_gru
from jac.capabilities.a2a.server import A2AServer, ServerInfo
from jac.profiles import A2APeerConfig
from jac.runtime.bus import EventBus
from jac.runtime.events import A2AServerStopped
from jac.tools import jac_function_toolset

__all__ = [
    "A2ACapability",
    "A2AServer",
    "ServerInfo",
    "build_guest_gru",
    "make_a2a_capability",
]


@dataclass
class A2ACapability(AbstractCapability[Any]):
    """A2A subsystem capability — server lifecycle + outbound tools.

    State carried per session:

    - ``bus`` — optional event bus; lifecycle events + outbound call
      events post here so the renderer can paint ``[a2a]`` notifications.
    - ``server`` — the running :class:`A2AServer` (None when stopped).
    - ``model`` + ``profile_name`` — captured at construction so
      ``/a2a serve`` knows which guest Gru to spin up. The REPL passes
      the active model id and profile name in.
    - ``retention_days`` — pulled from the active profile's
      ``a2a.context_retention_days``; the server uses it on start to
      prune expired contexts.
    - ``peers`` — the ``a2a.peers`` block from the active profile;
      ``a2a_call`` resolves named peers against this mapping. Empty
      dict is fine (Gru can still call raw URLs). The REPL refreshes
      this whenever ``/profile`` rebuilds Gru.
    """

    bus: EventBus | None = None
    model: str | Model | None = None
    """Model the guest Gru will run on. ``str`` is the production path
    (a fully-qualified ``provider:id`` string the server resolves via
    pydantic-ai's ``infer_model``). ``Model`` instance is the test path
    (callers pass ``TestModel()`` directly so the test doesn't need a
    real provider configured)."""

    profile_name: str | None = None
    retention_days: int = 3
    peers: dict[str, A2APeerConfig] = field(default_factory=dict)

    server: A2AServer | None = field(default=None, init=False, repr=False)

    def get_toolset(self) -> Any:
        """Expose ``a2a_call`` + ``a2a_discover`` to Gru.

        We build the tools through a closure that captures *this
        capability instance* — specifically the ``_current_peers``
        accessor — rather than the peers dict by value. That way when
        ``/profile`` swaps profiles and updates ``self.peers``, the
        outbound tools immediately see the new map without rebuilding
        the toolset.
        """
        tools = build_outbound_tools(
            peers_getter=self._current_peers,
            bus=self.bus,
        )
        return jac_function_toolset(*tools)

    def _current_peers(self) -> dict[str, A2APeerConfig]:
        """Live accessor for the active peers map (see :meth:`get_toolset`)."""
        return self.peers

    # ---------- public lifecycle ----------

    async def start_server(
        self,
        *,
        host: str,
        port: int,
        unsafe: bool = False,
    ) -> ServerInfo:
        """Boot the guest server. Idempotent guard: raises if already running.

        Args:
            host: bind address (the slash command defaults this to the
                active profile's ``a2a.host``; CLI flag overrides).
            port: bind port (same: profile default + CLI override).
            unsafe: skip bearer auth.

        Returns:
            The :class:`ServerInfo` for the running server (URL, host,
            port, full token, unsafe flag).

        Raises:
            RuntimeError: server is already running. Slash handler turns
                this into a friendly "already running, run /a2a stop first".
            JacConfigError: no model configured (defensive — REPL always
                passes one).
            OSError: bind failed (port in use, perm denied).
        """
        if self.server is not None and self.server.is_running:
            raise RuntimeError("A2A server is already running. Use `/a2a stop` first.")
        if not self.model:
            from jac.errors import JacConfigError

            raise JacConfigError(
                "A2A server cannot start without an active model. "
                "This usually means the capability was built without one — "
                "report this as a bug if you see it from the slash command."
            )

        guest_agent = build_guest_gru(model=self.model)
        server = A2AServer(
            guest_agent=guest_agent,
            bus=self.bus,
            profile_name=self.profile_name,
            retention_days=self.retention_days,
        )
        info = await server.start(host=host, port=port, unsafe=unsafe)
        self.server = server
        return info

    async def stop_server(self, *, reason: str = "user") -> None:
        """Stop the guest server. No-op when not running."""
        if self.server is None:
            return
        await self.server.stop(reason=reason)
        self.server = None

    async def shutdown(self) -> None:
        """REPL teardown hook — stop the server if still running."""
        if self.server is not None and self.server.is_running:
            await self.server.stop(reason="repl-exit")
            self.server = None
            # Emit a final stopped event for any consumer that missed the
            # one stop() itself sends (defensive; stop() already emits).
            if self.bus is not None:
                with _ignore_errors():
                    await self.bus.emit(A2AServerStopped(reason="repl-exit"))


def make_a2a_capability(
    *,
    bus: EventBus | None = None,
    model: str | Model | None = None,
    profile_name: str | None = None,
    retention_days: int = 3,
    peers: dict[str, A2APeerConfig] | None = None,
) -> A2ACapability:
    """Build a fresh :class:`A2ACapability`. One per agent / session.

    Mirrors the ``make_*_capability`` factories used by every other
    JAC capability for consistency.
    """
    return A2ACapability(
        bus=bus,
        model=model,
        profile_name=profile_name,
        retention_days=retention_days,
        peers=peers or {},
    )


# Small helper so the shutdown emit can't bring the REPL down if the
# bus is already torn down. Inline rather than importing contextlib to
# keep the module's import surface minimal.
class _ignore_errors:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return True
