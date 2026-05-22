"""Tests for the ``$EDITOR`` helper used by ``jac profiles edit``.

We fake ``$EDITOR`` with small Python one-liners so the test doesn't depend
on the host having vi / nano / etc. The subprocess invocation is the real
thing — we want to verify the round-trip through a real ``$EDITOR``.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

from jac.cli.editor import edit_text


def _python_editor(action: str) -> str:
    """Build a ``$EDITOR`` command vector that runs ``action`` against ``sys.argv[1]``.

    The action gets the path in ``p`` and can read/write freely.
    """
    py = shutil.which("python3") or sys.executable
    return (
        f'{py} -c "import sys; p=sys.argv[1]; '
        f"text=open(p).read(); {action}; open(p,'w').write(text)\" "
    )


def test_edit_text_returns_modified_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", _python_editor("text = text.upper()"))
    result = edit_text("hello world\n")
    assert result == "HELLO WORLD\n"


def test_edit_text_returns_none_when_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # No-op editor — just opens and closes.
    monkeypatch.setenv("EDITOR", _python_editor("pass"))
    assert edit_text("identical\n") is None


def test_edit_text_returns_none_when_editor_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    py = shutil.which("python3") or sys.executable
    monkeypatch.setenv("EDITOR", f'{py} -c "import sys; sys.exit(1)"')
    assert edit_text("anything\n") is None


def test_edit_text_visual_wins_over_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", _python_editor("text = 'from-visual\\n'"))
    monkeypatch.setenv("EDITOR", _python_editor("text = 'from-editor\\n'"))
    assert edit_text("seed\n") == "from-visual\n"


def test_edit_text_falls_back_to_vi_when_nothing_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither VISUAL nor EDITOR is set we should at least *attempt* vi.

    We don't actually want to run vi in tests, so we monkeypatch
    subprocess.run to confirm the command starts with ['vi', ...].
    """
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        captured["cmd"] = cmd

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("jac.cli.editor.subprocess.run", fake_run)
    edit_text("payload\n")
    assert captured["cmd"][0] == "vi"


def test_edit_text_cleans_up_tempfile_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """The temp file path must not exist after edit_text returns."""
    leaked_path: dict[str, str] = {}

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        leaked_path["path"] = cmd[-1]

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("jac.cli.editor.subprocess.run", fake_run)
    edit_text("payload\n")
    assert leaked_path["path"]
    assert not os.path.exists(leaked_path["path"])
