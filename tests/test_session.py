"""Tests for :class:`jac.runtime.session.Session` — message persistence.

Plan-checklist behaviour (``load_plan``) is exercised in
``test_plan_persistence.py``; this file focuses on the message-history
round-trip and the session-listing surface:

- ``save`` writes ``messages.json`` atomically (tmp + rename, no leftover
  ``.tmp``, and a round-trip through ``ModelMessagesTypeAdapter`` is
  lossless).
- ``resume`` / ``resume_latest`` load by id and fail-first with a clear
  error when the target is missing.
- ``list_ids`` / ``latest_id`` sort lexically (== chronologically for the
  timestamp id format) and ignore stray non-session directories.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from jac.errors import JacConfigError
from jac.runtime.session import Session, SessionSummary, parse_duration
from jac.workspace import paths

# ---------- fixtures / helpers ----------


@pytest.fixture(autouse=True)
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point session storage at a tmp project root.

    Patches ``project_root`` so ``project_state_root`` (and thus
    ``project_sessions_dir``) resolves under ``tmp_path/.agents/sessions``.
    The autouse ``_clear_root_caches`` in conftest handles cache clearing.
    """
    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    yield tmp_path


def _conversation(user: str, reply: str) -> list:
    """A minimal two-message exchange."""
    return [
        ModelRequest(parts=[UserPromptPart(content=user)]),
        ModelResponse(parts=[TextPart(content=reply)]),
    ]


def _texts(messages: list) -> list[str]:
    """Flatten the prose out of a message list for easy assertions."""
    out: list[str] = []
    for msg in messages:
        for part in msg.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                out.append(content)
    return out


# ---------- save / resume round-trip ----------


def test_save_then_resume_round_trips_messages() -> None:
    session = Session.new()
    session.save(_conversation("hello", "hi there"))

    resumed = Session.resume(session.session_id)
    assert resumed.session_id == session.session_id
    assert _texts(resumed.message_history) == ["hello", "hi there"]


def test_save_creates_session_dir_lazily() -> None:
    session = Session.new()
    assert not session.session_dir.exists()  # nothing on disk until first save
    session.save(_conversation("q", "a"))
    assert session.messages_file.is_file()


def test_save_overwrites_existing_file() -> None:
    session = Session.new()
    session.save(_conversation("first", "1"))
    session.save(_conversation("second", "2"))
    resumed = Session.resume(session.session_id)
    assert _texts(resumed.message_history) == ["second", "2"]


def test_save_updates_in_memory_history() -> None:
    session = Session.new()
    convo = _conversation("q", "a")
    session.save(convo)
    assert session.message_history == convo


# ---------- atomicity ----------


def test_save_is_atomic_leaves_no_tmp_file() -> None:
    session = Session.new()
    session.save(_conversation("q", "a"))
    leftovers = list(session.session_dir.glob("*.tmp"))
    assert leftovers == []


def test_save_does_not_clobber_on_repeated_writes() -> None:
    """A second save fully replaces the first — no interleaving/partial state."""
    session = Session.new()
    for i in range(5):
        session.save(_conversation(f"turn {i}", f"reply {i}"))
    resumed = Session.resume(session.session_id)
    assert _texts(resumed.message_history) == ["turn 4", "reply 4"]


# ---------- resume failure modes ----------


def test_resume_unknown_id_raises() -> None:
    with pytest.raises(JacConfigError, match="no session at"):
        Session.resume("2099-01-01T00-00-00")


def test_resume_latest_with_no_sessions_raises() -> None:
    with pytest.raises(JacConfigError, match="no sessions to resume"):
        Session.resume_latest()


def test_resume_latest_picks_newest() -> None:
    older = Session(session_id="2026-05-01T10-00-00")
    older.save(_conversation("old", "old-reply"))
    newer = Session(session_id="2026-05-29T10-00-00")
    newer.save(_conversation("new", "new-reply"))

    latest = Session.resume_latest()
    assert latest.session_id == "2026-05-29T10-00-00"
    assert _texts(latest.message_history) == ["new", "new-reply"]


# ---------- listing ----------


def test_list_ids_empty_when_no_sessions() -> None:
    assert Session.list_ids() == []
    assert Session.latest_id() is None


def test_list_ids_sorted_oldest_to_newest() -> None:
    for sid in ("2026-05-29T10-00-00", "2026-05-01T10-00-00", "2026-05-15T10-00-00"):
        Session(session_id=sid).save(_conversation("x", "y"))
    assert Session.list_ids() == [
        "2026-05-01T10-00-00",
        "2026-05-15T10-00-00",
        "2026-05-29T10-00-00",
    ]
    assert Session.latest_id() == "2026-05-29T10-00-00"


def test_list_ids_ignores_dirs_without_messages_file(isolated_project: Path) -> None:
    """A stray directory under sessions/ (no messages.json) isn't a session."""
    Session(session_id="2026-05-29T10-00-00").save(_conversation("x", "y"))
    stray = paths.project_sessions_dir() / "not-a-session"
    stray.mkdir(parents=True)
    (stray / "README.txt").write_text("noise")
    assert Session.list_ids() == ["2026-05-29T10-00-00"]


def test_list_ids_empty_when_sessions_dir_absent() -> None:
    """No sessions/ directory at all → empty list, not an error."""
    assert not paths.project_sessions_dir().exists()
    assert Session.list_ids() == []


# ---------- list_summaries ----------


def test_list_summaries_counts_messages_and_parses_date() -> None:
    Session(session_id="2026-05-29T10-00-00").save(_conversation("q", "a"))
    summaries = Session.list_summaries()
    assert len(summaries) == 1
    summary = summaries[0]
    assert isinstance(summary, SessionSummary)
    assert summary.session_id == "2026-05-29T10-00-00"
    assert summary.message_count == 2
    assert summary.created is not None
    assert (summary.created.year, summary.created.month, summary.created.day) == (2026, 5, 29)
    assert (summary.created.hour, summary.created.minute) == (10, 0)


def test_list_summaries_oldest_to_newest() -> None:
    for sid in ("2026-05-29T10-00-00", "2026-05-01T10-00-00"):
        Session(session_id=sid).save(_conversation("q", "a"))
    ids = [s.session_id for s in Session.list_summaries()]
    assert ids == ["2026-05-01T10-00-00", "2026-05-29T10-00-00"]


def test_list_summaries_unreadable_file_yields_none_count(isolated_project: Path) -> None:
    """A corrupt messages.json keeps the session listed with a None count."""
    sid = "2026-05-29T10-00-00"
    session_dir = paths.project_sessions_dir() / sid
    session_dir.mkdir(parents=True)
    (session_dir / "messages.json").write_text("{not json")
    summaries = Session.list_summaries()
    assert len(summaries) == 1
    assert summaries[0].message_count is None


def test_list_summaries_non_timestamp_id_has_none_created(isolated_project: Path) -> None:
    sid = "hand-renamed-session"
    session_dir = paths.project_sessions_dir() / sid
    session_dir.mkdir(parents=True)
    (session_dir / "messages.json").write_text("[]")
    summary = next(s for s in Session.list_summaries() if s.session_id == sid)
    assert summary.created is None
    assert summary.message_count == 0


# ---------- delete ----------


def test_delete_removes_session_dir() -> None:
    session = Session(session_id="2026-05-29T10-00-00")
    session.save(_conversation("q", "a"))
    assert session.session_dir.exists()
    Session.delete(session.session_id)
    assert not session.session_dir.exists()
    assert Session.list_ids() == []


def test_delete_unknown_id_raises() -> None:
    with pytest.raises(JacConfigError, match="no session"):
        Session.delete("2099-01-01T00-00-00")


def test_delete_leaves_other_sessions_intact() -> None:
    Session(session_id="2026-05-01T10-00-00").save(_conversation("a", "1"))
    Session(session_id="2026-05-29T10-00-00").save(_conversation("b", "2"))
    Session.delete("2026-05-01T10-00-00")
    assert Session.list_ids() == ["2026-05-29T10-00-00"]


# ---------- prune_older_than ----------


def test_prune_deletes_only_old_sessions() -> None:
    now = datetime(2026, 5, 29, 12, 0, 0)
    Session(session_id="2026-01-01T10-00-00").save(_conversation("old", "1"))
    Session(session_id="2026-05-28T10-00-00").save(_conversation("recent", "2"))

    deleted = Session.prune_older_than(timedelta(days=30), now=now)
    assert deleted == ["2026-01-01T10-00-00"]
    assert Session.list_ids() == ["2026-05-28T10-00-00"]


def test_prune_skips_non_timestamp_ids(isolated_project: Path) -> None:
    """A hand-renamed session (unparseable id) is never pruned."""
    stray = paths.project_sessions_dir() / "keepme"
    stray.mkdir(parents=True)
    (stray / "messages.json").write_text("[]")
    deleted = Session.prune_older_than(timedelta(days=1), now=datetime(2030, 1, 1))
    assert deleted == []
    assert "keepme" in Session.list_ids()


def test_prune_nothing_to_do_returns_empty() -> None:
    Session(session_id="2026-05-29T10-00-00").save(_conversation("q", "a"))
    deleted = Session.prune_older_than(timedelta(days=365), now=datetime(2026, 5, 29, 13))
    assert deleted == []
    assert Session.list_ids() == ["2026-05-29T10-00-00"]


# ---------- parse_duration ----------


def test_parse_duration_units() -> None:
    assert parse_duration("30d") == timedelta(days=30)
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("2w") == timedelta(weeks=2)
    assert parse_duration(" 7D ") == timedelta(days=7)  # tolerant of case + space


@pytest.mark.parametrize("bad", ["", "30", "d", "30x", "-5d", "0d", "1.5d"])
def test_parse_duration_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad)
