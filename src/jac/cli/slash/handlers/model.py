"""``/model`` — switch the model Gru is running on.

Two forms:

- ``/model`` (no arg) — print a numbered list of every model across every
  tier of the active profile (tier-labeled, with ``(active)`` on the current
  one), then prompt for a selection. ``c`` cancels.
- ``/model PROVIDER:ID`` — ad-hoc one-session override; works even for models
  outside the profile's configured tiers (gateway / litellm routing, etc.).

Tier-based switching is intentionally out of scope: tiers are how Gru picks
models when spawning minions; humans pick concrete models.
"""

from __future__ import annotations

from rich.prompt import Prompt

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.render import render_switch
from jac.cli.slash.result import Handled, SlashResult


@register(
    "model",
    summary="Switch model (numbered picker, or pass an explicit PROVIDER:ID)",
    usage="/model [PROVIDER:ID]",
)
def model_handler(ctx: SlashContext, args: str) -> SlashResult:
    target = args.strip()
    if target:
        return _switch_to(ctx, target)
    return _interactive_picker(ctx)


def _switch_to(ctx: SlashContext, model_id: str) -> SlashResult:
    """Ad-hoc switch — no validation against the profile's tier lists."""
    if model_id == ctx.model_id:
        ctx.console.print(f"[dim]already on {model_id}[/dim]")
        return Handled()
    # Note when the model isn't in the configured tiers — gentle nudge, not a block.
    if ctx.profile is not None and model_id not in ctx.profile.all_models():
        ctx.console.print(
            f"[dim]note: {model_id} isn't in profile "
            f"{ctx.profile_name!r}'s tiers — ad-hoc switch only.[/dim]"
        )
    return _apply_switch(ctx, model_id)


def _apply_switch(ctx: SlashContext, model_id: str) -> SlashResult:
    """Drive the control plane's model switch and render the outcome.

    The controller owns the env snapshot/rollback + Gru rebuild and mutates
    the runtime in place; the REPL re-syncs its display from the runtime after
    dispatch. Same path the web surface drives.
    """
    if ctx.controller is None:  # pragma: no cover - always wired in the REPL
        ctx.console.print("[yellow]control plane is not wired into this session[/yellow]")
        return Handled()
    render_switch(ctx, ctx.controller.switch_model(model_id))
    return Handled()


def _interactive_picker(ctx: SlashContext) -> SlashResult:
    """No-arg path: enumerate tier models and prompt for a number."""
    if ctx.profile is None:
        ctx.console.print(
            "[yellow]no profile loaded[/yellow] — use [bold]/model PROVIDER:ID[/bold] "
            "for an ad-hoc switch."
        )
        return Handled()

    options: list[tuple[str, str]] = []  # (model_id, tier_name)
    for tier_name, models in ctx.profile.tiers.items():
        for model_id in models:
            options.append((model_id, tier_name))

    if not options:
        ctx.console.print(f"[yellow]profile {ctx.profile_name!r} has no models configured[/yellow]")
        return Handled()

    if len(options) == 1:
        only_id, only_tier = options[0]
        ctx.console.print(
            f"[dim]only one model configured ({only_id} [{only_tier}]). "
            "Use [bold]/model PROVIDER:ID[/bold] for an ad-hoc switch.[/dim]"
        )
        return Handled()

    ctx.console.print(f"[bold]Models in profile {ctx.profile_name!r}:[/bold]")
    id_col = max(len(mid) for mid, _ in options)
    for idx, (model_id, tier_name) in enumerate(options, start=1):
        marker = " [yellow](active)[/yellow]" if model_id == ctx.model_id else ""
        ctx.console.print(
            f"  [bold]{idx:>2}.[/bold] {model_id:<{id_col}}  [dim][{tier_name}][/dim]{marker}"
        )

    choices = [str(i) for i in range(1, len(options) + 1)] + ["c"]
    raw = Prompt.ask("\nEnter number, or 'c' to cancel", choices=choices, default="c")
    if raw == "c":
        ctx.console.print("[dim]cancelled[/dim]")
        return Handled()

    picked_id, _picked_tier = options[int(raw) - 1]
    if picked_id == ctx.model_id:
        ctx.console.print(f"[dim]already on {picked_id}[/dim]")
        return Handled()
    return _apply_switch(ctx, picked_id)
