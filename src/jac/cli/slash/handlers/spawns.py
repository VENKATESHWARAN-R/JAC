"""``/spawns`` — list currently-suspended bidirectional sub-agents (Phase 4).

A bidirectional sub-agent that suspended on a question and is waiting for
the main agent to reply (via ``respond_to_sub_agent``) leaves an entry in
:data:`jac.runtime.sub_agent._pending_spawns`. This handler renders that
registry as a Rich table so the user can see at a glance what's in flight
without scrolling back through events.

Sequential (non-bidirectional) spawns do NOT appear here — they don't
suspend; they run to completion inside a single tool call.
"""

from __future__ import annotations

from rich.table import Table

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.runtime.sub_agent import _BIDIRECTIONAL_ROUND_TRIP_CAP, _pending_spawns

_OBJECTIVE_TRUNCATE_AT = 60


@register(
    "spawns",
    summary="List currently-active bidirectional sub-agents",
    usage="/spawns",
)
def spawns_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args.strip():
        ctx.console.print("[dim]/spawns takes no arguments[/dim]")

    if not _pending_spawns:
        ctx.console.print("[dim]no suspended sub-agents (none waiting)[/dim]")
        return Handled()

    table = Table(
        title=f"suspended sub-agents ({len(_pending_spawns)})",
        title_style="bold",
        title_justify="left",
        show_lines=False,
    )
    table.add_column("spawn_id", style="bold")
    table.add_column("tier")
    table.add_column("model", style="dim")
    table.add_column("round-trips")
    table.add_column("objective")

    for spawn_id, pending in sorted(_pending_spawns.items()):
        resolved = pending.resolved
        round_trips = f"{pending.round_trips}/{_BIDIRECTIONAL_ROUND_TRIP_CAP}"
        objective = pending.objective or "[dim](no objective)[/dim]"
        if len(objective) > _OBJECTIVE_TRUNCATE_AT:
            objective = objective[: _OBJECTIVE_TRUNCATE_AT - 1] + "…"
        table.add_row(spawn_id, resolved.resolved, resolved.model, round_trips, objective)

    ctx.console.print(table)
    ctx.console.print(
        "[dim]reply to a suspended sub-agent by letting the agent call "
        "respond_to_sub_agent(spawn_id=..., answer=...). /exit drops "
        "every suspended worker.[/dim]"
    )
    return Handled()
