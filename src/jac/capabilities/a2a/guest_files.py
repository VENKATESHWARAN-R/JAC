"""Materialize inbound A2A ``FilePart`` payloads onto disk for the guest (Phase 4.d.4).

The guest Gru ships with path-based tools (``read_file``, ``list_dir``,
``grep``, ``glob``) and no bytes-handling tools. When a peer attaches a
``FilePart`` with inline bytes to a ``message/send``, fasta2a's worker
already converts those bytes to pydantic-ai ``BinaryContent`` so
multimodal models can "see" them — but the agent's *tools* still need
a filesystem path. This module bridges the gap:

1. Scan the most recent user message in the task for ``FilePart``
   entries with inline ``bytes``.
2. Decode each, sanitize the filename, save under
   ``<repo>/.agents/a2a/guest-uploads/<context_id>/<filename>``.
3. Return the saved paths so the worker can append a synthetic
   ``UserPromptPart`` telling the agent where the files landed.

Per-context (not per-task) so a multi-turn conversation reuses the
same upload directory; collisions across turns get a numeric suffix
rather than silently overwriting. Receive-side counterpart to the
outbound saving in :mod:`jac.capabilities.a2a.client._save_inbound_files`.

URI-only file parts (``FileWithUri``) are skipped in v1 — fetching
arbitrary URIs needs an SSRF guard we haven't built. Bytes-only.
"""

from __future__ import annotations

import base64
import re
import uuid
from binascii import Error as _BinasciiError
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

from jac.workspace import paths

# Same sanitizer rules as the outbound save path. Allow letters,
# digits, hyphen, underscore, period; strip leading dots so a peer
# can't write to ``.hidden`` files.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def materialize_inbound_files(task: Mapping[str, Any], context_id: str) -> list[str]:
    """Save ``FileWithBytes`` parts from the latest user message to disk.

    Args:
        task: the task dict as loaded from storage (must include
            ``history`` if the message had file parts).
        context_id: the A2A context id — also the subdirectory under
            ``guest-uploads/`` so multi-turn conversations group their
            uploads.

    Returns:
        List of POSIX paths to saved files. Empty when no file parts
        were present, when bytes were malformed, or when disk writes
        all failed.

    Notes:
        - Only the *latest* user message is scanned. Earlier turns'
          files were materialized on their own turns; rescanning would
          either re-write (wasteful) or dedupe (confusing path names).
        - Filename collisions across turns within the same context get
          a numeric suffix (``data-2.csv``, ``data-3.csv``) by checking
          what's already on disk.
        - Best-effort: a single bad part skips itself but doesn't fail
          the call. Same posture as :func:`audit.cleanup_old_contexts`.
    """
    history = task.get("history")
    if not isinstance(history, list) or not history:
        return []
    latest = history[-1]
    if not isinstance(latest, dict) or latest.get("role") != "user":
        return []
    parts = latest.get("parts")
    if not isinstance(parts, list) or not parts:
        return []

    target_dir = paths.project_a2a_guest_uploads_dir() / context_id
    saved: list[str] = []
    target_dir_created = False

    for part in parts:
        if not isinstance(part, dict) or part.get("kind") != "file":
            continue
        file_obj = part.get("file")
        if not isinstance(file_obj, dict):
            continue
        b64 = file_obj.get("bytes")
        if not isinstance(b64, str) or not b64:
            continue  # URI variant or empty — skip in v1
        try:
            data = base64.b64decode(b64, validate=True)
        except (ValueError, _BinasciiError):
            continue

        name = _pick_name(file_obj, part)
        if not target_dir_created:
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return saved
            target_dir_created = True

        out_path = _dedupe_against_disk(target_dir, name)
        try:
            out_path.write_bytes(data)
        except OSError:
            continue
        saved.append(out_path.as_posix())

    return saved


def _pick_name(file_obj: dict[str, Any], part: dict[str, Any]) -> str:
    """Choose the best-available filename, sanitize it, fall back to a uuid.

    Resolution order: ``file.name`` (spec) → ``part.metadata.filename``
    (our belt-and-braces) → ``file-<uuid>.bin``. Sanitization strips
    path components, leading dots, and unsafe characters — the peer
    can't drop a file outside our per-context directory.
    """
    raw = file_obj.get("name")
    if not (isinstance(raw, str) and raw.strip()):
        meta = part.get("metadata")
        if isinstance(meta, dict):
            raw_meta = meta.get("filename")
            if isinstance(raw_meta, str) and raw_meta.strip():
                raw = raw_meta
    if not (isinstance(raw, str) and raw.strip()):
        return f"file-{uuid.uuid4().hex}.bin"
    bare = Path(raw).name  # defeats absolute paths / ``..`` traversal
    cleaned = _SAFE_FILENAME_RE.sub("_", bare).lstrip(".")
    return cleaned or f"file-{uuid.uuid4().hex}.bin"


def _dedupe_against_disk(target_dir: Path, name: str) -> Path:
    """Return a path under ``target_dir`` that doesn't already exist.

    If ``target_dir/name`` is free, return it. Otherwise append ``-2``,
    ``-3``, ... before the extension until we find an unused slot. This
    works across REPL restarts because we check the filesystem, not an
    in-memory set — important for multi-turn conversations that might
    upload the same file again in a later turn.
    """
    candidate = target_dir / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 2
    while True:
        candidate = target_dir / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def strip_binary_content_from_history(history: Iterable[Any]) -> list[Any]:
    """Remove ``BinaryContent`` items from ``UserPromptPart`` content lists.

    The guest Gru ships path-based tools (``read_file`` / ``grep`` / ``glob``),
    and :func:`materialize_inbound_files` already saves attached file bytes
    to disk + tells the agent where they landed via a synthetic prompt. So
    the original ``BinaryContent`` produced by fasta2a's
    ``_request_parts_from_a2a`` is *redundant*. Worse: model adapters
    reject most non-image mime types (CSV / TOML / octet-stream both
    crash OpenAI/Ollama and Anthropic), so leaving the binary parts in
    the history blows up :meth:`pydantic_ai.Agent.run`.

    This pass walks the history, strips ``BinaryContent`` from every
    ``UserPromptPart.content`` list, drops the part entirely when its
    content becomes empty, and drops the whole ``ModelRequest`` when it
    has no parts left. Non-binary parts (text, images-as-strings,
    tool returns, model responses) pass through unchanged.

    Args:
        history: Sequence of ``ModelMessage`` values (typically from
            :meth:`fasta2a.pydantic_ai.AgentWorker.build_message_history`).

    Returns:
        A new list. The input is not mutated — callers can keep a copy
        for audit/logging if needed.
    """
    cleaned: list[Any] = []
    for message in history:
        if not isinstance(message, ModelRequest):
            cleaned.append(message)
            continue
        new_parts: list[Any] = []
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                new_parts.append(part)
                continue
            content = part.content
            if isinstance(content, str):
                new_parts.append(part)
                continue
            if not isinstance(content, list):
                new_parts.append(part)
                continue
            filtered = [item for item in content if not isinstance(item, BinaryContent)]
            if not filtered:
                continue  # drop empty UserPromptPart
            if len(filtered) == len(content):
                new_parts.append(part)
                continue
            new_parts.append(
                UserPromptPart(
                    content=filtered,
                    timestamp=part.timestamp,
                    part_kind=part.part_kind,
                )
            )
        if not new_parts:
            continue  # drop empty ModelRequest
        cleaned.append(
            ModelRequest(
                parts=new_parts,
                instructions=message.instructions,
                kind=message.kind,
            )
        )
    return cleaned


def build_attachment_prompt(saved_paths: list[str]) -> str:
    """Compose a one-message annotation telling the guest agent where files landed.

    Appended as a synthetic ``UserPromptPart`` to the message history
    so any agent (multimodal or not) can read paths and feed them to
    its file tools. Kept short and machine-friendly; the prefix
    ``[a2a attachment]`` is the marker the agent's prompt can refer to.
    """
    if not saved_paths:
        return ""
    lines = "\n".join(f"- {p}" for p in saved_paths)
    return (
        "[a2a attachment] The peer attached file(s) to this turn. JAC has saved them to "
        "disk so your read_file / grep / glob tools can use them:\n"
        f"{lines}"
    )
