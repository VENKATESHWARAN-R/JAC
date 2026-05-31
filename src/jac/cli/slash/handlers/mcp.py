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

Enable/disable/reload delegate to the control plane (``ctx.controller``),
which persists the change *and* rebuilds Gru in place (no model switch) so
the reused :class:`MCPCapability`'s ``get_toolset`` is re-consulted against
its live catalog. The web surface drives the identical verbs.
"""

from __future__ import annotations

from rich.table import Table

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.render import render_action
from jac.cli.slash.result import Handled, SlashResult

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
    if ctx.controller is None:  # pragma: no cover - always wired in the REPL
        ctx.console.print("[yellow]control plane is not wired into this session[/yellow]")
        return Handled()
    render_action(ctx, ctx.controller.reload_mcp())
    return Handled()


def _handle_toggle(ctx: SlashContext, sub: str, name: str) -> SlashResult:
    if not name:
        ctx.console.print(f"[dim]usage:[/dim] /mcp {sub} NAME")
        return Handled()
    if ctx.controller is None:  # pragma: no cover - always wired in the REPL
        ctx.console.print("[yellow]control plane is not wired into this session[/yellow]")
        return Handled()
    # The control plane validates (unknown server / already-in-state), persists
    # the flag, and rebuilds Gru so the change takes effect immediately.
    render_action(ctx, ctx.controller.set_mcp_enabled(name, enabled=sub == "enable"))
    return Handled()
