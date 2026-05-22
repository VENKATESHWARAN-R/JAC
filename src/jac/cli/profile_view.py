"""Shared profile-listing renderer used by ``jac profiles`` and ``/profile``.

Single source of truth for the tier-aware listing format so the CLI
subcommand and the in-REPL slash command can't drift apart.
"""

from __future__ import annotations

from rich.console import Console

from jac.profiles import Profile


def render_profile_listing(
    console: Console,
    profiles: dict[str, Profile],
    *,
    default_name: str | None,
    active_name: str | None = None,
) -> None:
    """Print every profile with its tiers.

    Args:
        console: where to render.
        profiles: ``{name: Profile}`` to list.
        default_name: name to mark with ``(default)`` (from ``jac profiles list``).
        active_name: name to mark with ``(active)`` (from ``/profile`` in-REPL).
            ``default_name`` and ``active_name`` are independent — the default
            is what loads on next session start; the active is what's loaded
            *right now*.
    """
    if not profiles:
        console.print("[dim]no profiles configured. Run [bold]jac init[/bold] to create one.[/dim]")
        return

    console.print("[bold]Profiles:[/bold]")

    tier_col_width = max(
        (len(t) for p in profiles.values() for t in p.tiers),
        default=6,
    )

    for name, p in profiles.items():
        markers: list[str] = []
        if name == active_name:
            markers.append("[yellow](active)[/yellow]")
        if name == default_name:
            markers.append("[green](default)[/green]")
        marker = (" " + " ".join(markers)) if markers else ""
        env_part = f"  [dim]env: {', '.join(sorted(p.env))}[/dim]" if p.env else ""
        console.print(f"  [bold]{name}[/bold]{marker}{env_part}")
        for tier_name, models in p.tiers.items():
            active_tier = " [yellow]← active tier[/yellow]" if tier_name == p.active_tier else ""
            primary = models[0]
            alternates = f" [dim](+{len(models) - 1} alt)[/dim]" if len(models) > 1 else ""
            console.print(
                f"    [bold]{tier_name:<{tier_col_width}}[/bold]  "
                f"[dim]{primary}[/dim]{alternates}{active_tier}"
            )
