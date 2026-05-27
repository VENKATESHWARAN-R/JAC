"""``/spawns`` — list currently-active bidirectional sub-agents (D41).

A bidirectional sub-agent that's parked waiting for the main agent to
reply (via ``respond_to_sub_agent``) leaves an entry in
:data:`jac.runtime.sub_agent._pending_channels`. This handler renders
that registry as a Rich table so the user can see at a glance what's in
flight without scrolling back through events.

Sequential (non-bidirectional) spawns do NOT appear here — they don't
park; they run to completion inside a single tool call.
"""

from __future__ import annotations

from rich.table import Table

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.runtime.sub_agent import _pending_channels

_OBJECTIVE_TRUNCATE_AT = 60


@register(
    "spawns",
    summary="List currently-active bidirectional sub-agents",
    usage="/spawns",
)
def spawns_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args.strip():
        ctx.console.print("[dim]/spawns takes no arguments[/dim]")

    if not _pending_channels:
        ctx.console.print("[dim]no active sub-agents (none parked)[/dim]")
        return Handled()

    table = Table(
        title=f"active sub-agents ({len(_pending_channels)})",
        title_style="bold",
        title_justify="left",
        show_lines=False,
    )
    table.add_column("spawn_id", style="bold")
    table.add_column("tier")
    table.add_column("model", style="dim")
    table.add_column("round-trips")
    table.add_column("objective")

    for spawn_id, channel in sorted(_pending_channels.items()):
        resolved = channel.resolved
        tier = resolved.resolved if resolved is not None else "?"
        model = resolved.model if resolved is not None else "?"
        round_trips = f"{channel.round_trips}/{channel.cap}"
        objective = channel.objective or "[dim](no objective)[/dim]"
        if len(objective) > _OBJECTIVE_TRUNCATE_AT:
            objective = objective[: _OBJECTIVE_TRUNCATE_AT - 1] + "…"
        table.add_row(spawn_id, tier, model, round_trips, objective)

    ctx.console.print(table)
    ctx.console.print(
        "[dim]reply to a parked sub-agent by letting the agent call "
        "respond_to_sub_agent(spawn_id=..., answer=...). /exit cancels "
        "every parked worker.[/dim]"
    )
    return Handled()
