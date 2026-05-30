"""Web chat engine — Slice 2 of the web surface (D48).

Drives one live JAC session for the browser, reusing the exact same engine the
CLI does (:func:`jac.runtime.bootstrap.build_session_runtime`). This module is
the web's *renderer half*: where the CLI builds a ``CliRenderer`` that prints to
a terminal, here a persistent consumer drains the same ``EventBus`` into a queue
of JSON frames that an SSE endpoint streams to the browser, and HITL approval /
clarify futures are resolved by browser POSTs instead of terminal prompts.

Concurrency model (single-user by charter):

- **One** :class:`WebChatManager` per process (``get_manager()``), bound to
  uvicorn's event loop on first use.
- A **persistent consumer task** reads ``bus.stream()`` for the life of the
  session and pushes serialized frames onto ``out_queue``. It is decoupled from
  the SSE connection on purpose: approvals must still be readable (and the turn
  must keep progressing) even if the browser tab momentarily reconnects.
- Each user message runs ``driver.run_turn(stream=True)`` as its own task, so
  the POST returns immediately and tokens/tool-events flow over SSE. Only one
  turn runs at a time.

Known v1 limitation (documented in the design doc): if the browser never answers
an approval, the turn blocks on that future. Single-user, local: the operator
answers or starts a new chat.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

from jac.errors import JacConfigError
from jac.profiles_crud import get_profile, resolve_active_profile_name
from jac.runtime.bootstrap import SessionRuntime, build_session_runtime
from jac.runtime.events import (
    ApprovalRequest,
    ApprovalResponse,
    ClarifyRequest,
    ClarifyResponse,
)
from jac.runtime.session import Session
from jac.secrets import apply_profile_env
from jac.workspace import paths

# ---------- event serialization ----------


def _jsonable(value: Any) -> Any:
    """Recursively coerce an event field into something ``json.dumps`` accepts.

    Nested frozen dataclasses (e.g. ``PlanStepView``) become dicts; the
    non-serializable ``response_future`` is always dropped by the caller. Any
    value we don't recognise degrades to ``str(value)`` rather than failing the
    frame — a chat frame is best-effort display, never load-bearing data.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _jsonable(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if f.name != "response_future"
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def event_to_frame(event: Any) -> dict[str, Any]:
    """Serialize a JacEvent dataclass into a JSON-ready frame.

    ``{"type": ClassName, <field>: <jsonable>, ...}`` — the browser dispatches
    on ``type``. The approval/clarify ``response_future`` is never serialized.
    """
    frame: dict[str, Any] = {"type": type(event).__name__}
    for field in dataclasses.fields(event):
        if field.name == "response_future":
            continue
        frame[field.name] = _jsonable(getattr(event, field.name))
    return frame


# ---------- the manager ----------


class WebChatManager:
    """Owns one live chat session and the bus→SSE plumbing for it."""

    def __init__(self) -> None:
        self.runtime: SessionRuntime | None = None
        self.session: Session | None = None
        self.history: list[Any] = []
        self.out_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending_approvals: dict[str, asyncio.Future[ApprovalResponse]] = {}
        self._pending_clarify: asyncio.Future[ClarifyResponse] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._busy = False
        # Surface config the bootstrap resolved (e.g. the active profile name),
        # so a future settings change can be reflected; for now informational.
        self.model_override: str | None = None
        self.profile_name: str | None = None

    # ----- lifecycle -----

    async def ensure_started(self, *, session_id: str | None = None) -> None:
        """Build the engine + start the consumer if not already running.

        ``session_id`` resumes that session; otherwise the latest session is
        resumed when one exists, else a fresh one is started. Idempotent under
        the lock so a racing SSE-connect + send don't double-build.
        """
        async with self._start_lock:
            if self.runtime is not None and session_id is None:
                return
            if (
                self.runtime is not None
                and self.session is not None
                and session_id == self.session.session_id
            ):
                return
            await self._start(session_id=session_id)

    def _activate_profile(self) -> None:
        """Resolve + apply the default profile's env so a model is bound.

        ``jac web serve`` (unlike the REPL) doesn't activate a profile at boot —
        the panel must work on a fresh workspace. So the chat resolves the
        default profile lazily here and sets ``JAC_MODEL`` + secrets in
        ``os.environ`` before building Gru. Raises :class:`JacConfigError` (no
        default profile / missing key) — the caller turns that into a friendly
        error frame rather than a 500.
        """
        if self.profile_name is not None:
            return
        name = resolve_active_profile_name(None)
        profile = get_profile(name)
        apply_profile_env(name, profile)
        self.profile_name = name

    async def _attach(
        self, session: Session, *, restored_plan: list[dict[str, str]] | None
    ) -> None:
        """Build + install the engine for ``session``. Errors surface as a frame."""
        try:
            self._activate_profile()
            runtime = build_session_runtime(
                session,
                model_override=self.model_override,
                profile_name=self.profile_name,
                restored_plan=restored_plan,
            )
        except JacConfigError as exc:
            self.session = session
            self.runtime = None
            await self.out_queue.put(
                {
                    "type": "Error",
                    "error": f"can't start chat — {exc}",
                }
            )
            return

        if self._consumer is not None:
            self._consumer.cancel()
        self.session = session
        self.runtime = runtime
        self.history = list(session.message_history)
        self._pending_approvals.clear()
        self._pending_clarify = None
        self._busy = False
        self._consumer = asyncio.create_task(self._consume(runtime), name="jac.web.chat.consumer")

    async def _start(self, *, session_id: str | None) -> None:
        # Resolve which session to attach.
        if session_id is not None:
            session = Session.resume(session_id)
        elif (latest := Session.latest_id()) is not None:
            session = Session.resume(latest)
        else:
            session = Session.new()
        restored_plan, _warning = session.load_plan()
        await self._attach(session, restored_plan=restored_plan)

    # ----- the persistent bus consumer -----

    async def _consume(self, runtime: SessionRuntime) -> None:
        async for event in runtime.bus.stream():
            if isinstance(event, ApprovalRequest):
                self._pending_approvals[event.tool_call_id] = event.response_future
            elif isinstance(event, ClarifyRequest):
                self._pending_clarify = event.response_future
            await self.out_queue.put(event_to_frame(event))

    # ----- turns -----

    async def send(self, text: str) -> dict[str, Any]:
        """Kick off a turn. Returns an ack; events/results flow over SSE."""
        await self.ensure_started()
        if self.runtime is None or self.session is None:
            return {
                "ok": False,
                "reason": "no model is bound — configure a default profile under Profiles/Keys",
            }
        if self._busy:
            return {"ok": False, "reason": "a turn is already in progress"}
        text = text.strip()
        if not text:
            return {"ok": False, "reason": "empty message"}

        self._busy = True
        # Mirror the browser bubble immediately so the user sees their message.
        await self.out_queue.put({"type": "UserMessage", "content": text})
        self._turn_task = asyncio.create_task(self._run_turn(text), name="jac.web.chat.turn")
        return {"ok": True}

    async def _run_turn(self, text: str) -> None:
        assert self.runtime is not None and self.session is not None
        driver = self.runtime.driver
        try:
            # Pre-flight budget/context guards — mirror the REPL. On refusal the
            # driver emits the relevant event (rendered via the consumer); we
            # just stop the turn.
            if (await driver.check_token_budget()) is not None:
                return
            if (await driver.check_context_budget(self.history, text)) is not None:
                return

            result = await driver.run_turn(text, self.history, stream=True)
            self.history = result.message_history
            try:
                self.session.save(self.history)
            except OSError as exc:
                await self.out_queue.put({"type": "Notice", "level": "warn", "text": str(exc)})
        finally:
            self._busy = False
            await self.out_queue.put({"type": "TurnDone"})

    # ----- HITL resolution from the browser -----

    def resolve_approval(self, tool_call_id: str, approved: bool, feedback: str | None) -> bool:
        future = self._pending_approvals.pop(tool_call_id, None)
        if future is None or future.done():
            return False
        future.set_result(ApprovalResponse(approved=approved, feedback=feedback or None))
        return True

    def resolve_clarify(
        self, *, selected_index: int | None, selected_text: str | None, free_text: bool
    ) -> bool:
        future = self._pending_clarify
        if future is None or future.done():
            return False
        self._pending_clarify = None
        future.set_result(
            ClarifyResponse(
                selected_index=selected_index,
                selected_text=selected_text,
                free_text=free_text,
            )
        )
        return True

    async def new_session(self) -> dict[str, Any]:
        """Start a fresh session, replacing the active one."""
        session = Session.new()
        async with self._start_lock:
            await self._attach(session, restored_plan=None)
        if self.runtime is None:
            return {"ok": False, "reason": "no model is bound"}
        await self.out_queue.put({"type": "SessionStarted", "id": session.session_id})
        return {"ok": True, "id": session.session_id}

    # ----- SSE -----

    async def sse_events(self):
        """Async generator yielding SSE frames from ``out_queue`` forever."""
        # Greet the freshly-connected client with the current session id so the
        # header can render even before the first turn.
        if self.session is not None:
            yield {"data": json.dumps({"type": "SessionStarted", "id": self.session.session_id})}
        while True:
            frame = await self.out_queue.get()
            yield {"data": json.dumps(frame)}

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session.session_id if self.session else None,
            "model": self.runtime.model_id if self.runtime else None,
            "busy": self._busy,
            "scope": "project" if paths.in_project() else "global",
        }


_MANAGER: WebChatManager | None = None


def get_manager() -> WebChatManager:
    """Return the process-wide single-user chat manager (lazy, loop-bound)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = WebChatManager()
    return _MANAGER
