"""``/mode`` — switch interaction mode (D23): normal · plan · accept-edits.

- ``/mode`` — show the current mode and the choices.
- ``/mode plan`` — read-only: every state-changing tool call is auto-denied;
  Gru plans instead of executing.
- ``/mode accept-edits`` — file writes/edits auto-approve; shell and
  everything else still prompt.
- ``/mode normal`` — default HITL: every risky call prompts.

Setting a mode rebuilds Gru in place via the control plane so the matching
system-prompt guidance is applied. YOLO is intentionally absent — per D43 it
ships only with sandboxing (v2).
"""

from __future__ import annotations

from typing import cast

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult
from jac.runtime.modes import MODES, Mode, get_mode, set_mode

_DESC = {
    "normal": "default — every risky tool call prompts for approval",
    "plan": "read-only — state changes are blocked; Gru plans instead of acting",
    "accept-edits": "file writes/edits auto-approve; shell + the rest still prompt",
}


def _show(ctx: SlashContext) -> SlashResult:
    current = get_mode()
    ctx.console.print(f"[bold]mode:[/bold] {current} [dim]— {_DESC[current]}[/dim]")
    others = [m for m in MODES if m != current]
    ctx.console.print(f"[dim]switch with [bold]/mode {'|'.join(others)}[/bold][/dim]")
    return Handled()


@register(
    "mode",
    summary="Switch interaction mode (normal/plan/accept-edits)",
    usage="/mode [normal|plan|accept-edits]",
)
def mode_handler(ctx: SlashContext, args: str) -> SlashResult:
    arg = args.strip().lower()
    if not arg:
        return _show(ctx)

    if arg not in MODES:
        ctx.console.print(
            f"[red]unknown mode:[/red] {arg}  [dim](choose one of: {', '.join(MODES)})[/dim]"
        )
        return Handled()

    if arg == get_mode():
        ctx.console.print(f"[dim]already in {arg} mode[/dim]")
        return Handled()

    set_mode(cast(Mode, arg))  # arg validated against MODES above
    ctx.console.print(f"[green]✓[/green] {arg} mode [dim]— {_DESC[arg]}[/dim]")
    # Rebuild Gru via the control plane so the new mode's system-prompt
    # guidance is applied. set_mode (a module global) must run first — build_gru
    # reads it. A rebuild failure (e.g. no model bound) leaves the mode set.
    if ctx.controller is not None:
        outcome = ctx.controller.refresh_toolsets()
        if not outcome.ok:
            ctx.console.print(f"[yellow]{outcome.message}[/yellow]")
    return Handled()
