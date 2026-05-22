"""Slash command registry + dispatch.

Handlers register themselves with :func:`register` at import time. The
REPL imports :mod:`jac.cli.slash` (which imports every handler module),
parses input that starts with ``/``, looks up the handler by name, and
calls it with a :class:`SlashContext` and the remaining argument string.

Names are kept lowercase. Unknown commands raise :class:`UnknownSlashCommand`
so the REPL can render an actionable error instead of silently passing the
text to the LLM — the slash prefix is unambiguous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jac.cli.slash.context import SlashContext
from jac.cli.slash.result import SlashResult

SlashHandler = Callable[[SlashContext, str], SlashResult]


class UnknownSlashCommand(Exception):
    """Raised by :func:`dispatch` when no handler matches the typed command."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class SlashCommand:
    """A registered slash command."""

    name: str
    """Bare command name without the leading ``/``."""

    summary: str
    """One-line description shown in ``/help``."""

    usage: str
    """Brief usage string shown in ``/help`` (e.g. ``/resume [ID]``)."""

    handler: SlashHandler


SLASH_COMMANDS: dict[str, SlashCommand] = {}
"""Process-wide registry. Populated by handler modules at import time."""


def register(
    name: str, *, summary: str, usage: str | None = None
) -> Callable[[SlashHandler], SlashHandler]:
    """Decorator: register ``func`` as the handler for ``/{name}``."""

    def decorate(func: SlashHandler) -> SlashHandler:
        if name in SLASH_COMMANDS:
            raise RuntimeError(f"slash command /{name} already registered")
        SLASH_COMMANDS[name] = SlashCommand(
            name=name,
            summary=summary,
            usage=usage or f"/{name}",
            handler=func,
        )
        return func

    return decorate


def parse(text: str) -> tuple[str, str]:
    """Split ``/cmd rest of line`` into ``("cmd", "rest of line")``.

    Strips the leading slash and the whitespace separating name from args.
    Raises :class:`ValueError` if ``text`` doesn't start with ``/``.
    """
    if not text.startswith("/"):
        raise ValueError("slash input must start with '/'")
    body = text[1:].lstrip()
    name, _, rest = body.partition(" ")
    return name.lower(), rest.strip()


def dispatch(text: str, ctx: SlashContext) -> SlashResult:
    """Look up the handler for ``text`` and run it.

    Raises:
        UnknownSlashCommand: if no handler matches.
        ValueError: if ``text`` doesn't start with ``/`` (programmer error).
    """
    name, args = parse(text)
    if name not in SLASH_COMMANDS:
        raise UnknownSlashCommand(name)
    return SLASH_COMMANDS[name].handler(ctx, args)


def command_names() -> list[str]:
    """All registered command names, sorted — useful for the prompt completer."""
    return sorted(SLASH_COMMANDS)
