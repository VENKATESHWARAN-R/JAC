"""Persistent bottom-toolbar status line (1.7.b).

Renders one line at the bottom of the prompt-toolkit prompt, always
visible, showing:

    profile:claude  ·  tier:medium (sonnet-4-5)  ·  branch:main*  ·  ctx:34%/200k  ·  session:...

The toolbar is a pure function of :class:`StatusState`, which the REPL
mutates whenever something interesting changes (turn completed, slash
command switched the model/profile/session, etc.). prompt-toolkit calls
the toolbar callable on every keystroke so updates appear without an
explicit redraw.

Git branch + dirty status is shelled out, but debounced to once every
5 seconds via :func:`_branch_segment` — shelling out per keystroke would
be wasteful and would visibly stutter when a key is held down.

The dark background is set via a ``Style`` override in the REPL (the
``bottom-toolbar`` class with ``noreverse bg:ansiblack``). The HTML here
only sets foreground colours against that dark canvas:

- Key labels           — ansiyellow  (Gru's colour; warm against dark bg)
- Normal values        — ansiwhite
- Secondary info       — ansibrightblack  (model short-name, session ID)
- Threshold escalation — ansiyellow → ansired  (ctx and budget warnings)

Context percentage thresholds:

- ``< warn_pct``      — ansiwhite (neutral)
- ``warn_pct..auto``  — ansiyellow (warning)
- ``auto..refuse``    — ansibrightred (urgent)
- ``>= refuse_pct``   — ansired (critical)
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from html import escape

from prompt_toolkit.formatted_text import HTML
from pydantic_ai.messages import ModelMessage

from jac.capabilities.history import estimate_tokens
from jac.config import get_settings
from jac.profiles import Profile

_BRANCH_DEBOUNCE_S = 5.0
"""Min seconds between git invocations. Keystroke-frequency shells would
be wasteful and stutter; 5s is plenty for a status indicator."""

_branch_last_checked: float = -1.0
_branch_value: str = ""
_branch_dirty: bool = False

# Subtle dot separator between toolbar segments — dimmed so it recedes.
_SEP = '  <style fg="ansibrightblack">·</style>  '


def _branch_status() -> tuple[str, bool]:
    """Return ``(branch, dirty)``; refresh at most once per ``_BRANCH_DEBOUNCE_S``.

    Outside a git repo, or when ``git`` isn't on PATH, returns ``("", False)``.
    """
    global _branch_last_checked, _branch_value, _branch_dirty
    now = time.monotonic()
    if _branch_last_checked >= 0 and (now - _branch_last_checked) <= _BRANCH_DEBOUNCE_S:
        return _branch_value, _branch_dirty
    try:
        _branch_value = (
            subprocess.check_output(
                ["git", "symbolic-ref", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            .decode()
            .strip()
        )
        _branch_dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                stderr=subprocess.DEVNULL,
                timeout=1,
            ).decode()
        )
    except Exception:
        _branch_value = ""
        _branch_dirty = False
    _branch_last_checked = now
    return _branch_value, _branch_dirty


def _reset_branch_cache() -> None:
    """Reset the debounce cache. For tests only."""
    global _branch_last_checked, _branch_value, _branch_dirty
    _branch_last_checked = -1.0
    _branch_value = ""
    _branch_dirty = False


@dataclass
class StatusState:
    """Mutable REPL state surfaced in the bottom toolbar.

    The REPL updates these fields whenever something interesting changes:
    after a turn (``message_history``), after a slash-driven session
    switch (``session_id``), after a Gru rebuild (``profile_name``,
    ``profile``, ``model_id``). The toolbar callable reads from here on
    every render so updates appear without explicit redraw plumbing.
    """

    model_id: str = ""
    session_id: str = ""
    profile_name: str | None = None
    profile: Profile | None = None
    message_history: list[ModelMessage] = field(default_factory=list)
    budget_pct: int | None = None
    """Highest-used percent across configured token budgets (D25). ``None``
    means no budget is configured — the toolbar hides the ``bud:`` segment.
    The REPL refreshes this after every turn via
    :meth:`jac.runtime.usage.UsageTracker.status_pct`."""


# ---------- pure helpers ----------


def tier_for_model(profile: Profile | None, model_id: str) -> str | None:
    """Return the tier name a model belongs to, or ``None`` for ad-hoc models.

    A model is "ad-hoc" if there's no active profile (``--model`` override)
    or the model id isn't listed under any tier in the active profile.
    """
    if profile is None:
        return None
    for tier_name, models in profile.tiers.items():
        if model_id in models:
            return tier_name
    return None


def short_model(model_id: str) -> str:
    """Strip everything before the last ``:`` or ``/`` for the toolbar display.

    Examples:
        anthropic:claude-sonnet-4-5 → claude-sonnet-4-5
        gateway/openai:gpt-4o       → gpt-4o
        plainmodel                  → plainmodel
    """
    last_colon = model_id.rfind(":")
    last_slash = model_id.rfind("/")
    pos = max(last_colon, last_slash)
    return model_id[pos + 1 :] if pos >= 0 else model_id


def _ctx_color(pct: int) -> str:
    """Color thresholds mirror the compaction ladder."""
    s = get_settings().compaction
    if pct >= s.refuse_pct:
        return "ansired"
    if pct >= s.auto_compact_pct:
        return "ansibrightred"
    if pct >= s.warn_pct:
        return "ansiyellow"
    return "ansiwhite"


def _format_ctx_segment(used: int, budget: int) -> str:
    pct = int((used / budget) * 100) if budget > 0 else 0
    color = _ctx_color(pct)
    budget_k = budget // 1000
    return (
        f'<style fg="ansiyellow">ctx:</style>'
        f'<style fg="{color}">{pct}%/{budget_k}k</style>'
    )


def _format_branch_segment(branch: str, dirty: bool) -> str:
    if not branch:
        return ""
    dirty_mark = '<style fg="ansiyellow">*</style>' if dirty else ""
    return (
        f'<style fg="ansiyellow">branch:</style>'
        f'<style fg="ansiwhite">{escape(branch)}</style>'
        f"{dirty_mark}"
    )


def _budget_color(pct: int) -> str:
    """Color thresholds mirror the budget ladder (D25)."""
    s = get_settings().budget
    if pct >= s.hardstop_pct:
        return "ansired"
    if pct >= s.warn_pct:
        return "ansiyellow"
    return "ansiwhite"


def _format_budget_segment(pct: int | None) -> str:
    """Hidden when no budget is configured (``pct is None``)."""
    if pct is None:
        return ""
    color = _budget_color(pct)
    return f'<style fg="ansiyellow">bud:</style><style fg="{color}">{pct}%</style>'


def _format_spawns_segment() -> str:
    """Show ``minions:N`` when any bidirectional sub-agent is parked
    (D41). Hidden when nothing is in flight so the toolbar stays quiet
    in the common case.

    Imports the channel registry lazily to avoid pulling the sub-agent
    runtime into the statusbar import graph at module load time."""
    from jac.runtime.sub_agent import _pending_channels

    count = len(_pending_channels)
    if count == 0:
        return ""
    return f'<style fg="ansiyellow">minions:</style><style fg="ansiyellow">{count}</style>'


def _format_model_segment(state: StatusState) -> str:
    short = escape(short_model(state.model_id)) if state.model_id else "?"
    tier = tier_for_model(state.profile, state.model_id)
    if tier:
        return (
            f'<style fg="ansiyellow">tier:</style>'
            f'<style fg="ansiwhite">{escape(tier)}</style> '
            f'<style fg="ansibrightblack">({short})</style>'
        )
    # No matching tier — either ad-hoc /model PROVIDER:ID or no profile.
    return f'<style fg="ansiyellow">model:</style><style fg="ansiwhite">{short}</style>'


def _format_profile_segment(state: StatusState) -> str:
    if not state.profile_name:
        return ""
    return (
        f'<style fg="ansiyellow">profile:</style>'
        f'<style fg="ansiwhite">{escape(state.profile_name)}</style>'
    )


def format_toolbar(state: StatusState) -> HTML:
    """Render the bottom toolbar as a prompt-toolkit ``HTML`` blob.

    Each segment function returns its content with no leading/trailing
    spaces — all spacing is handled here via ``_SEP``. Empty segments
    (hidden when their data is absent) are filtered before joining so
    separators never appear adjacent to nothing.
    """
    budget = get_settings().compaction.max_context_tokens
    used = estimate_tokens(state.message_history)
    branch, dirty = _branch_status()

    session_seg = (
        f'<style fg="ansiyellow">session:</style>'
        f'<style fg="ansibrightblack">{escape(state.session_id) or "?"}</style>'
    )
    segments = [
        _format_profile_segment(state),
        _format_model_segment(state),
        _format_branch_segment(branch, dirty),
        _format_ctx_segment(used, budget),
        _format_budget_segment(state.budget_pct),
        _format_spawns_segment(),
        session_seg,
    ]
    active = [s for s in segments if s]
    return HTML(" " + _SEP.join(active) + " ")
