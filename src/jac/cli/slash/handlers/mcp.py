"""``/mcp`` — list / reload / enable / disable MCP servers (Phase F, D28).

Subcommands:

- ``/mcp list`` — table of every configured server: name, transport,
  status, approval/defer knobs, and which file it came from. Parse errors
  (bad JSON, invalid knobs) and the last build error (missing env var,
  unreachable server) surface here so a broken catalog is visible.
- ``/mcp reload`` — re-scan ``~/.jac/mcp.json`` + ``<repo>/.agents/mcp.json``
  and rebuild Gru so newly-added / edited servers take effect.
- ``/mcp enable NAME`` / ``/mcp disable NAME`` — flip a server on/off,
  persist to the owning file's ``jac`` block, and rebuild Gru.

Enable/disable/reload return :class:`RefreshToolsets` so the REPL rebuilds
Gru in place (no model switch) — the reused :class:`MCPCapability`'s
``get_toolset`` reads its live catalog on the rebuild.
"""

from __future__ import annotations

from rich.table import Table

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, RefreshToolsets, SlashResult

_USAGE = "/mcp {list | reload | enable NAME | disable NAME}"


@register("mcp", summary="Manage MCP tool servers (list / reload / enable / disable)", usage=_USAGE)
def mcp_handler(ctx: SlashContext, args: str) -> SlashResult:
    if ctx.mcp is None:
        ctx.console.print(
            "[yellow]MCP capability is not wired into this session[/yellow] "
            "[dim](this shouldn't happen in the REPL; report as a bug)[/dim]"
        )
        return Handled()

    sub, _, rest = args.partition(" ")
    sub = sub.strip().lower()
    rest = rest.strip()

    if not sub or sub == "list":
        return _handle_list(ctx)
    if sub == "reload":
        return _handle_reload(ctx)
    if sub in {"enable", "disable"}:
        return _handle_toggle(ctx, sub, rest)

    ctx.console.print(f"[red]unknown /mcp subcommand:[/red] {sub!r}  [dim](try {_USAGE})[/dim]")
    return Handled()


# --- subcommands -------------------------------------------------------


def _handle_list(ctx: SlashContext) -> SlashResult:
    assert ctx.mcp is not None
    catalog = ctx.mcp.catalog

    if not catalog.servers:
        ctx.console.print(
            "[dim]no MCP servers configured.[/dim] Add them to "
            "[bold]~/.jac/mcp.json[/bold] or [bold]<repo>/.agents/mcp.json[/bold] "
            "(standard mcpServers JSON), then run [bold]/mcp reload[/bold]."
        )
    else:
        table = Table(show_header=True, show_lines=False, pad_edge=False)
        table.add_column("server", style="bold")
        table.add_column("transport")
        table.add_column("status")
        table.add_column("approval")
        table.add_column("defer")
        table.add_column("source", style="dim")
        for name, srv in sorted(catalog.servers.items()):
            status = "[green]enabled[/green]" if srv.knobs.enabled else "[dim]disabled[/dim]"
            approval = "yes" if srv.knobs.requires_approval else "[yellow]no[/yellow]"
            defer = "yes" if srv.knobs.defer else "no"
            table.add_row(name, srv.transport, status, approval, defer, srv.source)
        ctx.console.print(table)

    for err in catalog.parse_errors:
        ctx.console.print(f"[red]config error:[/red] {err}")
    if ctx.mcp.last_build_error:
        ctx.console.print(f"[red]load error:[/red] {ctx.mcp.last_build_error}")
    return Handled()


def _handle_reload(ctx: SlashContext) -> SlashResult:
    assert ctx.mcp is not None
    ctx.mcp.reload()
    n = len(ctx.mcp.catalog.enabled)
    return RefreshToolsets(note=f"reloaded MCP catalog ({n} server{'s' if n != 1 else ''} enabled)")


def _handle_toggle(ctx: SlashContext, sub: str, name: str) -> SlashResult:
    assert ctx.mcp is not None
    if not name:
        ctx.console.print(f"[dim]usage:[/dim] /mcp {sub} NAME")
        return Handled()
    if name not in ctx.mcp.catalog.servers:
        available = ", ".join(sorted(ctx.mcp.catalog.servers)) or "(none)"
        ctx.console.print(
            f"[red]unknown MCP server:[/red] {name!r}  [dim](available: {available})[/dim]"
        )
        return Handled()

    want_enabled = sub == "enable"
    if ctx.mcp.catalog.servers[name].knobs.enabled == want_enabled:
        ctx.console.print(f"[dim]{name} is already {sub}d[/dim]")
        return Handled()

    try:
        ctx.mcp.set_enabled(name, want_enabled)
    except OSError as exc:
        ctx.console.print(f"[red]could not persist change:[/red] {exc}")
        return Handled()
    return RefreshToolsets(note=f"{sub}d MCP server [bold]{name}[/bold]")
