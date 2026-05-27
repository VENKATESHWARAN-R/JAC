"""Construct the Gru agent.

Phase 2a: Gru ships with filesystem, search, shell, **memory**, and
history capabilities. Risky tools (``write_file``, ``edit_file``,
``run_shell``, ``remember``) are approval-required; the CLI's
:class:`make_approval_handler` capability turns those deferred calls into
bus-mediated approval prompts.

Callers can pass ``extra_capabilities`` to attach hooks (event bus),
approval handlers, or future minion-factory capabilities without touching
this function. Set ``include_default_tools=False`` for headless / test
contexts that don't want filesystem access.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation

from jac.capabilities.context import make_context_capability
from jac.capabilities.filesystem import FilesystemCapability
from jac.capabilities.history import make_history_capability
from jac.capabilities.memory import MemoryCapability
from jac.capabilities.search import SearchCapability
from jac.capabilities.shell import ShellCapability
from jac.capabilities.sub_agent import (
    AskMainAgentCapability,
    RespondToSubAgentCapability,
    SubAgentToolCapability,
)
from jac.capabilities.web import WebCapability
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.runtime.events import EventBus
from jac.workspace.paths import load_prompt


def _default_tool_capabilities(
    *,
    bus: EventBus | None = None,
    summarizer_model: str | None = None,
    include_spawn: bool = True,
) -> list[Any]:
    """The standard tool + history capabilities every interactive JAC session gets.

    Includes :class:`ContextCapability` for dynamic AGENTS.md + memory.md
    re-reading — fresh ``remember()`` writes are visible to the next
    model request without rebuilding the agent.

    Args:
        bus: passed to the history capability so it can emit compaction events.
        summarizer_model: model id used for auto-compaction summarization
            (typically the active profile's ``small`` tier). When ``None``,
            compaction falls back to drop-only.
        include_spawn: whether to attach :class:`SubAgentToolCapability`.
            Set ``False`` when assembling the capability list for a
            **sub-agent** itself — depth cap = 1 (D40) is enforced
            structurally by leaving the spawn tool out of sub-agent
            toolsets.
    """
    base_prompt = load_prompt("gru_system").strip()
    if include_spawn and get_settings().cost.sub_agent_bidirectional:
        # Append the bidirectional-specific guidance so the model knows
        # how to recognise a sub-agent question and call respond_to_sub_agent.
        # The tool is gated by the same flag below; we never tell the model
        # about a tool it doesn't have.
        base_prompt = base_prompt + "\n\n" + load_prompt("gru_bidirectional").strip()

    caps: list[Any] = [
        Instrumentation(),
        make_context_capability(base_prompt),
        FilesystemCapability(),
        SearchCapability(),
        ShellCapability(),
        MemoryCapability(),
        WebCapability(),
        make_history_capability(bus=bus, summarizer_model=summarizer_model),
    ]
    if include_spawn:
        caps.append(SubAgentToolCapability())
        # D41: respond_to_sub_agent is the main-agent reply to a paused
        # sub-agent question. Only attached when the flag is on — the
        # tool is meaningless without the matching ask_main_agent on the
        # sub-agent side.
        if get_settings().cost.sub_agent_bidirectional:
            caps.append(RespondToSubAgentCapability())
    return caps


def sub_agent_capabilities(
    allowed_tools: list[str] | None = None,
    *,
    channel: Any = None,
) -> list[Any]:
    """Capability list a spawned sub-agent receives.

    Mirrors :func:`_default_tool_capabilities` minus
    :class:`SubAgentToolCapability` (depth cap = 1) and minus the
    history capability (sub-agents are short-lived; compaction is
    unnecessary overhead). The ``allowed_tools`` arg is reserved for
    future filtering at the toolset level — Phase B accepts the param
    for API stability but doesn't yet filter.

    Args:
        allowed_tools: reserved; honored in a follow-up that filters toolsets.
        channel: D41 bidirectional comms channel. When provided AND the
            ``cost.sub_agent_bidirectional`` flag is on, attaches
            :class:`AskMainAgentCapability` so the sub-agent can call
            ``ask_main_agent``. The channel itself is threaded to the tool
            via a contextvar — this argument's presence is the toggle.
    """
    _ = allowed_tools  # reserved; honored in a follow-up that filters toolsets
    caps: list[Any] = [
        Instrumentation(),
        make_context_capability(load_prompt("gru_system").strip()),
        FilesystemCapability(),
        SearchCapability(),
        ShellCapability(),
        MemoryCapability(),
        WebCapability(),
    ]
    # D41: ask_main_agent only goes into the sub-agent's toolset when
    # both the flag is on AND the spawn was started via the bidirectional
    # path (which provides the channel). Either condition off → tool stays
    # out, and the sub-agent literally cannot ask.
    if channel is not None and get_settings().cost.sub_agent_bidirectional:
        caps.append(AskMainAgentCapability())
    return caps


def build_gru(
    model_override: str | None = None,
    extra_capabilities: Sequence[Any] | None = None,
    include_default_tools: bool = True,
    *,
    bus: EventBus | None = None,
    summarizer_model: str | None = None,
) -> Agent[None, str]:
    """Build the Gru agent.

    Args:
        model_override: optional model id (e.g. ``"anthropic:claude-opus-4-6"``).
            Falls back to the layered config (env / project / user / package).
            **No package default** — fail-first if nothing is configured.
        extra_capabilities: capabilities to attach **before** the default tools.
            The CLI passes its ``Hooks`` and approval-handler capabilities here.
        include_default_tools: when ``True`` (default), attaches filesystem,
            search, and shell capabilities. Set ``False`` for tests or headless
            contexts that don't need filesystem access.
        bus: event bus passed to the default history capability so it can
            emit compaction events. ``None`` in headless / test contexts.
        summarizer_model: model id used by the history capability for
            auto-compaction (typically the active profile's ``small`` tier).
            ``None`` falls back to drop-only compaction.

    Returns:
        A ready-to-run ``Agent``.

    Raises:
        JacConfigError: if no model is configured anywhere in the layered config.
    """
    model = model_override or get_settings().model
    if not model:
        raise JacConfigError(
            "No model is configured. Set one of: "
            "(1) JAC_MODEL in your environment (see .env.template), "
            "(2) the --model flag on the CLI, "
            '(3) `model: "..."` in ~/.jac/config.yaml, '
            "or (4) run `jac init` for an interactive setup wizard."
        )
    capabilities: list[Any] = list(extra_capabilities or [])
    if include_default_tools:
        capabilities.extend(_default_tool_capabilities(bus=bus, summarizer_model=summarizer_model))
    return Agent(model, capabilities=capabilities)
