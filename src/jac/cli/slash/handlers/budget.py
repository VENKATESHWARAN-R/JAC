"""``/budget`` and ``/tokens`` — token-budget visibility + mid-session override (D25).

``/budget`` (no arg) shows the configured limits with current usage; with
``extend N`` adds tokens to ``session_total`` (default kind), or
``extend KIND N`` for a specific knob. ``/tokens`` is a detail view:
input / output / total / project_total counts side-by-side.

These two share their internals with the :class:`UsageTracker` carried on
:class:`SlashContext` — no model roundtrip, no duplicate counters.

Per D25 there is **no** ``/cost`` slash. Tokens map to whatever pricing
the user has; we don't ship a stale conversion table.
"""

from __future__ import annotations

from typing import cast

from rich.table import Table

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.runtime.events import BudgetKind

_VALID_KINDS: tuple[BudgetKind, ...] = (
    "session_input",
    "session_total",
    "project_total",
)
_DEFAULT_EXTEND_KIND: BudgetKind = "session_total"


def _format_pct(used: int, limit: int | None) -> str:
    if limit is None:
        return "[dim]—[/dim]"
    pct = int((used / limit) * 100) if limit > 0 else 0
    color = "red" if pct >= 100 else ("yellow" if pct >= 80 else "white")
    return f"[{color}]{pct}%[/{color}]"


def _format_limit(limit: int | None) -> str:
    return "[dim]not set[/dim]" if limit is None else f"{limit:,}"


def _print_state(ctx: SlashContext) -> None:
    tracker = ctx.usage_tracker
    if tracker is None:
        ctx.console.print("[dim]no usage tracker (likely a test context)[/dim]")
        return
    if not tracker.limits.any_configured():
        ctx.console.print(
            "[dim]no token budget configured — add a [bold]budget:[/bold] block "
            "to your config to enable hard-stops.[/dim]"
        )
        ctx.console.print(
            f"[dim]current session usage:[/dim] "
            f"input=[bold]{tracker.counters.input_tokens:,}[/bold]  "
            f"output=[bold]{tracker.counters.output_tokens:,}[/bold]  "
            f"total=[bold]{tracker.counters.total_tokens:,}[/bold]"
        )
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("kind")
    table.add_column("used", justify="right")
    table.add_column("limit", justify="right")
    table.add_column("pct", justify="right")
    for kind in _VALID_KINDS:
        used = tracker.usage_for(kind)
        limit = tracker.limits.limit_for(kind)
        table.add_row(kind, f"{used:,}", _format_limit(limit), _format_pct(used, limit))
    ctx.console.print(table)
    ctx.console.print(
        "[dim]raise a limit for this session with "
        "[bold]/budget extend N[/bold] (default kind: session_total).[/dim]"
    )


def _parse_extend_args(args: str) -> tuple[BudgetKind, int] | str:
    """Parse ``extend N`` or ``extend KIND N``. Returns the parsed pair or
    an error string for the caller to render."""
    parts = args.split()
    if len(parts) == 1:
        try:
            amount = int(parts[0].replace(",", "").replace("_", ""))
        except ValueError:
            return f"could not parse {parts[0]!r} as a token count."
        return _DEFAULT_EXTEND_KIND, amount
    if len(parts) == 2:
        kind_str, amount_str = parts
        if kind_str not in _VALID_KINDS:
            return f"unknown budget kind {kind_str!r}; expected one of {', '.join(_VALID_KINDS)}."
        try:
            amount = int(amount_str.replace(",", "").replace("_", ""))
        except ValueError:
            return f"could not parse {amount_str!r} as a token count."
        # ``kind_str`` is now known to be one of _VALID_KINDS.
        return cast(BudgetKind, kind_str), amount
    return "usage: /budget extend N  OR  /budget extend KIND N"


@register(
    "budget",
    summary="Show token budgets, or extend one for this session",
    usage="/budget [extend [KIND] N]",
)
def budget_handler(ctx: SlashContext, args: str) -> SlashResult:
    args = args.strip()
    if not args:
        _print_state(ctx)
        return Handled()

    verb, _, rest = args.partition(" ")
    if verb.lower() != "extend":
        ctx.console.print(f"[red]unknown /budget verb:[/red] {verb!r}  [dim](valid: extend)[/dim]")
        return Handled()

    tracker = ctx.usage_tracker
    if tracker is None:
        ctx.console.print("[dim]no usage tracker available in this context[/dim]")
        return Handled()

    parsed = _parse_extend_args(rest.strip())
    if isinstance(parsed, str):
        ctx.console.print(f"[red]{parsed}[/red]")
        return Handled()
    kind, amount = parsed
    if amount <= 0:
        ctx.console.print("[red]extend amount must be positive.[/red]")
        return Handled()

    try:
        new_limit = tracker.extend(kind, amount)
    except ValueError as exc:
        ctx.console.print(f"[red]{exc}[/red]")
        return Handled()
    ctx.console.print(
        f"[green]✓[/green] extended [bold]{kind}[/bold] by "
        f"[bold]{amount:,}[/bold] tokens — new limit "
        f"[bold]{new_limit:,}[/bold]."
    )
    return Handled()


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
