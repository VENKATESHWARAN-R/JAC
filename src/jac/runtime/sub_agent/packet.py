"""Sub-agent data models: the task packet, parallel spawn spec, and result.

These are the Pydantic models the ``spawn_sub_agent`` / ``spawn_sub_agents``
tools validate against and return. The packet is the *full* briefing a
sub-agent receives (rendered into its system prompt); the result is the
deliberately-small payload that returns to the main agent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jac.runtime.sub_agent.tiers import TierName

ExitStatus = Literal["ok", "max_turns", "error"]


class SubAgentTaskPacket(BaseModel):
    """The full briefing the main agent gives a sub-agent (D36).

    Every field exists to constrain the sub-agent's behavior so the main
    agent can predict the result shape. The packet is rendered into the
    sub-agent's system prompt; together with the active capabilities it
    is *all* the context the sub-agent receives — no message history
    inheritance.
    """

    objective: str
    """Single-sentence statement of what success looks like."""

    success_criteria: list[str] = Field(default_factory=list)
    """Checklist the sub-agent should be able to mark complete."""

    relevant_paths: list[str] = Field(default_factory=list)
    """Files / directories the sub-agent should focus on. Advisory, not
    a sandbox — the filesystem capability still allows reads anywhere."""

    forbidden_actions: list[str] = Field(default_factory=list)
    """Explicit "don't do this" list. Surfaced verbatim in the prompt."""

    expected_output: str = ""
    """Shape of the answer the main agent expects back (e.g. "3-paragraph
    summary"). Helps the sub-agent stop talking once the goal is met."""

    allowed_tools: list[str] | None = None
    """Tool name allowlist, enforced at the Agent layer (R2). ``None`` means
    "all default sub-agent tools" (the main toolset minus ``spawn_sub_agent``).
    When set, the worker sees only the named tools plus an always-allowed
    control-plane set (``read_file``, ``ask_supervisor``) — a real sandbox.
    Name a tighter set to keep a worker on-task and off destructive verbs."""

    max_turns: int = 10
    """Hard cap on the sub-agent's model-call count. Prevents runaway
    loops; returns ``exit_status=max_turns`` when hit."""


class SubAgentSpawnSpec(BaseModel):
    """One entry in a :func:`spawn_sub_agents` batch (Phase E).

    Each spec is fully independent: per-spawn tier (with its own cascade),
    per-spawn packet, optional label. The model emits a list of these as a
    single tool call; the tool body runs all of them via ``asyncio.gather``.
    """

    tier: TierName
    """Tier for this spawn. Cascades up independently of sibling spawns."""

    label: str = ""
    """Short tag shown in the HITL approval line and the per-spawn result
    header. Optional — when empty the header omits it."""

    task_packet: SubAgentTaskPacket
    """Briefing for this spawn (same shape as the single-spawn tool)."""


class SubAgentResult(BaseModel):
    """Returned by ``spawn_sub_agent`` to the main agent.

    Kept small on purpose: the main agent's context shouldn't bloat with
    the sub-agent's intermediate work. If the caller needs detail, the
    Logfire span has it.
    """

    output: str
    """The sub-agent's final response, as a string."""

    turns_used: int
    """Number of model requests the sub-agent made."""

    resolved_tier: str
    """The tier actually used (after cascade)."""

    resolved_model: str
    """The model id actually used."""

    exit_status: ExitStatus = "ok"
