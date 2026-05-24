"""Tests for jac.capabilities.a2a.audit.

Two halves: ``InboundLog`` (JSONL append + dir-creation + best-effort
on disk failure) and ``cleanup_old_contexts`` (mtime-based retention,
honors ``retention_days=0`` as "keep forever").
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from jac.capabilities.a2a.audit import (
    InboundLog,
    InboundRecord,
    cleanup_old_contexts,
    make_message_preview,
    now_iso,
)


def _record(**overrides) -> InboundRecord:
    defaults = {
        "ts": now_iso(),
        "peer_id": "peer-abcd1234",
        "context_id": "ctx-1",
        "task_id": "task-1",
        "state": "completed",
        "duration_ms": 12,
        "tokens_used": 0,
        "message_preview": "hello",
    }
    defaults.update(overrides)
    return InboundRecord(**defaults)


def test_inbound_log_appends_one_line_per_record(tmp_path: Path):
    log = InboundLog(tmp_path / "a2a" / "inbound.jsonl")
    log.append(_record(task_id="task-A"))
    log.append(_record(task_id="task-B"))

    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_id"] == "task-A"
    assert json.loads(lines[1])["task_id"] == "task-B"


def test_inbound_log_creates_parent_dir(tmp_path: Path):
    log_file = tmp_path / "newly_made" / "deep" / "inbound.jsonl"
    log = InboundLog(log_file)
    log.append(_record())
    assert log_file.is_file()


def test_inbound_log_swallows_oserror(tmp_path: Path, monkeypatch):
    """Inbound calls must keep working even if disk writes fail."""
    log = InboundLog(tmp_path / "a2a" / "inbound.jsonl")

    def boom(*args, **kwargs):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(Path, "open", boom)
    # Should not raise.
    log.append(_record())


def test_cleanup_removes_old_files(tmp_path: Path):
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    old_file = ctx_dir / "old.json"
    new_file = ctx_dir / "new.json"
    old_file.write_text("{}")
    new_file.write_text("{}")
    # mtime: 4 days ago vs. now
    four_days_ago = time.time() - (4 * 86_400)
    os.utime(old_file, (four_days_ago, four_days_ago))

    removed = cleanup_old_contexts(ctx_dir, retention_days=3)
    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_disabled_when_retention_zero(tmp_path: Path):
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    old_file = ctx_dir / "old.json"
    old_file.write_text("{}")
    os.utime(old_file, (1, 1))  # epoch — very old

    removed = cleanup_old_contexts(ctx_dir, retention_days=0)
    assert removed == 0
    assert old_file.exists()


def test_cleanup_missing_dir_is_noop(tmp_path: Path):
    # Never created — server hasn't written any contexts yet.
    assert cleanup_old_contexts(tmp_path / "never_created", retention_days=3) == 0


def test_cleanup_ignores_non_json_files(tmp_path: Path):
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    junk = ctx_dir / "README.md"
    junk.write_text("# stray file")
    os.utime(junk, (1, 1))

    removed = cleanup_old_contexts(ctx_dir, retention_days=3)
    assert removed == 0
    assert junk.exists()


def test_message_preview_truncates_with_ellipsis():
    long = "x" * 500
    preview = make_message_preview(long)
    assert len(preview) <= 120
    assert preview.endswith("…")


def test_message_preview_collapses_internal_whitespace():
    preview = make_message_preview("hello\n\n\tworld\nthere")
    assert "\n" not in preview
    assert "\t" not in preview
    assert preview == "hello world there"
