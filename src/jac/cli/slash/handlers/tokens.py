"""``/tokens`` — detailed token usage counters (D25).

Sibling of ``/budget``. Shows the input / output / total / project_total
counts side-by-side. Read-only; mutating happens via ``/budget extend``.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult


@register(
    "tokens",
    summary="Show detailed token usage counters",
    usage="/tokens",
)
def tokens_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args.strip():
        ctx.console.print("[dim]/tokens takes no arguments[/dim]")

    tracker = ctx.usage_tracker
    if tracker is None:
        ctx.console.print("[dim]no usage tracker (likely a test context)[/dim]")
        return Handled()

    ctx.console.print(
        f"[bold]session:[/bold]  input={tracker.counters.input_tokens:,}  "
        f"output={tracker.counters.output_tokens:,}  "
        f"total={tracker.counters.total_tokens:,}"
    )
    ctx.console.print(
        f"[bold]project:[/bold] total={tracker.project_total_tokens:,}  "
        f"[dim](baseline={tracker.project_baseline:,} "
        "from prior sessions in this repo)[/dim]"
    )
    if tracker.limits.any_configured():
        ctx.console.print("[dim]see [bold]/budget[/bold] for configured limits.[/dim]")
    return Handled()
