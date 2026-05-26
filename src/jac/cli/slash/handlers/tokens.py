"""``/tokens`` — detailed token usage counters (D25, Phase A.3).

Sibling of ``/budget``. Shows the input / output / total / project_total
counts side-by-side, plus — when activity has been recorded — the prompt
cache hit rate and the tool-result summarizer savings. Read-only;
mutating happens via ``/budget extend``.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.runtime.tool_summarize import get_summarizer_stats


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
        f"[bold]session:[/bold]   input={tracker.counters.input_tokens:,}  "
        f"output={tracker.counters.output_tokens:,}  "
        f"total={tracker.counters.total_tokens:,}"
    )

    # Prompt cache stats — only shown when the provider reported any
    # cache activity. Anthropic populates these; many others don't.
    cache_pct = tracker.counters.cache_hit_pct
    if cache_pct is not None and (
        tracker.counters.cache_read_tokens or tracker.counters.cache_write_tokens
    ):
        ctx.console.print(
            f"[bold]cache:[/bold]     read={tracker.counters.cache_read_tokens:,}  "
            f"write={tracker.counters.cache_write_tokens:,}  "
            f"hit_rate={cache_pct}%  "
            "[dim](prompt cache, provider-reported)[/dim]"
        )

    # Tool-result summarizer — only shown after at least one summarization
    # has fired this session.
    stats = get_summarizer_stats()
    if stats.calls > 0:
        ctx.console.print(
            f"[bold]summarize:[/bold] calls={stats.calls:,}  "
            f"original={stats.original_tokens:,}  "
            f"summary={stats.summary_tokens:,}  "
            f"saved={stats.saved_tokens:,}  "
            f"[dim](small-tier spent "
            f"in={stats.summarizer_input_tokens:,} "
            f"out={stats.summarizer_output_tokens:,})[/dim]"
        )

    if tracker.external.total_tokens > 0:
        ctx.console.print(
            f"[bold]a2a guest:[/bold] input={tracker.external.input_tokens:,}  "
            f"output={tracker.external.output_tokens:,}  "
            f"total={tracker.external.total_tokens:,}  "
            "[dim](counted under project_total only)[/dim]"
        )
    ctx.console.print(
        f"[bold]project:[/bold]   total={tracker.project_total_tokens:,}  "
        f"[dim](baseline={tracker.project_baseline:,} "
        "from prior sessions in this repo)[/dim]"
    )
    if tracker.limits.any_configured():
        ctx.console.print("[dim]see [bold]/budget[/bold] for configured limits.[/dim]")
    return Handled()
