"""``/help`` — list every registered slash command."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import SLASH_COMMANDS, register
from jac.cli.slash.result import Handled, SlashResult


@register("help", summary="Show available slash commands", usage="/help")
def help_handler(ctx: SlashContext, args: str) -> SlashResult:
    if args:
        ctx.console.print("[dim]/help takes no arguments[/dim]")

    ctx.console.print("[bold]Slash commands[/bold]")
    usage_width = max((len(cmd.usage) for cmd in SLASH_COMMANDS.values()), default=0)
    for name in sorted(SLASH_COMMANDS):
        cmd = SLASH_COMMANDS[name]
        ctx.console.print(f"  [bold]{cmd.usage:<{usage_width}}[/bold]  [dim]{cmd.summary}[/dim]")
    ctx.console.print(
        "\n[dim]Anything not starting with [bold]/[/bold] goes to the model. "
        "Unknown slash commands are loud errors — no LLM fallthrough.[/dim]"
    )
    return Handled()
