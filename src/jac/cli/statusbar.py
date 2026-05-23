"""Persistent bottom-toolbar status line (1.7.b).

Renders one line at the bottom of the prompt-toolkit prompt, always
visible, showing:

    profile:claude  tier:medium (sonnet-4-5)  branch:main*  ctx:34%/200k  session:20260523T14-30-00

The toolbar is a pure function of :class:`StatusState`, which the REPL
mutates whenever something interesting changes (turn completed, slash
command switched the model/profile/session, etc.). prompt-toolkit calls
the toolbar callable on every keystroke so updates appear without an
explicit redraw.

Git branch + dirty status is shelled out, but debounced to once every
5 seconds via :class:`_BranchCache` — shelling out per keystroke would
be wasteful and would visibly stutter when a key is held down.

Context percentage colors mirror the compaction ladder:

- ``< warn_pct``      — neutral
- ``warn_pct..auto``  — yellow
- ``auto..refuse``    — orange
- ``>= refuse_pct``   — red
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


class _BranchCache:
    """Caches the current git branch + dirty state with a debounce.

    On every :meth:`get` the cache is refreshed only if the previous
    refresh was more than ``_BRANCH_DEBOUNCE_S`` seconds ago. All git
    calls are wrapped in ``try/except`` — when ``git`` isn't on PATH, or
    we're outside a repo, we return ``("", False)`` rather than crashing.
    """

    def __init__(self) -> None:
        self._last_checked: float = -1.0
        self._branch: str = ""
        self._dirty: bool = False

    def get(self) -> tuple[str, bool]:
        now = time.monotonic()
        if self._last_checked < 0 or (now - self._last_checked) > _BRANCH_DEBOUNCE_S:
            self._refresh()
            self._last_checked = now
        return self._branch, self._dirty

    def _refresh(self) -> None:
        try:
            self._branch = (
                subprocess.check_output(
                    ["git", "symbolic-ref", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                )
                .decode()
                .strip()
            )
        except Exception:
            self._branch = ""
            self._dirty = False
            return
        try:
            self._dirty = bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"],
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                ).decode()
            )
        except Exception:
            self._dirty = False


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
    branch_cache: _BranchCache = field(default_factory=_BranchCache)
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
    return f'<style fg="ansicyan">ctx:</style><style fg="{color}">{pct}%/{budget_k}k</style>'


def _format_branch_segment(branch: str, dirty: bool) -> str:
    if not branch:
        return ""
    dirty_mark = '<style fg="ansiyellow">*</style>' if dirty else ""
    return f'  <style fg="ansicyan">branch:</style>{escape(branch)}{dirty_mark}'


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
    return f'  <style fg="ansicyan">bud:</style><style fg="{color}">{pct}%</style>'


def _format_model_segment(state: StatusState) -> str:
    short = escape(short_model(state.model_id)) if state.model_id else "?"
    tier = tier_for_model(state.profile, state.model_id)
    if tier:
        return (
            f'<style fg="ansicyan">tier:</style>{escape(tier)} '
            f'<style fg="ansibrightblack">({short})</style>'
        )
    # No matching tier — either ad-hoc /model PROVIDER:ID or no profile.
    return f'<style fg="ansicyan">model:</style>{short}'


def _format_profile_segment(state: StatusState) -> str:
    if not state.profile_name:
        return ""
    return f'<style fg="ansicyan">profile:</style>{escape(state.profile_name)}  '


def format_toolbar(state: StatusState) -> HTML:
    """Render the bottom toolbar as a prompt-toolkit ``HTML`` blob."""
    budget = get_settings().compaction.max_context_tokens
    used = estimate_tokens(state.message_history)
    branch, dirty = state.branch_cache.get()

    parts = [
        " ",
        _format_profile_segment(state),
        _format_model_segment(state),
        _format_branch_segment(branch, dirty),
        "  ",
        _format_ctx_segment(used, budget),
        _format_budget_segment(state.budget_pct),
        "  ",
        f'<style fg="ansicyan">session:</style>'
        f'<style fg="ansibrightblack">{escape(state.session_id) or "?"}</style>',
    ]
    return HTML("".join(parts))
