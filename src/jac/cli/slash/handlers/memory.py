"""``/memory`` — show what Gru has stored via ``remember`` (user + project).

Read-only window onto the two JAC-managed ``memory.md`` files. Mostly a
convenience for ``forget``: it prints each entry's prose verbatim (audit
comments stripped) so the user can copy the exact text back into a
``forget`` call without opening the file by hand.

    /memory          # both scopes
    /memory user     # ~/.jac/memory.md only
    /memory project  # <repo>/.agents/memory.md only
"""

from __future__ import annotations

from rich.console import Console

from jac.capabilities.memory import MemoryScope, read_memory_entries
from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, SlashResult

_SCOPE_LABELS: dict[MemoryScope, str] = {
    "user": "User memory",
    "project": "Project memory",
}


def _render_scope(console: Console, scope: MemoryScope) -> None:
    path, sections = read_memory_entries(scope)
    total = sum(len(entries) for entries in sections.values())
    label = _SCOPE_LABELS[scope]

    if not path.is_file():
        console.print(f"[bold]{label}[/bold] [dim]({path}) — not created yet[/dim]")
        return
    if total == 0:
        console.print(f"[bold]{label}[/bold] [dim]({path}) — empty[/dim]")
        return

    console.print(f"[bold]{label}[/bold] [dim]({path})[/dim]")
    for title, entries in sections.items():
        if not entries:
            continue
        console.print(f"  [cyan]{title}[/cyan]")
        for entry in entries:
            console.print(f"    • {entry}")


@register(
    "memory",
    summary="Show stored memory entries (user + project)",
    usage="/memory [user|project]",
)
def memory_handler(ctx: SlashContext, args: str) -> SlashResult:
    arg = args.strip().lower()
    if arg not in ("", "user", "project"):
        ctx.console.print(
            f"[red]unknown scope:[/red] {arg}  [dim](use [bold]/memory[/bold], "
            "[bold]/memory user[/bold], or [bold]/memory project[/bold])[/dim]"
        )
        return Handled()

    scopes: tuple[MemoryScope, ...]
    if arg == "user":
        scopes = ("user",)
    elif arg == "project":
        scopes = ("project",)
    else:
        scopes = ("user", "project")

    for i, scope in enumerate(scopes):
        if i:
            ctx.console.print()
        _render_scope(ctx.console, scope)

    ctx.console.print(
        "\n[dim]to drop an entry, ask Gru to [bold]forget[/bold] its exact text "
        "above (you'll approve the call).[/dim]"
    )
    return Handled()
