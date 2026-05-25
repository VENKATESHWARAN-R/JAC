"""``/a2a status`` — show A2A guest server running state.

Renders four blocks in order:

1. **server** — running / not running, URL, bind, auth, card URL.
2. **peers** — count of merged profile + session peers (or ``(none)``).
3. **inbound** — last 5 calls read from the tail of ``inbound.jsonl``.
4. (hints when nothing is running yet)

Reads only — never mutates capability state. Safe to call at any time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jac.cli.slash.context import SlashContext
from jac.cli.slash.result import Handled, SlashResult
from jac.workspace import paths

_LAST_N_CALLS = 5
_PREVIEW_MAX = 48


def handle(ctx: SlashContext) -> SlashResult:
    cap = ctx.a2a
    assert cap is not None

    _render_server_block(ctx, cap)
    _render_peers_block(ctx, cap)
    _render_inbound_block(ctx)
    return Handled()


def _render_server_block(ctx: SlashContext, cap: Any) -> None:
    if cap.server is None or not cap.server.is_running or cap.server.info is None:
        ctx.console.print("[bold]A2A server:[/bold] [dim](not running)[/dim]")
        ctx.console.print("[dim]start with: /a2a serve[/dim]")
        return

    from jac.capabilities.a2a.auth import redact_token

    info = cap.server.info
    auth_line = (
        "[red]disabled (--unsafe)[/red]"
        if info.unsafe
        else f"bearer [dim]({redact_token(info.token)})[/dim]"
    )
    ctx.console.print("[bold]A2A server:[/bold] running")
    ctx.console.print(f"  url:   [bold]{info.url}[/bold]")
    ctx.console.print(f"  bind:  {info.bind_host}:{info.port}")
    ctx.console.print(f"  auth:  {auth_line}")
    ctx.console.print(f"  card:  [dim]{info.url}/.well-known/agent-card.json[/dim]")


def _render_peers_block(ctx: SlashContext, cap: Any) -> None:
    profile_count = len(cap.profile_peers)
    session_count = len(cap.session_peers)
    total = len(cap.peers)  # merged view — accounts for shadowing

    if total == 0:
        ctx.console.print("[bold]A2A peers:[/bold]   [dim](none configured)[/dim]")
        return

    parts = [f"[bold]{total}[/bold] configured"]
    if profile_count and session_count:
        parts.append(f"[dim](profile: {profile_count}, session: {session_count})[/dim]")
    elif session_count:
        parts.append(f"[dim](session: {session_count})[/dim]")
    else:
        parts.append(f"[dim](profile: {profile_count})[/dim]")
    ctx.console.print(f"[bold]A2A peers:[/bold]   {' '.join(parts)}")
    ctx.console.print("[dim]  see /a2a peers for the full list[/dim]")


def _render_inbound_block(ctx: SlashContext) -> None:
    log_file = paths.project_a2a_inbound_log()
    records = _tail_jsonl(log_file, _LAST_N_CALLS)
    if not records:
        ctx.console.print("[bold]A2A inbound:[/bold] [dim](no calls recorded)[/dim]")
        return

    ctx.console.print(f"[bold]A2A inbound:[/bold] last {len(records)} call(s)")
    for record in records:
        ts = _short_ts(str(record.get("ts", "")))
        peer = str(record.get("peer_id", "?"))
        state = str(record.get("state", "?"))
        duration_ms = record.get("duration_ms", 0)
        preview = _truncate(str(record.get("message_preview", "")), _PREVIEW_MAX)
        state_color = "green" if state == "completed" else "red"
        ctx.console.print(
            f"  [dim]{ts}[/dim]  {peer}  "
            f"[{state_color}]{state}[/{state_color}]  "
            f"[dim]{duration_ms}ms[/dim]  {preview}"
        )


def _tail_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    """Return up to the last ``n`` decoded JSONL rows from ``path``.

    Best-effort: missing file → empty list. Malformed lines are skipped
    (mirrors :func:`load_project_baseline` discipline). The file is small
    (one line per inbound call, no rotation) so reading it whole is fine
    in practice — switch to seek-based tailing only when audit logs grow
    past a few MB in practice.
    """
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for raw in lines[-n * 2 :]:  # 2x cushion in case some lines fail to decode
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            records.append(entry)
    return records[-n:]


def _short_ts(ts: str) -> str:
    """Strip timezone + fractional seconds from an ISO-8601 timestamp.

    Inbound log writes :func:`audit.now_iso` which includes fractional
    seconds and a tz offset; for one-line status output we want just
    ``HH:MM:SS`` or ``YYYY-MM-DD HH:MM:SS``. Fall back to the raw value
    when parsing fails.
    """
    if not ts:
        return "?"
    # ISO format is ``YYYY-MM-DDTHH:MM:SS[.ffffff][±HH:MM]`` — after the
    # 'T' split we want just the first 8 chars of the time portion.
    if "T" in ts:
        date, _, rest = ts.partition("T")
        return f"{date} {rest[:8]}".strip()
    return ts


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
