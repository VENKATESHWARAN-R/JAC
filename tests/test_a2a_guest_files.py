"""Tests for jac.capabilities.a2a.guest_files.

Materializing inbound FilePart bytes onto disk for the guest server
(Phase 4.d.4). Unit-tested in isolation here — the end-to-end
integration with a real ASGI request lives in ``test_a2a_server``.
"""

from __future__ import annotations

import base64
from pathlib import Path

from jac.capabilities.a2a.guest_files import (
    build_attachment_prompt,
    materialize_inbound_files,
)


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _task(*parts: dict) -> dict:
    """Build a minimal task dict whose latest user message has ``parts``."""
    return {
        "id": "task-1",
        "history": [
            {
                "role": "user",
                "parts": list(parts),
                "kind": "message",
                "messageId": "m1",
            }
        ],
    }


def _pin_root(tmp_path: Path, monkeypatch) -> None:
    """Point JAC's project root at tmp_path for the test."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    if hasattr(paths.find_project_root, "cache_clear"):
        paths.find_project_root.cache_clear()


# ---------- happy path ----------


def test_materialize_saves_file_with_bytes(tmp_path, monkeypatch):
    _pin_root(tmp_path, monkeypatch)

    task = _task(
        {"kind": "text", "text": "please analyze"},
        {
            "kind": "file",
            "file": {"name": "data.csv", "mimeType": "text/csv", "bytes": _b64(b"a,b\n1,2\n")},
        },
    )

    saved = materialize_inbound_files(task, "ctx-9")

    assert len(saved) == 1
    p = Path(saved[0])
    assert p.exists()
    assert p.read_bytes() == b"a,b\n1,2\n"
    assert p.parts[-3:] == ("guest-uploads", "ctx-9", "data.csv")


def test_materialize_returns_empty_when_no_file_parts(tmp_path, monkeypatch):
    _pin_root(tmp_path, monkeypatch)

    task = _task({"kind": "text", "text": "just a question"})
    saved = materialize_inbound_files(task, "ctx-x")

    assert saved == []
    # No directory created when nothing to save.
    assert not (tmp_path / ".agents" / "a2a" / "guest-uploads").exists()


def test_materialize_returns_empty_when_no_history(tmp_path, monkeypatch):
    _pin_root(tmp_path, monkeypatch)

    assert materialize_inbound_files({}, "ctx-x") == []
    assert materialize_inbound_files({"history": []}, "ctx-x") == []


# ---------- safety / robustness ----------


def test_materialize_sanitizes_path_traversal(tmp_path, monkeypatch):
    """A peer trying to escape the per-context dir with ``..`` gets boxed."""
    _pin_root(tmp_path, monkeypatch)

    task = _task(
        {
            "kind": "file",
            "file": {"name": "../../etc/passwd", "bytes": _b64(b"haha")},
        }
    )
    saved = materialize_inbound_files(task, "ctx-evil")

    assert len(saved) == 1
    p = Path(saved[0])
    # Stays under the per-context dir
    assert "guest-uploads/ctx-evil" in p.as_posix()
    assert ".." not in p.parts
    assert p.exists()


def test_materialize_falls_back_to_uuid_when_name_missing(tmp_path, monkeypatch):
    _pin_root(tmp_path, monkeypatch)

    task = _task({"kind": "file", "file": {"bytes": _b64(b"raw")}})
    saved = materialize_inbound_files(task, "ctx-noname")

    assert len(saved) == 1
    name = Path(saved[0]).name
    assert name.startswith("file-")
    assert name.endswith(".bin")


def test_materialize_uses_metadata_filename_when_file_name_missing(tmp_path, monkeypatch):
    """Belt-and-braces: filename in part.metadata when file.name absent."""
    _pin_root(tmp_path, monkeypatch)

    task = _task(
        {
            "kind": "file",
            "file": {"bytes": _b64(b"x")},
            "metadata": {"filename": "from-metadata.csv"},
        }
    )
    saved = materialize_inbound_files(task, "ctx-meta")
    assert Path(saved[0]).name == "from-metadata.csv"


def test_materialize_skips_malformed_base64(tmp_path, monkeypatch):
    _pin_root(tmp_path, monkeypatch)

    task = _task(
        {"kind": "file", "file": {"name": "broken.bin", "bytes": "@@@not-base64@@@"}},
        {"kind": "file", "file": {"name": "good.png", "bytes": _b64(b"ok")}},
    )
    saved = materialize_inbound_files(task, "ctx-mixed")

    assert len(saved) == 1
    assert Path(saved[0]).name == "good.png"


def test_materialize_skips_uri_only_part(tmp_path, monkeypatch):
    """v1 doesn't fetch URIs — skip those, save only inline bytes."""
    _pin_root(tmp_path, monkeypatch)

    task = _task(
        {"kind": "file", "file": {"name": "remote.bin", "uri": "https://example.com/data"}}
    )
    saved = materialize_inbound_files(task, "ctx-uri")

    assert saved == []


def test_materialize_dedupes_against_existing_files(tmp_path, monkeypatch):
    """A follow-up turn uploading the same filename gets a -N suffix
    rather than silently overwriting the prior turn's file."""
    _pin_root(tmp_path, monkeypatch)

    # Pre-populate one prior turn's file
    pre_dir = tmp_path / ".agents" / "a2a" / "guest-uploads" / "ctx-dup"
    pre_dir.mkdir(parents=True)
    (pre_dir / "data.csv").write_bytes(b"first")

    task = _task({"kind": "file", "file": {"name": "data.csv", "bytes": _b64(b"second")}})
    saved = materialize_inbound_files(task, "ctx-dup")

    assert len(saved) == 1
    p = Path(saved[0])
    assert p.name == "data-2.csv"
    assert p.read_bytes() == b"second"
    # Prior file untouched
    assert (pre_dir / "data.csv").read_bytes() == b"first"


def test_materialize_only_scans_latest_user_message(tmp_path, monkeypatch):
    """Files attached in earlier turns shouldn't be re-materialized — they
    were saved on their own turn."""
    _pin_root(tmp_path, monkeypatch)

    task = {
        "id": "task-multi",
        "history": [
            {
                "role": "user",
                "parts": [{"kind": "file", "file": {"name": "old.csv", "bytes": _b64(b"old")}}],
                "kind": "message",
                "messageId": "m0",
            },
            {
                "role": "agent",
                "parts": [{"kind": "text", "text": "got it"}],
                "kind": "message",
                "messageId": "m1",
            },
            {
                "role": "user",
                "parts": [{"kind": "file", "file": {"name": "new.csv", "bytes": _b64(b"new")}}],
                "kind": "message",
                "messageId": "m2",
            },
        ],
    }
    saved = materialize_inbound_files(task, "ctx-multi")

    assert len(saved) == 1
    assert Path(saved[0]).name == "new.csv"


def test_materialize_ignores_when_latest_is_agent_message(tmp_path, monkeypatch):
    """If the latest history entry isn't a user message (shouldn't happen
    in practice, but be defensive), don't try to materialize."""
    _pin_root(tmp_path, monkeypatch)

    task = {
        "id": "t",
        "history": [
            {
                "role": "agent",
                "parts": [{"kind": "text", "text": "no user turn"}],
                "kind": "message",
                "messageId": "m1",
            }
        ],
    }
    assert materialize_inbound_files(task, "ctx-y") == []


# ---------- build_attachment_prompt ----------


def test_attachment_prompt_lists_each_path():
    msg = build_attachment_prompt(["/tmp/a.csv", "/tmp/b.png"])
    assert "[a2a attachment]" in msg
    assert "/tmp/a.csv" in msg
    assert "/tmp/b.png" in msg
    assert "read_file" in msg  # tells the agent what to do


def test_attachment_prompt_empty_when_no_paths():
    assert build_attachment_prompt([]) == ""
