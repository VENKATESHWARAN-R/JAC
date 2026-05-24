"""``/a2a peers`` — list the merged profile + session A2A peers."""

from __future__ import annotations

from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a._shared import auth_label, desc_tail
from jac.cli.slash.result import Handled, SlashResult


def handle(ctx: SlashContext) -> SlashResult:
    """Render the merged A2A peers view (profile + session) with provenance tags.

    Shows session-scoped peers (from ``/a2a peer add``) with a ``[session]``
    tag and profile-defined peers with ``[profile]``. When a session peer
    shadows a profile peer of the same name, the profile one is rendered
    greyed-out so the operator sees the override is intentional.
    """
    cap = ctx.a2a
    assert cap is not None
    profile_peers = cap.profile_peers
    session_peers = cap.session_peers

    if not profile_peers and not session_peers:
        ctx.console.print("[dim]A2A peers: (none configured)[/dim]")
        ctx.console.print(
            "[dim]for stable peers: add under [bold]a2a.peers.<name>[/bold] in "
            "your profile YAML ([bold]jac profiles edit NAME[/bold]).[/dim]"
        )
        ctx.console.print(
            "[dim]for ephemeral peers: [bold]/a2a peer add NAME URL ...[/bold] "
            "(this session only).[/dim]"
        )
        return Handled()

    ctx.console.print(f"[bold]A2A peers[/bold] [dim](profile: {ctx.profile_name})[/dim]")

    all_names = sorted(set(profile_peers) | set(session_peers))
    name_col = max(len(n) for n in all_names)
    url_col = max(
        max((len(p.url) for p in profile_peers.values()), default=0),
        max((len(p.url) for p in session_peers.values()), default=0),
    )

    for name in all_names:
        in_session = name in session_peers
        in_profile = name in profile_peers
        peer = session_peers[name] if in_session else profile_peers[name]
        provenance = r"[cyan]\[session][/cyan]" if in_session else r"[blue]\[profile][/blue]"
        ctx.console.print(
            f"  [bold]{name:<{name_col}}[/bold]  {peer.url:<{url_col}}  "
            f"auth: {auth_label(peer)}  {provenance}{desc_tail(peer)}"
        )
        if in_session and in_profile:
            shadowed = profile_peers[name]
            ctx.console.print(
                f"  [dim]{'':<{name_col}}  {shadowed.url:<{url_col}}  "
                f"auth: {auth_label(shadowed, colored=False)}  [blue](shadowed profile)[/blue]"
                f"{desc_tail(shadowed)}[/dim]"
            )
    return Handled()
