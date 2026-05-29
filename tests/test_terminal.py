"""Tests for the cooked-mode terminal guard (jac.cli.terminal)."""

from __future__ import annotations

import sys
from unittest.mock import patch

from jac.cli.terminal import cooked_mode


def test_cooked_mode_noop_when_not_a_tty() -> None:
    # Under pytest stdin isn't a TTY; cooked_mode must be a clean no-op that
    # still runs the body (and never touches termios).
    ran = False
    with cooked_mode():
        ran = True
    assert ran is True


def test_cooked_mode_restores_attrs_on_a_fake_tty() -> None:
    fake_attrs = [0, 0, 0, 0, 0, 0, []]
    calls: list[str] = []

    class _FakeTermios:
        ICRNL = 1
        ICANON = 2
        ECHO = 4
        ISIG = 8
        IEXTEN = 16
        TCSANOW = 0
        TCSADRAIN = 1
        error = OSError

        def tcgetattr(self, _fd: int) -> list:
            calls.append("get")
            return list(fake_attrs)

        def tcsetattr(self, _fd: int, _when: int, _attrs: list) -> None:
            calls.append("set")

    with (
        patch.object(sys.stdin, "isatty", lambda: True),
        patch.object(sys.stdin, "fileno", lambda: 0),
        patch.dict("sys.modules", {"termios": _FakeTermios()}),
        cooked_mode(),
    ):
        pass

    # Entry reads attrs twice (snapshot + working copy), sets cooked, and
    # restores on exit → exactly two tcsetattr calls.
    assert calls.count("set") == 2
