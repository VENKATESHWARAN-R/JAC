"""Render a :class:`~jac.runtime.control.ControlResult` to the REPL console.

The control-plane verbs (``/model``, ``/profile``, ``/mcp``) are surface-
agnostic: they mutate the runtime and return a plain ``ControlResult``. These
helpers are the CLI's *rendering half* — they turn that result into the styled
output the REPL has always shown, keeping the verbs free of any console code.
"""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.runtime.control import ControlResult


def render_switch(ctx: SlashContext, result: ControlResult) -> None:
    """Render a model/profile switch result (success line / failure panel)."""
    if not result.ok:
        from rich.panel import Panel

        fallback = (
            f"staying on profile [bold]{ctx.profile_name}[/bold]"
            if ctx.profile_name is not None
            else "staying on the current model"
        )
        ctx.console.print(
            Panel.fit(
                f"[yellow]switch failed[/yellow]\n\n{result.message}\n\n{fallback}",
                border_style="yellow",
            )
        )
        return

    data = result.data or {}
    model = data.get("model", "")
    profile = data.get("profile")
    summary = f"[green]✓[/green] switched to [bold]{model}[/bold]"
    # ctx.profile_name is the *pre-switch* profile (the SlashContext was built
    # before the verb ran), so this correctly detects a profile change.
    if profile is not None and profile != ctx.profile_name:
        summary += f"  [dim](profile: {profile})[/dim]"
    elif profile is None:
        summary += "  [dim](ad-hoc, no profile)[/dim]"
    ctx.console.print(summary)


def render_action(ctx: SlashContext, result: ControlResult) -> None:
    """Render a plain action result (e.g. MCP enable/disable/reload)."""
    if result.ok:
        ctx.console.print(f"[green]✓[/green] {result.message}")
    else:
        ctx.console.print(f"[red]{result.message}[/red]")
