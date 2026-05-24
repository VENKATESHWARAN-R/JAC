"""A2A guest server orchestration (D24).

Wraps :func:`fasta2a.pydantic_ai.agent_to_a2a` (which produces a
Starlette ASGI app), bolts on our bearer-auth middleware, swaps
fasta2a's defaults for our on-disk storage + audit log, and runs the
result on a uvicorn server in a background asyncio task.

The class is intentionally small — most of the heavy lifting is in
fasta2a (worker, JSON-RPC dispatch, agent-card endpoint) and uvicorn
(the actual HTTP server). This module's job is *lifecycle*:

- :meth:`A2AServer.start` — build the Starlette app, instantiate
  uvicorn, kick off ``serve()`` as a background task, wait for the
  server to actually bind (so callers know the port is live before we
  return), emit :class:`A2AServerStarted`, and run the retention
  cleanup pass.
- :meth:`A2AServer.stop` — flip ``should_exit`` on the uvicorn server,
  await the task, emit :class:`A2AServerStopped`. Best-effort; never
  raises.

The audit-log emission for inbound calls happens in
:meth:`AuditingAgentWorker.run_task` (subclassed from fasta2a's
``AgentWorker``) — that's where we get the message + final state
+ duration in one place.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import uvicorn
from fasta2a.broker import Broker, InMemoryBroker
from fasta2a.pydantic_ai import AgentWorker, agent_to_a2a
from fasta2a.schema import AgentCard, Message, TaskSendParams
from pydantic_ai import Agent

from jac.capabilities.a2a.audit import (
    InboundLog,
    InboundRecord,
    cleanup_old_contexts,
    make_message_preview,
    now_iso,
)
from jac.capabilities.a2a.auth import (
    BearerAuthMiddleware,
    generate_token,
    peer_id_from_token,
    redact_token,
)
from jac.capabilities.a2a.card import build_agent_card
from jac.capabilities.a2a.storage import JacFileStorage
from jac.errors import JacConfigError
from jac.runtime.events import (
    A2AInboundCall,
    A2AInboundCompleted,
    A2AServerStarted,
    A2AServerStopped,
    EventBus,
    JacEventT,
)
from jac.workspace import paths

# uvicorn defaults to INFO which prints every connection — way too noisy
# for an in-REPL background server. Silence by default; users debugging
# can raise it via JAC_A2A_LOG_LEVEL or by editing this file directly.
_UVICORN_LOG_LEVEL = "warning"

# How long start() waits for uvicorn to bind before giving up. The serve
# loop is fast — a second is generous and avoids hanging the REPL when
# the port's already taken.
_BIND_WAIT_TIMEOUT_S = 3.0
_BIND_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class ServerInfo:
    """Public-facing handle for a running A2A server.

    Returned by :meth:`A2AServer.start` and surfaced via ``/a2a status``.
    Includes the *full* token (not just the redacted form) so the slash
    command can re-print it on demand for an operator who scrolled past
    the startup banner.
    """

    url: str
    bind_host: str
    port: int
    token: str
    unsafe: bool


class AuditingAgentWorker(AgentWorker):
    """fasta2a ``AgentWorker`` + per-call audit logging + bus events.

    Subclasses ``AgentWorker`` so the message ↔ ModelMessage mapping
    and the storage round-tripping all stay upstream (no copying
    fragile glue code into JAC). Overrides only ``run_task`` to wrap
    the existing implementation with:

    - emit :class:`A2AInboundCall` before
    - emit :class:`A2AInboundCompleted` + write an :class:`InboundRecord`
      after, regardless of success/failure
    """

    def __init__(
        self,
        *,
        agent: Agent,
        broker: Broker,
        storage: JacFileStorage,
        bus: EventBus | None,
        inbound_log: InboundLog,
        get_peer_id: Callable[[], str],
    ) -> None:
        super().__init__(agent=agent, broker=broker, storage=storage)
        self._bus = bus
        self._inbound_log = inbound_log
        self._get_peer_id = get_peer_id

    async def run_task(self, params: TaskSendParams) -> None:
        message_text = _extract_text(params.get("message", {}))
        preview = make_message_preview(message_text)
        peer_id = self._get_peer_id()
        context_id = params["context_id"]
        task_id = params["id"]

        await self._emit(
            A2AInboundCall(
                peer_id=peer_id,
                context_id=context_id,
                task_id=task_id,
                message_preview=preview,
            )
        )

        started_at = time.monotonic()
        state = "completed"
        try:
            await super().run_task(params)
        except Exception as exc:  # pragma: no cover - parity with parent
            state = "failed"
            logging.getLogger("jac.a2a").exception("guest run_task failed: %s", exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            tokens_used = 0  # PR4 wires real usage accounting; PR1 logs zero
            await self._emit(
                A2AInboundCompleted(
                    peer_id=peer_id,
                    context_id=context_id,
                    task_id=task_id,
                    state=state,
                    duration_ms=duration_ms,
                    tokens_used=tokens_used,
                )
            )
            self._inbound_log.append(
                InboundRecord(
                    ts=now_iso(),
                    peer_id=peer_id,
                    context_id=context_id,
                    task_id=task_id,
                    state=state,
                    duration_ms=duration_ms,
                    tokens_used=tokens_used,
                    message_preview=preview,
                )
            )

    async def _emit(self, event: JacEventT) -> None:
        if self._bus is not None:
            await self._bus.emit(event)


class A2AServer:
    """Background uvicorn server hosting one guest Gru.

    Lifecycle is exactly two methods (``start`` / ``stop``); each is
    idempotent (calling ``start`` on a running server raises rather
    than silently no-op'ing — that's a bug in the caller, not us).
    """

    def __init__(
        self,
        *,
        guest_agent: Agent,
        bus: EventBus | None = None,
        profile_name: str | None = None,
        retention_days: int = 3,
    ) -> None:
        self._guest_agent = guest_agent
        self._bus = bus
        self._profile_name = profile_name
        self._retention_days = retention_days

        self._uvicorn_server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._info: ServerInfo | None = None
        # Peer identity for the *current* inbound request. fasta2a's
        # worker doesn't expose the originating HTTP request to the
        # worker, so we stash the per-request token in an asyncio
        # contextvar via a middleware (set up in _build_app).
        self._current_token: str | None = None

    @property
    def info(self) -> ServerInfo | None:
        """Public ``ServerInfo`` while running; ``None`` when stopped."""
        return self._info

    @property
    def is_running(self) -> bool:
        return self._info is not None

    async def start(
        self,
        *,
        host: str,
        port: int,
        unsafe: bool = False,
        token: str | None = None,
    ) -> ServerInfo:
        """Start the server and return its public handle.

        Args:
            host: bind address (``127.0.0.1`` default per D24; pass
                ``0.0.0.0`` to expose on LAN).
            port: bind port (D24 default ``8001``).
            unsafe: skip bearer auth entirely. Card omits
                ``securitySchemes`` so peers know.
            token: optional pre-generated bearer (lets tests pin a
                deterministic value). When ``None`` we generate a fresh
                URL-safe token via :func:`generate_token`. Ignored when
                ``unsafe=True``.

        Raises:
            RuntimeError: if the server is already running.
            OSError: if the bind fails (port in use, permission denied).
        """
        if self.is_running:
            raise RuntimeError("A2A server is already running; call stop() first.")

        effective_token = token or generate_token() if not unsafe else ""
        base_url = f"http://{host}:{port}"
        card = build_agent_card(
            profile_name=self._profile_name,
            base_url=base_url,
            unsafe=unsafe,
        )

        app = self._build_app(card=card, token=effective_token, unsafe=unsafe)

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=_UVICORN_LOG_LEVEL,
            # workers/reload/lifespan are uvicorn defaults; we want the
            # in-process default (lifespan='auto') so fasta2a's startup
            # hook runs (it brings the task_manager + worker up).
        )
        server = uvicorn.Server(config)
        # serve() runs forever until should_exit goes True. We catch
        # bind errors (OSError) by polling started below.
        serve_task = asyncio.create_task(server.serve(), name="jac.a2a.uvicorn")

        # Wait for uvicorn to flip its ``started`` flag (it does so after
        # the socket is bound and the lifespan startup completed). On
        # failure (port in use) the task itself errors out — we propagate.
        try:
            await _wait_for_started(server, serve_task)
        except Exception:
            # Make sure the task is cleaned up before propagating.
            server.should_exit = True
            with contextlib.suppress(Exception):
                await serve_task
            raise

        info = ServerInfo(
            url=base_url,
            bind_host=host,
            port=port,
            token=effective_token,
            unsafe=unsafe,
        )
        self._uvicorn_server = server
        self._serve_task = serve_task
        self._info = info

        # Run retention cleanup now that we know the server's up. Failures
        # here are best-effort (cleanup_old_contexts swallows OSError).
        removed = cleanup_old_contexts(paths.project_a2a_contexts_dir(), self._retention_days)
        if removed:
            logging.getLogger("jac.a2a").info("pruned %d expired A2A context file(s)", removed)

        await self._emit(
            A2AServerStarted(
                url=base_url,
                token_redacted=redact_token(effective_token) if effective_token else "(unsafe)",
                unsafe=unsafe,
                bind_host=host,
            )
        )
        return info

    async def stop(self, *, reason: str = "user") -> None:
        """Stop the server. Idempotent; no-op when already stopped."""
        server = self._uvicorn_server
        task = self._serve_task
        if server is None or task is None:
            return

        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5.0)

        self._uvicorn_server = None
        self._serve_task = None
        self._info = None
        await self._emit(A2AServerStopped(reason=reason))

    # ---------- internal helpers ----------

    def _build_app(self, *, card: AgentCard, token: str, unsafe: bool):
        """Wire fasta2a + storage + auth + audit into a single Starlette app.

        Note on the agent-card route: fasta2a 0.6.1 builds its own card
        from the constructor args (name/url/version/description/skills)
        and serves it at ``/.well-known/agent-card.json`` — it has no
        way to declare ``securitySchemes`` or ``security``. To get the
        bearer auth advertised in the card (a spec requirement so peers
        know to send the header), we register our OWN card handler in
        the ``routes=`` arg, which goes into Starlette's router BEFORE
        fasta2a adds its handler — first-match-wins semantics mean
        ours runs.
        """
        from fasta2a.schema import agent_card_ta
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.routing import Route

        contexts_dir = paths.project_a2a_contexts_dir()
        storage = JacFileStorage(contexts_dir=contexts_dir)
        broker = InMemoryBroker()
        inbound_log = InboundLog(paths.project_a2a_inbound_log())

        # Pre-serialize the card once at startup; same memoization shape
        # fasta2a uses for its own card.
        card_bytes = agent_card_ta.dump_json(card, by_alias=True)

        async def _card_endpoint(request: Request) -> Response:
            return Response(content=card_bytes, media_type="application/json")

        custom_routes = [
            Route(
                "/.well-known/agent-card.json",
                _card_endpoint,
                methods=["GET", "HEAD", "OPTIONS"],
            )
        ]

        # The custom worker needs to know what bearer token came in on
        # the current request, but fasta2a's worker doesn't see the HTTP
        # request. We carry it via a per-request capture middleware that
        # stashes the token on this A2AServer instance before the JSON-RPC
        # handler runs. Single inbound request at a time (InMemoryBroker
        # serializes via its memory stream) so we don't need contextvars.
        def _get_peer_id() -> str:
            return peer_id_from_token(self._current_token)

        worker = AuditingAgentWorker(
            agent=self._guest_agent,
            broker=broker,
            storage=storage,
            bus=self._bus,
            inbound_log=inbound_log,
            get_peer_id=_get_peer_id,
        )

        # Lifespan: run task_manager + agent context + worker, mirroring
        # fasta2a.pydantic_ai's default worker_lifespan but using OUR
        # worker instance.
        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with app.task_manager, self._guest_agent, worker.run():
                yield

        # Middleware order: auth FIRST (reject early), then token-capture
        # (only runs when auth passed). Starlette runs middleware in the
        # order added → reverse for inbound, so the LAST .add'd runs FIRST
        # inbound — we'll use the FastA2A.user_middleware shape via the
        # constructor's middleware= arg.
        middleware = self._build_middleware(token=token, unsafe=unsafe)

        app = agent_to_a2a(
            self._guest_agent,
            storage=storage,
            broker=broker,
            name=card["name"],
            url=card["url"],
            version=card["version"],
            description=card.get("description"),
            skills=list(card.get("skills", [])),
            middleware=middleware,
            lifespan=lifespan,
            routes=custom_routes,
        )
        return app

    def _build_middleware(self, *, token: str, unsafe: bool) -> list:
        """Build the Starlette middleware stack for the server.

        Auth middleware runs FIRST (rejecting bad bearers before they
        hit the JSON-RPC handler). The token-capture middleware runs
        second, populating ``self._current_token`` so the auditing
        worker can tag inbound calls with the correct peer id.
        """
        from starlette.middleware import Middleware

        stack: list = []
        if not unsafe:
            stack.append(Middleware(BearerAuthMiddleware, expected_token=token))
        # Token-capture middleware as a closure-based BaseHTTPMiddleware.
        captured = _TokenCaptureMiddleware
        stack.append(Middleware(captured, server=self))
        return stack

    async def _emit(self, event: JacEventT) -> None:
        if self._bus is not None:
            await self._bus.emit(event)


def _extract_text(message: Message) -> str:
    """Pull the user-visible text out of an A2A ``Message``.

    The A2A schema allows multiple parts (text + file + data). Most
    inbound calls today are pure text; we concatenate text parts and
    drop binary/data parts from the preview (those still flow to the
    agent via the worker — this is just for the audit log + event
    rendering).
    """
    parts = message.get("parts", []) if isinstance(message, dict) else []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("kind") == "text":
            chunks.append(str(part.get("text", "")))
    return " ".join(chunks)


async def _wait_for_started(server: uvicorn.Server, serve_task: asyncio.Task[None]) -> None:
    """Poll ``server.started`` until True or the serve task dies / we time out."""
    deadline = asyncio.get_running_loop().time() + _BIND_WAIT_TIMEOUT_S
    while not server.started:
        if serve_task.done():
            # Surface the underlying bind error.
            exc = serve_task.exception()
            if exc is not None:
                raise exc
            raise JacConfigError("uvicorn serve task exited before binding — check log output.")
        if asyncio.get_running_loop().time() > deadline:
            raise JacConfigError(
                f"A2A server failed to bind within {_BIND_WAIT_TIMEOUT_S}s — "
                "port may be in use or middleware misconfigured."
            )
        await asyncio.sleep(_BIND_POLL_INTERVAL_S)


# ---------- middleware: capture the bearer for the auditing worker ----------


class _TokenCaptureMiddleware:
    """ASGI middleware that stashes the inbound bearer on the server.

    Runs *after* :class:`BearerAuthMiddleware` (so by the time we see
    the request the token has already been validated). We pull it back
    out of the ``Authorization`` header rather than re-validating —
    cheaper, and the request can't have gotten this far with a bad
    bearer.

    Implemented as a raw ASGI app (not Starlette's
    ``BaseHTTPMiddleware``) because ``BaseHTTPMiddleware`` consumes the
    request body in a way that breaks fasta2a's JSON parsing path.
    Raw ASGI is fine — we never touch the body, just read a header.
    """

    def __init__(self, app, server: A2AServer) -> None:
        self._app = app
        self._server = server

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth_bytes = headers.get(b"authorization", b"")
        token: str | None = None
        if auth_bytes:
            scheme, _, value = auth_bytes.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and value:
                token = value
        # Best-effort single-call assignment; concurrency note: see the
        # InMemoryBroker comment in A2AServer._build_app — broker
        # serializes execution so the auditing worker reads a consistent
        # value for the in-flight call.
        self._server._current_token = token
        try:
            await self._app(scope, receive, send)
        finally:
            self._server._current_token = None
