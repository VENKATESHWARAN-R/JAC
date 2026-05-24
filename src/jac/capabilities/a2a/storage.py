"""On-disk Storage for the A2A guest server (D24).

Subclasses fasta2a's :class:`Storage` ABC. Two responsibilities:

- **Tasks** (the JSON-RPC lifecycle records — submitted/working/completed)
  are kept **in memory** for v1. Tasks are ephemeral execution state;
  losing them on a server restart is OK — peers re-issue with the same
  ``context_id`` and pick the conversation up from there.

- **Contexts** (the pydantic-ai ``message_history`` for a conversation
  thread) are **persisted to disk** under
  ``<project>/.agents/a2a/contexts/<context_id>.json``. This is what
  makes multi-turn A2A conversations survive REPL restarts — and what
  gives us the audit trail for replays / post-hoc debugging.

Atomic writes via temp-file + rename, mirroring how
:mod:`jac.capabilities.memory` and :mod:`jac.runtime.session` handle
their JSON state. Retention is enforced by :mod:`.audit` on server
start, not here — this module just reads/writes.

Serialization uses pydantic-ai's :data:`ModelMessagesTypeAdapter` so
the on-disk shape is exactly what the agent loop produces — no custom
schema to keep in sync. Failures to deserialize an old context file
(schema drift across pydantic-ai versions) raise back to the caller;
fasta2a will treat it as "no context" and start fresh.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from fasta2a.schema import Artifact, Message, Task, TaskState, TaskStatus
from fasta2a.storage import Storage
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

_CONTEXT_FILE_SUFFIX = ".json"


class JacFileStorage(Storage[list[ModelMessage]]):
    """Tasks in-memory; contexts on disk.

    Args:
        contexts_dir: directory where ``<context_id>.json`` files live.
            Created on first write — the directory itself doesn't need
            to exist when the server starts. (We let the audit module's
            ``cleanup_old_contexts`` make sure it exists before pruning.)
    """

    def __init__(self, contexts_dir: Path) -> None:
        self._contexts_dir = contexts_dir
        self._tasks: dict[str, Task] = {}

    # ---------- tasks (in-memory; mirrors fasta2a.InMemoryStorage) ----------

    async def load_task(self, task_id: str, history_length: int | None = None) -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if history_length and "history" in task:
            # Shallow copy + slice so we don't mutate the stored record.
            # cast(): spreading a TypedDict drops the TypedDict identity at
            # the type level; the runtime shape is identical to ``Task``.
            task = cast(Task, {**task, "history": task["history"][-history_length:]})
        return task

    async def submit_task(self, context_id: str, message: Message) -> Task:
        task_id = str(uuid4())
        # The A2A spec wants every message to carry both its task_id and
        # context_id (clients use them for correlation across the wire).
        message["task_id"] = task_id
        message["context_id"] = context_id
        task: Task = {
            "id": task_id,
            "context_id": context_id,
            "kind": "task",
            "status": TaskStatus(state="submitted", timestamp=_now_iso()),
            "history": [message],
        }
        self._tasks[task_id] = task
        return task

    async def update_task(
        self,
        task_id: str,
        state: TaskState,
        new_artifacts: list[Artifact] | None = None,
        new_messages: list[Message] | None = None,
    ) -> Task:
        task = self._tasks[task_id]
        task["status"] = TaskStatus(state=state, timestamp=_now_iso())
        if new_artifacts:
            task.setdefault("artifacts", [])
            task["artifacts"].extend(new_artifacts)
        if new_messages:
            task.setdefault("history", [])
            for m in new_messages:
                m["task_id"] = task_id
                m["context_id"] = task["context_id"]
                task["history"].append(m)
        return task

    # ---------- contexts (persisted to disk) ----------

    async def load_context(self, context_id: str) -> list[ModelMessage] | None:
        path = self._context_path(context_id)
        if not path.is_file():
            return None
        try:
            payload = path.read_bytes()
            return ModelMessagesTypeAdapter.validate_json(payload)
        except Exception:
            # Schema drift / corruption — treat as "no context", let the
            # call start fresh. Audit log will still record the call;
            # operator can grep for context_id if they care.
            return None

    async def update_context(self, context_id: str, context: list[ModelMessage]) -> None:
        path = self._context_path(context_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ModelMessagesTypeAdapter.dump_json(context)
        # Atomic write: temp file in same dir + rename. Same pattern as
        # session.save() and memory writes.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, path)

    # ---------- helpers ----------

    def _context_path(self, context_id: str) -> Path:
        # Defense in depth: context_id comes from the wire. Strip any path
        # separators / dotfile tricks before joining.
        safe = _sanitize_context_id(context_id)
        return self._contexts_dir / f"{safe}{_CONTEXT_FILE_SUFFIX}"

    # ---------- test-friendly introspection ----------

    @property
    def tasks(self) -> dict[str, Task]:
        """Live in-memory task map. Read-only for callers; mutation here
        is asking for trouble."""
        return self._tasks

    @property
    def contexts_dir(self) -> Path:
        """The directory we persist contexts to (also used by audit cleanup)."""
        return self._contexts_dir


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _sanitize_context_id(context_id: str) -> str:
    """Map ``context_id`` to a filesystem-safe slug.

    Real-world ``context_id`` values are UUIDs (fasta2a generates them
    when the client doesn't supply one) but the spec permits arbitrary
    strings. We accept any UUID-like / alphanumeric input verbatim,
    and replace everything else with ``-`` so a hostile peer can't
    path-traverse our contexts dir.
    """
    out: list[str] = []
    for ch in context_id:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    cleaned = "".join(out).strip("-_") or "anonymous"
    return cleaned[:128]  # absolute cap; UUIDs are 36 chars
