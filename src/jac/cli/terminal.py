"""Terminal-mode safety for interactive prompts.

A spawned subprocess that inherits the controlling TTY (notably some MCP
stdio servers — Node-based ones like chrome-devtools / playwright) can flip
the terminal into **raw mode** and never restore it. In raw mode the line
discipline is off: ``ICRNL`` is cleared so Enter sends a bare ``\\r`` that
``input()`` / ``rich.Prompt`` never see as end-of-line (it echoes ``^M`` and
hangs), and ``ISIG`` is cleared so Ctrl-C / Ctrl-\\ stop generating signals.
The result is a frozen approval prompt.

We defend against that at the point of input: :func:`cooked_mode` forces the
TTY back into canonical mode (line editing, echo, signals, CR→NL) for the
duration of a blocking prompt and restores the prior attributes afterwards.
Safe to use unconditionally — it's a no-op when stdin isn't a TTY or when
``termios`` is unavailable (Windows).

The real fix lives upstream too: MCP subprocesses get their stderr redirected
to a log file (see :mod:`jac.capabilities.mcp`) so they never hold the TTY in
the first place. This module is the belt to that suspenders.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator


@contextlib.contextmanager
def cooked_mode() -> Iterator[None]:
    """Force the controlling TTY into canonical mode for a blocking read.

    Ensures ``ICRNL`` (so Enter submits), ``ICANON`` (line editing),
    ``ECHO``, ``ISIG`` (Ctrl-C works), and ``IEXTEN`` are on while the body
    runs, then restores the attributes that were in effect on entry. A no-op
    when stdin isn't an interactive TTY or ``termios`` is unavailable
    (Windows).
    """
    if not sys.stdin.isatty():
        yield
        return
    try:
        import termios  # POSIX only; absent on Windows.
    except ImportError:  # pragma: no cover - exercised only on Windows
        yield
        return
    fd = sys.stdin.fileno()
    try:
        previous = termios.tcgetattr(fd)
    except (termios.error, OSError):  # not a real tty after all
        yield
        return
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] |= termios.ICRNL  # iflag: map CR→NL so Enter ends the line
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN  # lflag
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        yield
    finally:
        with contextlib.suppress(termios.error, OSError):
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)
