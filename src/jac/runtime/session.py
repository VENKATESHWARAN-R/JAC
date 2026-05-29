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
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

from jac.errors import JacConfigError
from jac.workspace.paths import project_sessions_dir

_TIMESTAMP_FMT = "%Y-%m-%dT%H-%M-%S"
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([wdh])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"w": "weeks", "d": "days", "h": "hours"}


def parse_duration(text: str) -> timedelta:
    """Parse a short retention string like ``30d`` / ``12h`` / ``2w``.

    Supported units: ``w`` (weeks), ``d`` (days), ``h`` (hours). Used by
    ``jac sessions prune --older-than``. Raises :class:`ValueError` with an
    actionable message on anything else, so the CLI fails loud rather than
    pruning an unexpected window.
    """
    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError(
            f"invalid duration {text!r}; use a number followed by w/d/h (e.g. 30d, 12h, 2w)."
        )
    value, unit = int(match.group(1)), match.group(2).lower()
    if value == 0:
        raise ValueError("duration must be greater than zero.")
    return timedelta(**{_DURATION_UNITS[unit]: value})


_MESSAGES_FILENAME = "messages.json"
_PLAN_FILENAME = "plan.json"
_PLAN_SCHEMA_VERSION = 1
_VALID_PLAN_STATUSES = frozenset({"pending", "in_progress", "completed"})


def _new_session_id() -> str:
    """Timestamp-style session id, filesystem-friendly (no colons)."""
    return datetime.now().strftime(_TIMESTAMP_FMT)


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight metadata for one session, for listing without a full load.

    ``message_count`` is taken from the length of the top-level JSON array
    in ``messages.json`` — cheap, no schema validation. ``None`` when the
    file is unreadable. ``created`` is parsed from the timestamp id;
    ``None`` if the id isn't in the expected format (e.g. a hand-renamed
    directory).
    """

    session_id: str
    message_count: int | None
    created: datetime | None


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
        """Persist ``messages`` to disk. Overwrites the existing file.

        Written atomically via a sibling tempfile + rename so a kill
        mid-write can't truncate ``messages.json`` and strand the session.
        ``Path.replace`` is atomic within a filesystem, which holds here:
        the tempfile is created in the same session dir as the target.
        """
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.message_history = messages
        payload = ModelMessagesTypeAdapter.dump_json(messages, indent=2)
        tmp = self.messages_file.with_name(self.messages_file.name + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(self.messages_file)

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

    @classmethod
    def list_summaries(cls) -> list[SessionSummary]:
        """Return a :class:`SessionSummary` per session, oldest → newest.

        Reads each ``messages.json`` only to count its top-level entries —
        no full :class:`ModelMessage` validation, so listing stays cheap
        even with many sessions. An unreadable or malformed file yields a
        ``None`` count rather than dropping the session from the list.
        """
        sessions_dir = project_sessions_dir()
        summaries: list[SessionSummary] = []
        for sid in cls.list_ids():
            messages_file = sessions_dir / sid / _MESSAGES_FILENAME
            try:
                data = json.loads(messages_file.read_text(encoding="utf-8"))
                count = len(data) if isinstance(data, list) else None
            except (OSError, json.JSONDecodeError):
                count = None
            try:
                created = datetime.strptime(sid, _TIMESTAMP_FMT)
            except ValueError:
                created = None
            summaries.append(SessionSummary(session_id=sid, message_count=count, created=created))
        return summaries

    @classmethod
    def delete(cls, session_id: str) -> None:
        """Delete one session's directory (messages, plan, compacted slices).

        The token-usage ledger (``usage.jsonl``) is **not** touched — those
        tokens were genuinely spent and still count toward ``project_total``.

        Raises:
            JacConfigError: if no session with that id exists.
        """
        session_dir = project_sessions_dir() / session_id
        if not (session_dir / _MESSAGES_FILENAME).is_file():
            raise JacConfigError(
                f"no session {session_id!r} to delete. Run `jac sessions` to see ids."
            )
        shutil.rmtree(session_dir)

    @classmethod
    def prune_older_than(cls, max_age: timedelta, *, now: datetime | None = None) -> list[str]:
        """Delete sessions created more than ``max_age`` ago. Returns deleted ids.

        Age is read from the timestamp id (creation time). Sessions whose id
        isn't a parseable timestamp (hand-renamed dirs) are **skipped**, never
        deleted — we won't guess an age we can't read. Leaves ``usage.jsonl``
        intact, like :meth:`delete`.
        """
        cutoff = (now or datetime.now()) - max_age
        deleted: list[str] = []
        for summary in cls.list_summaries():
            if summary.created is None:
                continue
            if summary.created < cutoff:
                cls.delete(summary.session_id)
                deleted.append(summary.session_id)
        return deleted
