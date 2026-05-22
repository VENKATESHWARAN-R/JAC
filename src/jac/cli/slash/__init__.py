"""Slash command package.

Importing this module triggers handler registration as a side effect — every
handler module under :mod:`jac.cli.slash.handlers` decorates itself with
:func:`~jac.cli.slash.registry.register` at import time. Callers can then
use :func:`dispatch` and :data:`SLASH_COMMANDS` directly.

See ``docs/architecture.md`` §11 D22 for the slash design intent.
"""

from __future__ import annotations

# Side-effect import: registers every handler with the registry.
from jac.cli.slash import handlers as _handlers  # noqa: F401
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import (
    SLASH_COMMANDS,
    SlashCommand,
    UnknownSlashCommand,
    command_names,
    dispatch,
    parse,
    register,
)
from jac.cli.slash.result import Exit, Handled, RebuildGru, SlashResult, SwitchSession

__all__ = [
    "SLASH_COMMANDS",
    "Exit",
    "Handled",
    "RebuildGru",
    "SlashCommand",
    "SlashContext",
    "SlashResult",
    "SwitchSession",
    "UnknownSlashCommand",
    "command_names",
    "dispatch",
    "parse",
    "register",
]
