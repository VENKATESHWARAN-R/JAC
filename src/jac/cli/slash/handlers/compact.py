"""``/compact`` — force a summarizing compaction of the conversation now.

Works in every ``compaction.strategy`` mode (auto / sliding / manual): it
folds the oldest slice of history into a small-tier summary and continues
from there, regardless of the current fill level. The actual work happens in
the REPL (the live message history lives there and summarization is async) —
this handler just signals the intent via :class:`CompactNow`.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import CompactNow, Handled, SlashResult


@register(
    "compact",
    summary="Summarize the oldest history now to free context",
    usage="/compact",
)
def compact_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args.strip():
        ctx.console.print("[dim]/compact takes no arguments[/dim]")
        return Handled()
    return CompactNow()
