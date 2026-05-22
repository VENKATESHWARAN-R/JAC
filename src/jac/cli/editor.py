"""Open the user's ``$EDITOR`` on a temporary file.

Used by ``jac profiles edit NAME`` so the user hand-edits the YAML directly
rather than navigating a menu tree. Generic enough to reuse for future
"edit a YAML / Markdown blob" flows.

Lookup order: ``$VISUAL`` → ``$EDITOR`` → ``vi``. We accept either env var
the way most Unix tools do; ``VISUAL`` wins because it traditionally points
at the full-screen editor.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


def _resolve_editor() -> list[str]:
    """Return the command vector for the user's editor.

    Honors shell-style quoting in the env var so e.g. ``EDITOR="code -w"`` works.
    """
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    return shlex.split(raw)


def edit_text(initial: str, *, suffix: str = ".yaml") -> str | None:
    """Open ``$EDITOR`` on ``initial`` and return what the user saved.

    Returns ``None`` if the editor exited non-zero (user aborted) or the
    content is byte-identical to ``initial`` (no changes — caller treats as
    a no-op).

    Args:
        initial: text to seed the temp file with.
        suffix: temp-file suffix so the editor can pick the right syntax mode.
    """
    editor = _resolve_editor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as fh:
        fh.write(initial)
        tmp_path = Path(fh.name)
    try:
        result = subprocess.run([*editor, str(tmp_path)], check=False)
        if result.returncode != 0:
            return None
        new_text = tmp_path.read_text(encoding="utf-8")
        if new_text == initial:
            return None
        return new_text
    finally:
        tmp_path.unlink(missing_ok=True)
