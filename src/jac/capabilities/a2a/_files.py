"""Shared filename-safety helpers for A2A file transfer (R15).

Both the outbound client (saving files a peer *returned*) and the inbound
guest (saving files a peer *uploaded*) must turn an untrusted,
peer-supplied filename into something safe to join under a per-task /
per-context directory. The sanitize + pick-best-name logic is identical on
both sides and security-relevant — it is the ``..`` traversal defence — so
it lives here once rather than drifting between two copies.

Deduplication is deliberately **not** shared: the outbound side dedupes
in-memory within a single response's parts
(:func:`jac.capabilities.a2a.client._dedupe_name`); the inbound guest
dedupes against what is already on disk across multi-turn conversations
(:func:`jac.capabilities.a2a.guest_files._dedupe_against_disk`). Those are
different, justified strategies — keep them where they are.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Allow letters, digits, hyphen, underscore, period; collapse any run of
# other characters to a single underscore. Leading dots are stripped so a
# peer can't write to ``.hidden`` files.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    """Strip path components, leading dots, and unsafe chars from ``name``.

    Takes the basename only (defeats absolute paths and ``..`` traversal),
    collapses unsafe character runs to ``_``, strips leading dots, and
    falls back to a uuid-based name if nothing safe remains.
    """
    bare = Path(name).name
    cleaned = _SAFE_FILENAME_RE.sub("_", bare).lstrip(".")
    return cleaned or f"file-{uuid.uuid4().hex}.bin"


def pick_filename(file_obj: Mapping[str, Any], part: Mapping[str, Any]) -> str:
    """Choose the best-available filename and sanitize it.

    Resolution order: ``file.name`` (A2A spec) → ``part.metadata.filename``
    (JAC belt-and-braces, since fasta2a's ``FileWithBytes`` TypedDict drops
    ``file.name``) → ``file-<uuid>.bin``. The result is always safe to join
    under a per-task / per-context directory.
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
    return sanitize_filename(raw)
