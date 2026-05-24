"""A2A audit log + context retention (D24).

Two responsibilities, neither huge enough for its own file but distinct
enough not to belong in :mod:`.storage`:

- :class:`InboundLog` — one-line-JSONL appender for
  ``<project>/.agents/a2a/inbound.jsonl``. Records every inbound call
  with peer/context/task IDs, final state, duration, and the message
  preview. Append-only — we never rotate or prune this file, the user
  owns retention via their own log-management workflow.

- :func:`cleanup_old_contexts` — prunes context JSON files older than
  the configured retention window. Called on server start and on a
  1-hour timer while the server runs (PR3 wires the timer; PR1 just
  runs it on start). Best-effort, never raises — partial cleanup is
  better than crashing the server because one file is unreadable.

Both modules are I/O-only — they don't know about the agent runtime
or the bus. That keeps :mod:`.server` testable without a real disk.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_MESSAGE_PREVIEW_MAX = 120
_CONTEXT_FILE_SUFFIX = ".json"


@dataclass(frozen=True)
class InboundRecord:
    """One row in ``inbound.jsonl``. Plain data, no logic."""

    ts: str
    peer_id: str
    context_id: str
    task_id: str
    state: str
    duration_ms: int
    tokens_used: int
    message_preview: str

    def to_jsonl_line(self) -> str:
        """JSON-encoded one-liner ready for ``write()``."""
        # ensure_ascii=False keeps non-ASCII previews readable (UTF-8 on
        # disk); the trailing newline is mandatory for proper JSONL.
        return json.dumps(self.__dict__, ensure_ascii=False) + "\n"


class InboundLog:
    """Append-only writer for ``inbound.jsonl``.

    Thread-safety: relies on POSIX ``O_APPEND`` semantics — concurrent
    writes from multiple guest tasks within the same process are
    line-atomic so long as each line fits in one ``write()`` syscall
    (lines are ≤ a few KB so this holds). Across processes the same
    guarantee holds on every mainstream FS we care about.
    """

    def __init__(self, log_file: Path) -> None:
        self._path = log_file

    def append(self, record: InboundRecord) -> None:
        """Append one record. Creates parent dirs lazily on first write."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Best-effort: log failures shouldn't fail the inbound call.
        # The renderer's [a2a] notification + Logfire span keep the
        # event visible even if the disk write silently failed.
        with contextlib.suppress(OSError), self._path.open("a", encoding="utf-8") as fh:
            fh.write(record.to_jsonl_line())

    @property
    def path(self) -> Path:
        return self._path


def cleanup_old_contexts(contexts_dir: Path, retention_days: int) -> int:
    """Remove context JSON files older than ``retention_days``.

    Args:
        contexts_dir: ``<project>/.agents/a2a/contexts/``. Missing
            directory is a no-op (we haven't written any contexts yet).
        retention_days: ``0`` disables retention (keep forever; useful
            for offline audit). Negative values are treated as 0 — we
            don't raise from a best-effort cleanup pass.

    Returns:
        Number of files removed. ``0`` when nothing was eligible (or
        the dir is missing).
    """
    if retention_days <= 0:
        return 0
    if not contexts_dir.is_dir():
        return 0

    cutoff = time.time() - (retention_days * 86_400)
    removed = 0
    for entry in contexts_dir.iterdir():
        if entry.suffix != _CONTEXT_FILE_SUFFIX or not entry.is_file():
            continue
        # mtime, not creation time — POSIX doesn't really give us ctime,
        # and mtime reflects "last time we touched this context", which
        # is what we actually care about.
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            with contextlib.suppress(OSError):
                os.remove(entry)
                removed += 1
    return removed


def now_iso() -> str:
    """ISO-8601 timestamp with local TZ — used by inbound records and storage."""
    return datetime.now().astimezone().isoformat()


def make_message_preview(text: str) -> str:
    """Truncate ``text`` to a fixed preview length, single-line."""
    flat = " ".join(text.split())  # collapse internal whitespace
    if len(flat) <= _MESSAGE_PREVIEW_MAX:
        return flat
    return flat[: _MESSAGE_PREVIEW_MAX - 1] + "…"
