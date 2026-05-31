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
  session and broadcasts serialized frames to every connected SSE subscriber.
  It is decoupled from the SSE connection on purpose: approvals must still be
  readable (and the turn must keep progressing) even if the browser reconnects.
  Broadcast (one queue per connection) — not a single shared queue — so a
  dangling generator from a prior page-load can't steal a live connection's
  frames (that was the "first message not shown" bug).
- Each user message runs ``driver.run_turn(stream=False)`` as its own task, so
  the POST returns immediately and tool-events flow over SSE. **stream=False is
  deliberate:** HITL approval is driven by ``agent.run()``'s deferred-tool-call
  handling, which ``run_stream`` bypasses — streaming would let a gated tool run
  with no confirmation. Only one turn runs at a time.

Known v1 limitation (documented in the design doc): if the browser never answers
an approval, the turn blocks on that future. Single-user, local: the operator
answers or starts a new chat.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from typing import Any

from jac.config import reset_settings_cache
from jac.errors import JacConfigError
from jac.profiles_crud import (
    get_default_profile_name,
    get_profile,
    list_profiles,
    resolve_active_profile_name,
)
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.runtime.bootstrap import (
    SessionRuntime,
    build_session_runtime,
    resolve_summarizer_model,
)
from jac.runtime.events import (
    ApprovalRequest,
    ApprovalResponse,
    ClarifyRequest,
    ClarifyResponse,
    SubAgentCompleted,
    SubAgentSpawned,
    ToolCallStarted,
)
from jac.runtime.gru import build_gru
from jac.runtime.session import Session
from jac.runtime.sub_agent import _BIDIRECTIONAL_ROUND_TRIP_CAP, _pending_spawns
from jac.runtime.sub_agent_usage import get_sub_agent_stats
from jac.runtime.tool_summarize import set_summarizer_model
from jac.secrets import (
    apply_ad_hoc_model_env,
    apply_profile_env,
    restore_env,
    snapshot_env,
)
from jac.workspace import paths

# Tool name → the short "action" label shown in the dashboard's files panel.
# These are the file-mutating tools whose ``path`` arg we track (Slice 3); read
# tools are intentionally excluded. Sub-agent edits flow on the same bus, so
# minion-touched files are captured here too.
_MUTATING_FILE_TOOLS = {"write_file": "write", "edit_file": "edit"}

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
        # One queue per live SSE connection (broadcast). A single shared queue
        # would let a dangling generator from a prior page-load steal frames —
        # that's the "first message not shown" bug. Each connection registers
        # its own queue and is discarded on disconnect.
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._pending_approvals: dict[str, asyncio.Future[ApprovalResponse]] = {}
        self._pending_clarify: asyncio.Future[ClarifyResponse] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        # Grace-period failsafe: if every SSE client drops mid-approval and none
        # reconnects, auto-deny the pending HITL so the turn doesn't hang forever.
        self._hitl_failsafe: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._busy = False
        # path -> last action ("write"/"edit") for the dashboard files panel.
        self.files_changed: dict[str, str] = {}
        # spawn_id -> {tier, model, objective} for sub-agents currently running.
        # Sourced from the SubAgentSpawned/SubAgentCompleted bus events because
        # ``_pending_spawns`` only holds *suspended* (bidirectional) workers —
        # a parallel ``spawn_sub_agents`` batch runs to completion without ever
        # parking, so it would otherwise never appear in the dashboard.
        self.active_minions: dict[str, dict[str, Any]] = {}
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
            await self._ensure_started_locked(session_id=session_id)

    async def _ensure_started_locked(self, *, session_id: str | None = None) -> None:
        """Body of :meth:`ensure_started` without the lock (callers already hold it)."""
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
            self._emit({"type": "Error", "error": f"can't start chat — {exc}"})
            return

        if self._consumer is not None:
            self._consumer.cancel()
        self.session = session
        self.runtime = runtime
        self.history = list(session.message_history)
        self._pending_approvals.clear()
        self._pending_clarify = None
        self._busy = False
        self.files_changed.clear()
        self.active_minions.clear()
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

    def _emit(self, frame: dict[str, Any]) -> None:
        """Broadcast a frame to every connected SSE subscriber."""
        for queue in list(self._subscribers):
            queue.put_nowait(frame)

    # ----- the persistent bus consumer -----

    async def _consume(self, runtime: SessionRuntime) -> None:
        async for event in runtime.bus.stream():
            if isinstance(event, ApprovalRequest):
                self._pending_approvals[event.tool_call_id] = event.response_future
            elif isinstance(event, ClarifyRequest):
                self._pending_clarify = event.response_future
            elif isinstance(event, ToolCallStarted):
                action = _MUTATING_FILE_TOOLS.get(event.tool_name)
                if action and isinstance(event.args, dict) and event.args.get("path"):
                    self.files_changed[str(event.args["path"])] = action
            elif isinstance(event, SubAgentSpawned):
                self.active_minions[event.spawn_id] = {
                    "tier": event.tier,
                    "model": event.model,
                    "objective": event.objective,
                }
            elif isinstance(event, SubAgentCompleted):
                self.active_minions.pop(event.spawn_id, None)
            self._emit(event_to_frame(event))

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
        # Echo the user's message immediately so it shows in the transcript.
        self._emit({"type": "UserMessage", "content": text})
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

            # stream=False: HITL approval (ApprovalRequiredToolset +
            # deferred_tool_calls) is driven by agent.run(), NOT run_stream —
            # the streamed path silently bypasses the approval handler, so a
            # gated tool could run with no confirmation. Correctness over
            # token-streaming: the reply lands on RunCompleted. (Streaming WITH
            # approval needs the agent.iter() graph path — a future enhancement.)
            result = await driver.run_turn(text, self.history, stream=False)
            self.history = result.message_history
            try:
                self.session.save(self.history)
            except OSError as exc:
                self._emit({"type": "Notice", "level": "warn", "text": str(exc)})
        finally:
            self._busy = False
            self._emit({"type": "TurnDone"})

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
        self._emit({"type": "SessionStarted", "id": session.session_id})
        return {"ok": True, "id": session.session_id}

    # ----- live model / profile switch (mid-session Gru rebuild) -----

    def _rebuild(self, *, new_model_id: str, new_profile_name: str | None) -> tuple[bool, str]:
        """Rebuild Gru in place for a new model/profile, snapshot/rollback on failure.

        Mirrors the REPL's ``_rebuild_gru``: snapshot every env key either profile
        (or the new model's provider) could touch, apply the new env, rebuild Gru
        against the **same** bus + capabilities, and on a missing-key / bad-profile
        error restore the snapshot and keep the old Gru. The history, bus, consumer,
        and pending approvals are untouched — only the agent swaps.
        """
        rt = self.runtime
        assert rt is not None
        new_profile = None
        if new_profile_name is not None:
            try:
                new_profile = get_profile(new_profile_name)
            except JacConfigError as exc:
                return False, str(exc)

        keys: set[str] = {"JAC_MODEL"}
        if rt.active_profile is not None:
            keys.update(rt.active_profile.env)
            keys.update(rt.active_profile.required_env_keys())
        if new_profile is not None:
            keys.update(new_profile.env)
            keys.update(new_profile.required_env_keys())
        keys.update(get_provider_registry().required_env_for_prefix(provider_prefix(new_model_id)))
        snap = snapshot_env(list(keys))

        try:
            if new_profile_name is None:
                apply_ad_hoc_model_env(new_model_id)
            elif new_profile is not None and new_profile_name != self.profile_name:
                apply_profile_env(new_profile_name, new_profile)
                if new_model_id != new_profile.default_model():
                    os.environ["JAC_MODEL"] = new_model_id
            else:
                apply_ad_hoc_model_env(new_model_id)
            reset_settings_cache()
            resolved = resolve_summarizer_model(
                new_profile_name if new_profile_name is not None else self.profile_name
            )
            # Pass model_override explicitly: get_settings() is cached, so the
            # env change alone wouldn't be seen by build_gru's fallback.
            new_gru = build_gru(
                model_override=new_model_id,
                extra_capabilities=rt.persisted_capabilities,
                bus=rt.bus,
                summarizer_model=resolved,
            )
            set_summarizer_model(resolved)
        except Exception as exc:
            # Any switch failure rolls back to the prior agent: JacConfigError
            # (missing key), pydantic-ai UserError (unknown model), or a provider
            # construction error. Snapshot/restore keeps the running agent intact.
            restore_env(snap)
            reset_settings_cache()
            return False, str(exc)

        target_profile = new_profile if new_profile is not None else rt.active_profile
        target_profile_name = (
            new_profile_name if new_profile_name is not None else self.profile_name
        )

        rt.gru = new_gru
        rt.driver.gru = new_gru
        rt.model_id = new_model_id
        rt.active_profile = target_profile
        self.profile_name = target_profile_name
        default_model = target_profile.default_model() if target_profile is not None else None
        self.model_override = None if new_model_id == default_model else new_model_id

        a2a = rt.a2a_capability
        if a2a is not None:
            a2a.model = new_model_id
            a2a.profile_name = target_profile_name
            if target_profile is not None:
                a2a.retention_days = target_profile.a2a.context_retention_days
                a2a.allow_private_peers = target_profile.a2a.allow_private_peers
                a2a.profile_peers.clear()
                a2a.profile_peers.update(target_profile.a2a.peers)
        return True, new_model_id

    async def switch_model(self, model_id: str) -> dict[str, Any]:
        """Switch the active model (ad-hoc, keeping the current profile)."""
        model_id = (model_id or "").strip()
        if not model_id:
            return {"ok": False, "reason": "empty model id"}
        async with self._start_lock:
            await self._ensure_started_locked()
            if self.runtime is None:
                return {
                    "ok": False,
                    "reason": "no model bound — configure a profile under Profiles",
                }
            if self._busy:
                return {
                    "ok": False,
                    "reason": "a turn is in progress — try again after it finishes",
                }
            ok, msg = self._rebuild(new_model_id=model_id, new_profile_name=self.profile_name)
        if not ok:
            self._emit({"type": "Notice", "level": "warn", "text": f"model switch failed: {msg}"})
            return {"ok": False, "reason": msg}
        self._emit({"type": "Notice", "text": f"switched model → {msg}"})
        self._emit({"type": "ModelSwitched", "model": msg, "profile": self.profile_name})
        return {"ok": True, "model": msg, "profile": self.profile_name}

    async def switch_profile(self, name: str) -> dict[str, Any]:
        """Switch the active profile (binds its active-tier default model)."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "reason": "empty profile name"}
        try:
            profile = get_profile(name)
        except JacConfigError as exc:
            return {"ok": False, "reason": str(exc)}
        async with self._start_lock:
            await self._ensure_started_locked()
            if self.runtime is None:
                return {"ok": False, "reason": "no runtime — configure a profile first"}
            if self._busy:
                return {
                    "ok": False,
                    "reason": "a turn is in progress — try again after it finishes",
                }
            ok, msg = self._rebuild(new_model_id=profile.default_model(), new_profile_name=name)
        if not ok:
            self._emit({"type": "Notice", "level": "warn", "text": f"profile switch failed: {msg}"})
            return {"ok": False, "reason": msg}
        self._emit({"type": "Notice", "text": f"switched profile → {name} ({msg})"})
        self._emit({"type": "ModelSwitched", "model": msg, "profile": name})
        return {"ok": True, "model": msg, "profile": name}

    def switcher_options(self) -> dict[str, Any]:
        """Data for the top-bar profile/model dropdowns (profiles + tier models)."""
        try:
            profile_names = list(list_profiles().keys())
        except JacConfigError:
            profile_names = []
        current_profile = self.profile_name
        if current_profile is None:
            try:
                current_profile = get_default_profile_name()
            except JacConfigError:
                current_profile = None

        profile = self.runtime.active_profile if self.runtime else None
        if profile is None and current_profile:
            try:
                profile = get_profile(current_profile)
            except JacConfigError:
                profile = None

        models: list[dict[str, str]] = []
        if profile is not None:
            for tier, tier_models in profile.tiers.items():
                for model in tier_models:
                    models.append({"tier": tier, "model": model})
        current_model = self.runtime.model_id if self.runtime else None
        if current_model is None and profile is not None:
            current_model = profile.default_model()
        return {
            "profiles": profile_names,
            "current_profile": current_profile,
            "models": models,
            "current_model": current_model,
        }

    # ----- SSE -----

    async def sse_events(self):
        """Async generator: one queue per connection, removed on disconnect.

        Broadcast (not a shared queue) so concurrent / reconnecting EventSource
        connections each get every frame — and a dropped connection can't strand
        frames meant for a live one.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        # A client connected — cancel any pending auto-deny failsafe.
        if self._hitl_failsafe is not None and not self._hitl_failsafe.done():
            self._hitl_failsafe.cancel()
            self._hitl_failsafe = None
        try:
            # Greet this client with the current session id so the header
            # renders even before the first turn.
            if self.session is not None:
                yield {
                    "data": json.dumps({"type": "SessionStarted", "id": self.session.session_id})
                }
            while True:
                frame = await queue.get()
                yield {"data": json.dumps(frame)}
        finally:
            self._subscribers.discard(queue)
            # Last client gone with HITL outstanding → arm the grace-period
            # failsafe. A reconnect (page reload, bfcache restore) cancels it.
            if not self._subscribers and (self._pending_approvals or self._pending_clarify):
                self._arm_hitl_failsafe()

    def _arm_hitl_failsafe(self, delay: float = 30.0) -> None:
        if self._hitl_failsafe is not None and not self._hitl_failsafe.done():
            return
        self._hitl_failsafe = asyncio.create_task(self._hitl_failsafe_after(delay))

    async def _hitl_failsafe_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if not self._subscribers:
            self._fail_pending_hitl()

    def _fail_pending_hitl(self) -> None:
        """Resolve outstanding approvals/clarify as denied so a turn never hangs
        on a closed browser. Only fires after the grace period with no reconnect."""
        for future in list(self._pending_approvals.values()):
            if not future.done():
                future.set_result(
                    ApprovalResponse(
                        approved=False, feedback="(browser disconnected — auto-denied)"
                    )
                )
        self._pending_approvals.clear()
        if self._pending_clarify is not None and not self._pending_clarify.done():
            self._pending_clarify.set_result(
                ClarifyResponse(selected_index=None, selected_text=None, free_text=False)
            )
        self._pending_clarify = None

    def dashboard(self) -> dict[str, Any]:
        """A snapshot for the activity sidebar (Slice 3): tokens, minions, files.

        Polled by the browser. Reads the same process-global registries the CLI
        ``/spawns`` and ``/tokens`` commands do — for the single-user web they
        reflect the one active session ``build_session_runtime`` set up.
        """
        tracker = self.runtime.usage_tracker if self.runtime else None
        tokens = {
            "input": tracker.counters.input_tokens if tracker else 0,
            "output": tracker.counters.output_tokens if tracker else 0,
            "total": tracker.counters.total_tokens if tracker else 0,
            "cache_pct": tracker.counters.cache_hit_pct if tracker else None,
            "project_total": tracker.project_total_tokens if tracker else 0,
            "budget_pct": tracker.status_pct() if tracker else None,
        }
        stats = get_sub_agent_stats()
        # Merge two sources of "active" workers, keyed by spawn_id:
        #   * ``active_minions`` — running workers, from the lifecycle bus events
        #     (covers parallel batches + sequential workers before they park).
        #   * ``_pending_spawns`` — suspended bidirectional workers waiting on a
        #     reply; richer (round-trip + turn counts), so it wins on overlap.
        by_id: dict[str, dict[str, Any]] = {}
        for sid, m in self.active_minions.items():
            by_id[sid] = {
                "spawn_id": sid,
                "tier": m["tier"],
                "model": m["model"],
                "round_trips": 0,
                "cap": _BIDIRECTIONAL_ROUND_TRIP_CAP,
                "turns_used": 0,
                "objective": m["objective"],
                "status": "running",
            }
        for sid, p in _pending_spawns.items():
            by_id[sid] = {
                "spawn_id": sid,
                "tier": p.resolved.resolved,
                "model": p.resolved.model,
                "round_trips": p.round_trips,
                "cap": _BIDIRECTIONAL_ROUND_TRIP_CAP,
                "turns_used": p.turns_used,
                "objective": p.objective,
                "status": "waiting",
            }
        minions = [by_id[sid] for sid in sorted(by_id)]
        return {
            "session_id": self.session.session_id if self.session else None,
            "model": self.runtime.model_id if self.runtime else None,
            "busy": self._busy,
            "scope": "project" if paths.in_project() else "global",
            "tokens": tokens,
            "sub_agents": {
                "spawns": stats.spawns,
                "tokens": stats.total_tokens,
                "by_tier": stats.by_tier,
                "active": minions,
            },
            "files": self._files_on_disk(),
        }

    def history_messages(self) -> list[dict[str, Any]]:
        """Serialize the attached session's history into transcript items.

        Used to repaint past messages when an old session is opened in the
        browser. Best-effort and display-only: each user prompt becomes a user
        bubble, each assistant ``TextPart`` an assistant bubble, each tool call
        a completed tool chip; system prompts, tool returns, thinking, and
        retries are skipped (they'd just be noise in the scroll-back). Mirrors
        the shape the live event frames produce so a reopened chat reads the
        same as a live one.
        """
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            ToolCallPart,
            UserPromptPart,
        )

        def _text(content: Any) -> str:
            if isinstance(content, str):
                return content
            if isinstance(content, (list, tuple)):
                return " ".join(c for c in content if isinstance(c, str))
            return ""

        out: list[dict[str, Any]] = []
        for msg in self.history:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, UserPromptPart):
                        text = _text(part.content).strip()
                        if text:
                            out.append({"role": "user", "content": text})
            elif isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        if part.content and part.content.strip():
                            out.append({"role": "assistant", "content": part.content})
                    elif isinstance(part, ToolCallPart):
                        reason = ""
                        try:
                            args = part.args_as_dict()
                            reason = str(args.get("reason", "")) if isinstance(args, dict) else ""
                        except (ValueError, TypeError):
                            reason = ""
                        out.append({"role": "tool", "name": part.tool_name, "reason": reason})
        return out

    def environment(self) -> dict[str, Any]:
        """Static-per-session view of the connected environment.

        A2A outbound peers, MCP servers, and loaded skills — read straight off
        the live capabilities the session was built with (the same objects the
        CLI's ``/a2a peers``, ``/mcp list`` and ``/skills`` render). Fetched
        *once* by the browser (not polled like :meth:`dashboard`) because it
        only changes on a session swap or a config reload.
        """
        rt = self.runtime
        if rt is None:
            return {"a2a": [], "mcp": [], "skills": []}

        a2a: list[dict[str, Any]] = []
        a2a_cap = rt.a2a_capability
        if a2a_cap is not None:
            session_names = set(getattr(a2a_cap, "session_peers", {}) or {})
            for name, peer in sorted(a2a_cap.peers.items()):
                a2a.append(
                    {
                        "name": name,
                        "url": peer.url,
                        "auth": type(peer.auth).__name__.replace("Auth", "").lower()
                        if peer.auth
                        else "none",
                        "source": "session" if name in session_names else "profile",
                    }
                )

        mcp: list[dict[str, Any]] = []
        mcp_cap = rt.mcp_capability
        if mcp_cap is not None:
            for name, srv in sorted(mcp_cap.catalog.servers.items()):
                mcp.append(
                    {
                        "name": name,
                        "transport": srv.transport,
                        "enabled": srv.knobs.enabled,
                        "approval": srv.knobs.requires_approval,
                        "source": srv.source,
                    }
                )

        skills: list[dict[str, Any]] = []
        sk_cap = rt.skills_capability
        if sk_cap is not None:
            for _name, sk in sorted(sk_cap.skills.items()):
                skills.append({"name": sk.name, "description": sk.description, "source": sk.source})

        return {"a2a": a2a, "mcp": mcp, "skills": skills}

    def _files_on_disk(self) -> list[dict[str, str]]:
        """Changed files that actually exist now.

        ``before_tool_execute`` (which records the path) also fires for the
        *deferred* call before approval, so a denied write would otherwise leave
        a phantom entry. Filtering to files that exist on disk keeps the panel
        honest — a denied/failed write never created a file, so it drops out.
        """
        out: list[dict[str, str]] = []
        for path, action in sorted(self.files_changed.items()):
            try:
                exists = paths.resolve_under_project(path).exists()
            except OSError:
                exists = True  # can't tell — don't hide it
            if exists:
                out.append({"path": path, "action": action})
        return out


_MANAGER: WebChatManager | None = None


def get_manager() -> WebChatManager:
    """Return the process-wide single-user chat manager (lazy, loop-bound)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = WebChatManager()
    return _MANAGER
