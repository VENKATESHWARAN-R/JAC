"""Background process capability — start / tail / kill / list.

Plain ``run_shell`` is synchronous with a 30-second timeout, which is
unworkable for dev servers, watch tests, or anything else long-running.
This capability lets Gru kick off a process in the background, peek at its
output, list active processes, and kill them when no longer needed.

Design notes:

- **State on the capability instance.** One ``ProcessStore`` per session
  (just like :class:`jac.capabilities.plan.PlanCapability`). Minions do
  not get this capability — long-running processes outlive a minion task
  and the bookkeeping would be confusing.
- **Process output is buffered, not streamed.** Each process gets a
  bounded :class:`collections.deque` (2000 lines). Streaming output onto
  the bus would flood the renderer and the agent's context. Gru pulls
  with ``tail_process`` when it wants to see what happened.
- **Auto-cleanup at session close.** The REPL awaits
  :meth:`ProcessCapability.shutdown` before exiting; it sends SIGTERM to
  every still-running child and waits up to 5s before SIGKILL'ing
  stragglers. No orphan dev servers.
- **Approval policy.** ``start_process`` and ``kill_process`` are
  HITL-gated; ``tail_process`` and ``list_processes`` are read-only.
- **Shell is on.** We invoke through ``asyncio.create_subprocess_shell``
  so users get the familiar quoting / piping semantics. The HITL gate is
  what stops misuse — same posture as :mod:`jac.capabilities.shell`.
- **Bus is optional** (mirrors plan capability). Pass one to get the
  ``ProcessStarted`` / ``ProcessExited`` notifications on the renderer.

Architecture decision: docs/architecture.md §11 D16.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal as _signal
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition

from jac.runtime.bus import EventBus
from jac.runtime.events import ProcessExited, ProcessStarted
from jac.tools import jac_function_toolset, jac_tool
from jac.workspace.paths import find_project_root

ProcessStatus = Literal["running", "exited"]

_OUTPUT_RING_LINES = 2000
_TAIL_MAX_LINES = 2000
_TAIL_DEFAULT_LINES = 50
_SHUTDOWN_GRACE_S = 5.0

# Signal name → POSIX number. Limited set; the LLM should never need ``KILL``
# unless ``TERM`` was already tried.
_SIGNAL_MAP: dict[str, int] = {
    "TERM": _signal.SIGTERM,
    "INT": _signal.SIGINT,
    "KILL": _signal.SIGKILL,
}

_RISKY_TOOLS = frozenset({"start_process", "kill_process"})


@dataclass
class _ProcessRecord:
    """One running (or exited) background process."""

    task_id: str
    command: str
    name: str | None
    process: asyncio.subprocess.Process
    started_at: float
    output: deque[str]
    drain_task: asyncio.Task[None] | None = None
    exit_code: int | None = None

    @property
    def status(self) -> ProcessStatus:
        return "running" if self.exit_code is None else "exited"

    def runtime_s(self) -> float:
        return round(time.monotonic() - self.started_at, 1)


@dataclass
class ProcessStore:
    """Holds the active process records. Pure state — no I/O."""

    records: dict[str, _ProcessRecord] = field(default_factory=dict)
    _counter: int = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"proc-{self._counter}"

    def get(self, task_id: str) -> _ProcessRecord:
        record = self.records.get(task_id)
        if record is None:
            known = sorted(self.records.keys())
            hint = f" Known ids: {known}" if known else " (none active)"
            raise ValueError(f"unknown task_id {task_id!r}.{hint}")
        return record

    def all(self) -> Iterable[_ProcessRecord]:
        return self.records.values()


@dataclass
class ProcessCapability(AbstractCapability[Any]):
    """Background-process toolset.

    Mutating tools (``start_process``, ``kill_process``) are HITL-gated.
    The capability keeps a registry of every spawned process for the life
    of the session and SIGTERMs them on :meth:`shutdown`.
    """

    bus: EventBus | None = None
    store: ProcessStore = field(default_factory=ProcessStore)

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(*self._build_tools())
        return toolset.approval_required(_needs_approval)

    async def shutdown(self) -> None:
        """Kill any still-running children at session close.

        Sends SIGTERM, waits up to ``_SHUTDOWN_GRACE_S`` for the drain
        task to finish, then SIGKILLs anything still alive. Best-effort —
        we never raise from shutdown.
        """
        pending: list[_ProcessRecord] = [r for r in self.store.all() if r.exit_code is None]
        if not pending:
            return
        for record in pending:
            try:
                record.process.terminate()
            except ProcessLookupError:
                continue
        drain_tasks = [r.drain_task for r in pending if r.drain_task is not None]
        if drain_tasks:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*drain_tasks, return_exceptions=True),
                    timeout=_SHUTDOWN_GRACE_S,
                )
        for record in pending:
            if record.exit_code is None:
                try:
                    record.process.kill()
                except ProcessLookupError:
                    continue

    def _build_tools(self) -> list[Any]:
        bus = self.bus
        store = self.store

        async def _emit(event: Any) -> None:
            if bus is not None:
                await bus.emit(event)

        @jac_tool
        async def start_process(reason: str, command: str, name: str | None = None) -> str:
            """Spawn ``command`` as a background process.

            Use this for anything that runs longer than ~30s or needs to
            keep running while you do other work — dev servers, watch
            tests, log tailing, long builds. For one-shot synchronous
            commands, use ``run_shell`` instead.

            stdout and stderr are merged into a 2000-line ring buffer per
            process. Read it with ``tail_process(task_id)``. The process
            inherits the project root as CWD. **Approval-required.**

            Args:
                reason: One-sentence justification (e.g. "start the Vite
                    dev server so we can iterate on the home page").
                command: Shell command line (passed to ``sh -c``).
                name: Optional short label — purely for human reference
                    in ``list_processes``. Defaults to ``None``.

            Returns:
                Confirmation string carrying the ``task_id``. Use that id
                with ``tail_process`` / ``kill_process``.
            """
            task_id = store.next_id()
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=find_project_root(),
            )
            output: deque[str] = deque(maxlen=_OUTPUT_RING_LINES)
            record = _ProcessRecord(
                task_id=task_id,
                command=command,
                name=name,
                process=proc,
                started_at=time.monotonic(),
                output=output,
            )

            async def _drain() -> None:
                assert proc.stdout is not None
                try:
                    async for raw_line in proc.stdout:
                        output.append(raw_line.decode("utf-8", errors="replace").rstrip("\n"))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    output.append(f"[jac: drain error: {exc}]")
                exit_code = await proc.wait()
                record.exit_code = exit_code
                await _emit(ProcessExited(task_id=task_id, exit_code=exit_code))

            record.drain_task = asyncio.create_task(_drain())
            store.records[task_id] = record
            await _emit(ProcessStarted(task_id=task_id, command=command, name=name))
            label = f" (name={name})" if name else ""
            return f"started {task_id}: {command}{label}"

        @jac_tool
        def tail_process(reason: str, task_id: str, lines: int = _TAIL_DEFAULT_LINES) -> str:
            """Return the last ``lines`` of merged stdout/stderr for ``task_id``.

            Each process keeps a rolling 2000-line buffer. If output
            scrolled past that cap, older lines are gone. Read-only — no
            approval needed.

            Args:
                reason: One-sentence justification.
                task_id: Returned by ``start_process``.
                lines: How many trailing lines to return (1-2000).

            Returns:
                A header (``[task_id status exit=N]``) followed by the
                tailed lines, or a "(no output yet)" notice.
            """
            if not (1 <= lines <= _TAIL_MAX_LINES):
                raise ValueError(f"`lines` must be between 1 and {_TAIL_MAX_LINES}; got {lines}.")
            record = store.get(task_id)
            snapshot = list(record.output)[-lines:]
            header_parts = [f"{record.task_id} {record.status}"]
            if record.name:
                header_parts.append(f"name={record.name}")
            if record.exit_code is not None:
                header_parts.append(f"exit={record.exit_code}")
            header = "[" + " ".join(header_parts) + "]"
            if not snapshot:
                return f"{header}\n(no output yet)"
            return header + "\n" + "\n".join(snapshot)

        @jac_tool
        async def kill_process(reason: str, task_id: str, signal: str = "TERM") -> str:
            """Send ``signal`` to a running background process.

            Default is ``TERM`` (graceful). Use ``INT`` to mimic Ctrl-C,
            or ``KILL`` only if ``TERM`` was already tried and ignored.
            **Approval-required.**

            Args:
                reason: One-sentence justification.
                task_id: Returned by ``start_process``.
                signal: ``TERM`` (default) | ``INT`` | ``KILL``.

            Returns:
                Confirmation string. If the process already exited this
                tool returns a no-op notice rather than raising.
            """
            sig_num = _SIGNAL_MAP.get(signal.upper())
            if sig_num is None:
                raise ValueError(f"signal must be one of {sorted(_SIGNAL_MAP)}; got {signal!r}.")
            record = store.get(task_id)
            if record.exit_code is not None:
                return f"{task_id} already exited (code={record.exit_code}); no signal sent."
            try:
                record.process.send_signal(sig_num)
            except ProcessLookupError:
                return f"{task_id} already gone; no signal sent."
            return f"sent SIG{signal.upper()} to {task_id}"

        @jac_tool
        def list_processes(reason: str) -> list[dict[str, Any]]:
            """List every process spawned this session.

            Includes both running and exited processes (until the session
            ends). Read-only.

            Returns:
                A list of dicts with ``task_id`` / ``name`` / ``command``
                / ``status`` / ``exit_code`` / ``runtime_s``.
            """
            return [
                {
                    "task_id": r.task_id,
                    "name": r.name,
                    "command": r.command,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "runtime_s": r.runtime_s(),
                }
                for r in store.all()
            ]

        return [start_process, tail_process, kill_process, list_processes]


def _needs_approval(ctx: Any, tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    return tool_def.name in _RISKY_TOOLS


def make_process_capability(bus: EventBus | None = None) -> ProcessCapability:
    """Build a fresh :class:`ProcessCapability`. One per agent / session."""
    return ProcessCapability(bus=bus)
