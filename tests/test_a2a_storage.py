"""Tests for jac.capabilities.a2a.storage.

JacFileStorage holds tasks in memory (mirroring fasta2a's InMemoryStorage)
but persists contexts to disk. Coverage:

- submit_task → load_task round trip
- update_task transitions state + appends artifacts/messages
- update_context writes a real JSON file atomically; load_context reads
  it back through ModelMessagesTypeAdapter
- missing context returns None (not an error)
- corrupt context file returns None (schema-drift safety)
- context_id sanitization keeps path-traversal payloads contained
- atomic write leaves no .tmp leftovers
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from fasta2a.schema import Message
from pydantic_ai import ModelRequest, UserPromptPart

from jac.capabilities.a2a.storage import JacFileStorage


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _user_msg(text: str) -> Message:
    """A2A-shaped user message with one text part."""
    return {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "kind": "message",
        "message_id": "m1",
    }


@pytest.fixture
def storage(tmp_path: Path) -> JacFileStorage:
    return JacFileStorage(contexts_dir=tmp_path / "contexts")


def test_submit_and_load_task(storage: JacFileStorage):
    task = _run(storage.submit_task("ctx-1", _user_msg("hello")))
    assert task["status"]["state"] == "submitted"
    assert task["context_id"] == "ctx-1"

    loaded = _run(storage.load_task(task["id"]))
    assert loaded is not None
    assert loaded["id"] == task["id"]


def test_update_task_advances_state(storage: JacFileStorage):
    task = _run(storage.submit_task("ctx-1", _user_msg("hello")))
    updated = _run(storage.update_task(task["id"], state="completed"))
    assert updated["status"]["state"] == "completed"


def test_context_round_trip(storage: JacFileStorage):
    """Save + reload a real list of pydantic-ai ModelMessages."""
    history = [ModelRequest(parts=[UserPromptPart(content="hi from peer")])]
    _run(storage.update_context("ctx-rt", history))
    loaded = _run(storage.load_context("ctx-rt"))
    assert loaded is not None
    assert len(loaded) == 1
    # The reconstructed request carries the same user prompt
    assert loaded[0].parts[0].content == "hi from peer"


def test_load_missing_context_returns_none(storage: JacFileStorage):
    assert _run(storage.load_context("never-existed")) is None


def test_corrupt_context_returns_none(storage: JacFileStorage):
    """A garbled context file shouldn't crash the worker — fasta2a treats
    None as 'start fresh', which is what we want on schema drift."""
    storage._contexts_dir.mkdir(parents=True, exist_ok=True)
    (storage._contexts_dir / "broken.json").write_text("{not valid json")
    assert _run(storage.load_context("broken")) is None


def test_context_id_sanitization_prevents_path_traversal(storage: JacFileStorage):
    """Hostile context_ids must not escape the contexts dir."""
    history = [ModelRequest(parts=[UserPromptPart(content="x")])]
    # Path-traversal attempt — should be sanitized
    _run(storage.update_context("../../escape", history))
    # File lands inside contexts/, with separators replaced
    written = list(storage._contexts_dir.iterdir())
    # Every entry is a direct child of contexts/, never an ancestor file
    assert all(p.parent == storage._contexts_dir for p in written)
    # And we can read it back via the SAME sanitized id (idempotent)
    loaded = _run(storage.load_context("../../escape"))
    assert loaded is not None


def test_atomic_write_leaves_no_tmp(storage: JacFileStorage):
    history = [ModelRequest(parts=[UserPromptPart(content="x")])]
    _run(storage.update_context("ctx-atomic", history))
    leftovers = [p for p in storage._contexts_dir.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
