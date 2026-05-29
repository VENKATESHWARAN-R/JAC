"""Interaction modes (D23) — Plan, Accept-Edits, and the default Normal.

A *mode* is a session-scoped policy that changes two things without touching
the model or the message history:

1. **Approval override** — how the HITL handler resolves a deferred (risky)
   tool call *before* prompting the user. Plan Mode auto-**denies** every
   gated call (you're planning, not executing); Accept-Edits auto-**allows**
   file writes/edits (but still prompts for shell + everything else); Normal
   prompts for all of them, as always.
2. **Prompt addendum** — a short block appended to Gru's system prompt so the
   model *knows* the mode is active and behaves accordingly (e.g. "produce a
   plan, don't try to edit"). Applied at :func:`jac.runtime.gru.build_gru`
   time, so switching modes triggers a Gru rebuild (``RefreshToolsets``).

This is the ``ModeCapability`` base D23 calls for. It deliberately keeps both
knobs the design anticipated: ``approval_override`` (used today by Plan +
Accept-Edits, and tomorrow by YOLO's allow-all) and ``filter_capabilities``
(reserved — identity today). Plan Mode realises its "read-only subset" via
approval auto-deny rather than literally removing capabilities, because the
filesystem toolset bundles ``read_file`` with ``write_file``; auto-denying the
gated calls keeps reads available while blocking writes.

**YOLO is intentionally not exposed here.** The ``approval_override`` knob is
YOLO-ready (a mode returning ``"allow"`` for everything), but per D43 YOLO
ships only with ``pydantic-monty`` sandboxing + a Git-Clean Guard, which is
v2. We build the seam, not the door.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

Mode = Literal["normal", "plan", "accept-edits"]
"""The interaction modes a session can be in."""

ApprovalDecision = Literal["allow", "deny"]
"""A mode's override for a gated tool call: auto-allow, auto-deny, or (``None``
from :func:`approval_override`) defer to the normal HITL prompt."""

MODES: tuple[Mode, ...] = get_args(Mode)

# Tools Accept-Edits auto-approves. Everything else that reaches the approval
# handler (run_shell, delete_file, remember, spawn_sub_agent, …) still prompts.
_ACCEPT_EDITS_AUTO_APPROVE: frozenset[str] = frozenset({"write_file", "edit_file"})

# --- session-scoped current mode (process global, like the context override) ---

_current_mode: Mode = "normal"


def get_mode() -> Mode:
    """Return the active interaction mode."""
    return _current_mode


def set_mode(mode: Mode) -> None:
    """Set the active interaction mode. Raises ``ValueError`` on an unknown mode."""
    global _current_mode
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; valid: {', '.join(MODES)}")
    _current_mode = mode


def reset_mode() -> None:
    """Reset to Normal — called on REPL teardown so a new session starts clean."""
    global _current_mode
    _current_mode = "normal"


# --- the two ModeCapability knobs ---


def approval_override(tool_name: str, mode: Mode | None = None) -> ApprovalDecision | None:
    """Decide a gated tool call before the user is prompted.

    Returns ``"allow"`` (run without prompting), ``"deny"`` (refuse without
    prompting, feeding the model :func:`deny_message`), or ``None`` (no
    override — prompt the user as normal). ``mode`` defaults to the active mode.
    """
    m = mode if mode is not None else _current_mode
    if m == "plan":
        return "deny"
    if m == "accept-edits":
        return "allow" if tool_name in _ACCEPT_EDITS_AUTO_APPROVE else None
    return None


def filter_capabilities(caps: list[Any], mode: Mode | None = None) -> list[Any]:
    """Reserved D23 knob — returns ``caps`` unchanged today.

    Plan Mode blocks mutations via :func:`approval_override` (auto-deny) so
    that reads stay available, so no capability is actually removed yet. Kept
    as the seam for a future mode (e.g. YOLO) that wants literal capability
    filtering."""
    return caps


def deny_message(mode: Mode | None = None) -> str:
    """The tool-result message the model sees when a mode auto-denies a call."""
    m = mode if mode is not None else _current_mode
    if m == "plan":
        return (
            "Plan Mode is active: execution is blocked. Do not call tools that "
            "modify files, run commands, spawn sub-agents, or write memory. "
            "Instead, record this step in your plan with the plan/update_plan "
            "checklist and present the full plan to the user for approval. "
            "Exit Plan Mode (the user runs /mode normal) before executing."
        )
    return "This tool call was auto-denied by the active mode."


# --- prompt addendum (build-time) ---


def prompt_addendum(mode: Mode | None = None) -> str | None:
    """Return the system-prompt block for ``mode``, or ``None`` for Normal.

    Loaded from an overridable prompt file (``gru_plan_mode`` /
    ``gru_accept_edits``) so users can tune the wording like any other prompt.
    """
    m = mode if mode is not None else _current_mode
    prompt_name = {
        "plan": "gru_plan_mode",
        "accept-edits": "gru_accept_edits",
    }.get(m)
    if prompt_name is None:
        return None
    from jac.workspace.paths import load_prompt

    return load_prompt(prompt_name).strip()


# --- status bar display ---


def status_segment(mode: Mode | None = None) -> tuple[str, str] | None:
    """Return ``(label, prompt_toolkit_color)`` for the status bar, or ``None``
    when in Normal mode (the segment is hidden — the toolbar stays quiet in
    the common case)."""
    m = mode if mode is not None else _current_mode
    if m == "plan":
        return "plan", "ansiblue"
    if m == "accept-edits":
        return "accept-edits", "ansiyellow"
    return None
