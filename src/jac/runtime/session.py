"""Per-session state — message-history persistence on disk.

One JAC session lives at ``<repo>/.agents/sessions/<timestamp>/`` (folder
per session, per ARCH §11 D3). After every completed turn the full message
list is rewritten to ``messages.json`` via ``ModelMessagesTypeAdapter`` —
the format pydantic-ai uses internally, so nothing is lost between save
and load. Tool calls and their results stay paired automatically because
``all_messages()`` returns the canonical ordered list.

Timestamps sort lexically, so listing sessions oldest → newest is a plain
``sorted()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

from jac.errors import JacConfigError
from jac.workspace.paths import project_sessions_dir

_TIMESTAMP_FMT = "%Y-%m-%dT%H-%M-%S"
_MESSAGES_FILENAME = "messages.json"


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

    def save(self, messages: list[ModelMessage]) -> None:
        """Persist ``messages`` to disk. Overwrites the existing file."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.message_history = messages
        self.messages_file.write_bytes(ModelMessagesTypeAdapter.dump_json(messages, indent=2))

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
