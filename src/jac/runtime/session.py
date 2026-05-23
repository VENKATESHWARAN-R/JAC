"""Per-session state — message-history persistence on disk.

One JAC session lives at ``<repo>/.agents/sessions/<timestamp>/`` (folder
per session, per ARCH §11 D3). After every completed turn the full message
list is rewritten to ``messages.json`` via ``ModelMessagesTypeAdapter`` —
the format pydantic-ai uses internally, so nothing is lost between save
and load. Tool calls and their results stay paired automatically because
``all_messages()`` returns the canonical ordered list.

Timestamps sort lexically, so listing sessions oldest → newest is a plain
``sorted()``.

Sessions also persist the in-session plan checklist to ``plan.json`` (D27).
:meth:`load_plan` reads it on resume, flipping any ``in_progress`` steps to
``pending`` because the actor was killed mid-step. Malformed files
degrade gracefully — the caller receives a warning string and continues
with an empty plan (D27 reasoning: a corrupt checklist is much smaller
collateral than a corrupt ``messages.json``, never block a resume on it).
The filename stays ``plan.json`` while Plan Mode (D23) is deferred to v2;
when D23 ships the file renames to ``tasks.json`` as part of the bundle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

from jac.errors import JacConfigError
from jac.workspace.paths import project_sessions_dir

_TIMESTAMP_FMT = "%Y-%m-%dT%H-%M-%S"
_MESSAGES_FILENAME = "messages.json"
_PLAN_FILENAME = "plan.json"
_PLAN_SCHEMA_VERSION = 1
_VALID_PLAN_STATUSES = frozenset({"pending", "in_progress", "completed"})


def _new_session_id() -> str:
    """Timestamp-style session id, filesystem-friendly (no colons)."""
    return datetime.now().strftime(_TIMESTAMP_FMT)


@dataclass
class Session:
    """A persistent JAC session.

    Instances are mutable in one respect only: :attr:`message_history` is
    rewritten by :meth:`save` to match what was persisted. Everything else
    is set at construction.
    """

    session_id: str
    message_history: list[ModelMessage] = field(default_factory=list)

    @property
    def session_dir(self) -> Path:
        return project_sessions_dir() / self.session_id

    @property
    def messages_file(self) -> Path:
        return self.session_dir / _MESSAGES_FILENAME

    @property
    def plan_file(self) -> Path:
        return self.session_dir / _PLAN_FILENAME

    def save(self, messages: list[ModelMessage]) -> None:
        """Persist ``messages`` to disk. Overwrites the existing file."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.message_history = messages
        self.messages_file.write_bytes(ModelMessagesTypeAdapter.dump_json(messages, indent=2))

    def load_plan(self) -> tuple[list[dict[str, str]], str | None]:
        """Load the persisted plan checklist (D27).

        Returns ``(steps, warning)``:

        - ``steps`` is a list of ``{"text": str, "status": str}`` dicts.
          Any step that was ``in_progress`` when the prior session was
          killed is flipped to ``pending`` — the actor isn't running, so
          the step needs to be re-started.
        - ``warning`` is ``None`` on a clean load. On a missing file
          ``steps`` is empty and ``warning`` is ``None``. On a malformed
          file (bad JSON, wrong shape, unknown status) ``steps`` is empty
          and ``warning`` is a one-line message the REPL surfaces in
          yellow before continuing — per D27 we never block a resume on a
          bad ``plan.json``.
        """
        if not self.plan_file.is_file():
            return [], None
        try:
            raw = self.plan_file.read_text(encoding="utf-8")
            data: Any = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
                raise ValueError("plan.json must be an object with a 'steps' list.")
            cleaned: list[dict[str, str]] = []
            for i, entry in enumerate(data["steps"], start=1):
                if not isinstance(entry, dict):
                    raise ValueError(f"step #{i} is not an object.")
                text = entry.get("text")
                status = entry.get("status")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f"step #{i} has missing or empty 'text'.")
                if status not in _VALID_PLAN_STATUSES:
                    raise ValueError(
                        f"step #{i} has unknown status {status!r}; "
                        f"expected one of {sorted(_VALID_PLAN_STATUSES)}."
                    )
                # In-progress flips to pending: the actor was killed mid-step.
                if status == "in_progress":
                    status = "pending"
                cleaned.append({"text": text.strip(), "status": status})
            return cleaned, None
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return [], f"prior plan was unreadable ({exc}); continuing with empty checklist"

    @classmethod
    def new(cls) -> Session:
        """Start a fresh session with a timestamp id. Disk write happens on first save."""
        return cls(session_id=_new_session_id(), message_history=[])

    @classmethod
    def resume(cls, session_id: str) -> Session:
        """Load an existing session by id.

        Raises:
            JacConfigError: if the session doesn't exist.
        """
        target = project_sessions_dir() / session_id / _MESSAGES_FILENAME
        if not target.is_file():
            raise JacConfigError(
                f"no session at {target}. Run `jac sessions` to see available ids."
            )
        messages = ModelMessagesTypeAdapter.validate_json(target.read_bytes())
        return cls(session_id=session_id, message_history=list(messages))

    @classmethod
    def resume_latest(cls) -> Session:
        """Load the most recent session.

        Raises:
            JacConfigError: if no sessions exist for the current project.
        """
        latest = cls.latest_id()
        if latest is None:
            raise JacConfigError("no sessions to resume in this project — start one with `jac`")
        return cls.resume(latest)

    @classmethod
    def latest_id(cls) -> str | None:
        """Return the newest session id by name (timestamps sort lexically)."""
        ids = cls.list_ids()
        return ids[-1] if ids else None

    @classmethod
    def list_ids(cls) -> list[str]:
        """Return all session ids in this project, oldest → newest."""
        sessions_dir = project_sessions_dir()
        if not sessions_dir.is_dir():
            return []
        return sorted(
            child.name
            for child in sessions_dir.iterdir()
            if child.is_dir() and (child / _MESSAGES_FILENAME).is_file()
        )
