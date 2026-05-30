"""JAC runtime configuration.

Reads from a layered stack:

  CLI args > env vars > .env > <repo>/.agents/config.yaml >
  ~/.jac/config.yaml > <package>/data/defaults.yaml

The layering is plumbed in :mod:`jac.workspace.config_loader`. Required
values (no default in code) raise ``JacConfigError`` at the point of use —
see CLAUDE.md "Fail-first, no hardcoding".

``Settings()`` is constructed lazily via :func:`get_settings` so that
:func:`jac.workspace.bootstrap.ensure_user_workspace` (and profile activation,
which sets ``JAC_MODEL``) can run first.
"""

from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from jac.workspace.config_loader import jac_config_sources

SecretBackendName = Literal["keyring", "dotenv", "env-only"]

CompactionStrategy = Literal["auto", "sliding", "manual"]
"""How JAC keeps the context within budget.

- ``auto`` — at ``auto_compact_pct`` the oldest slice is summarized via the
  small-tier model and replaced with a synthetic summary message. ``/compact``
  is also available to force this early.
- ``sliding`` — never summarizes automatically. When the history exceeds the
  budget, the oldest user/model turns are dropped from what's *sent* to the
  model (the on-disk session is untouched — it's a send-time window only).
  The status bar shows a red overflow marker while trimming is active.
- ``manual`` — JAC never compacts on its own. ``/compact`` is the only lever;
  the REPL still refuses a turn at ``refuse_pct`` so you can't silently blow
  past the budget.
"""

MAX_CONTEXT_CEILING = 512_000
"""Hard ceiling for any context budget (the 512k "power of two" easter egg —
512 = 2⁹ in thousands). Configured budgets above this raise; ``/context``
overrides clamp down to it with a notice."""


class SecretsSettings(BaseModel):
    """Where JAC stores credentials. Configured under ``secrets:`` in YAML."""

    backend: SecretBackendName = "keyring"


class CompactionSettings(BaseModel):
    """History-compaction thresholds (D20).

    Compaction operates against a **user-configurable budget**, not the
    model's published context window — recent models advertise 1M+ but
    quality typically degrades past ~200-300k. ``max_context_tokens``
    defaults to 256k (2⁸ thousand); bump it per-model via
    ``model_context_tokens`` up to the 512k ceiling, or lower it for cheaper
    models. Pcts apply against the *resolved* budget (see
    :func:`resolve_context_budget`).
    """

    strategy: CompactionStrategy = "auto"
    """How to keep within budget: ``auto`` (summarize), ``sliding`` (drop
    oldest turns at send time), or ``manual`` (only ``/compact``). See
    :data:`CompactionStrategy`."""

    max_context_tokens: int = 256_000
    """The default "useful" context budget Gru runs against, when the active
    model has no entry in :attr:`model_context_tokens`. The compaction ladder
    is measured as a percentage of the *resolved* budget — not the model's
    raw window."""

    model_context_tokens: dict[str, int] = Field(default_factory=dict)
    """Optional per-model budget overrides keyed by model id (e.g.
    ``{"anthropic:claude-opus-4-8": 400000}``). When the active model has an
    entry it wins over :attr:`max_context_tokens`. Nothing is hardcoded — this
    is purely opt-in user config. Values are capped at the 512k ceiling."""

    warn_pct: int = 60
    """At this percent of the budget, emit a :class:`CompactionWarning`."""

    auto_compact_pct: int = 70
    """At this percent, auto-summarize the oldest slice via the small-tier model."""

    refuse_pct: int = 85
    """At this percent, refuse the next user turn — the user must ``/clear``
    or otherwise free space. Caught pre-flight in the REPL."""

    target_pct_after_compact: int = 50
    """Auto-compaction shrinks the kept history until estimated size ≤ this
    percent of the budget, then stops."""

    @field_validator("max_context_tokens")
    @classmethod
    def _check_ceiling(cls, v: int) -> int:
        if v > MAX_CONTEXT_CEILING:
            raise ValueError(
                f"max_context_tokens={v} exceeds the {MAX_CONTEXT_CEILING} ceiling "
                f"(512k). Quality degrades well before this on every current model."
            )
        return v

    @field_validator("model_context_tokens")
    @classmethod
    def _check_model_ceiling(cls, v: dict[str, int]) -> dict[str, int]:
        for model, tokens in v.items():
            if tokens > MAX_CONTEXT_CEILING:
                raise ValueError(
                    f"model_context_tokens[{model!r}]={tokens} exceeds the "
                    f"{MAX_CONTEXT_CEILING} ceiling (512k)."
                )
        return v


class BudgetSettings(BaseModel):
    """Token budgets (D25). Cost guardrail against paid providers.

    Three independent knobs, all defaulting to ``None`` — budgets are
    **opt-in only**. No surprise hard-stops on first run. Set any of them
    in YAML or via env (``JAC_BUDGET__SESSION_TOTAL_TOKENS=200000``) to
    activate. When a knob is ``None`` its threshold checks are skipped.

    The status bar shows a ``bud:`` segment only when at least one knob is
    set. ``warn_pct``/``hardstop_pct`` apply uniformly across knobs.
    """

    session_input_tokens: int | None = None
    """Cap on cumulative *input* tokens for the current session."""

    session_total_tokens: int | None = None
    """Cap on cumulative input + output tokens for the current session."""

    project_total_tokens: int | None = None
    """Cap on cumulative input + output tokens across every session in
    this project — summed from ``<repo>/.agents/usage.jsonl``."""

    warn_pct: int = 80
    """Threshold (% of budget) at which :class:`BudgetWarning` fires once."""

    hardstop_pct: int = 100
    """Threshold at which the next user turn is pre-flight refused."""

    @field_validator(
        "session_input_tokens",
        "session_total_tokens",
        "project_total_tokens",
    )
    @classmethod
    def _reject_non_positive(cls, v: int | None) -> int | None:
        # A budget knob is either ``None`` (unset → unlimited) or a positive
        # cap. ``0`` or a negative number is almost always a typo that would
        # silently mean "unlimited" downstream (usage.py treats ``<= 0`` as
        # "no budget") — the opposite of the user's intent. Fail loud instead.
        if v is not None and v <= 0:
            raise ValueError(
                f"budget token caps must be positive (got {v}). Leave a knob "
                "unset (omit it, or null in YAML) for unlimited; a value of 0 "
                "is rejected so it can't be mistaken for a limit."
            )
        return v


_DEFAULT_SUMMARIZE_PROMPT = """\
You are compressing the output of a developer tool so another AI agent can
keep working without reading the raw output. The agent will act on your
summary directly, so it must be faithful — losing a fact here means the
agent acts on incomplete information.

Preserve every fact the agent might need to act on:

- exit codes, error messages, file paths, line numbers
- counts (tests passed/failed, files changed)
- any URL, identifier, or token mentioned
- the first ~200 chars of any stack trace verbatim

Drop only noise: repeated lines, decorative banners, progress bars, ANSI
codes, irrelevant warnings.

Rules:
- Report only what the output actually contains. Do not infer, guess, or add
  commentary the output doesn't support.
- If the output is mostly structured (JSON, table), keep that structure.
- Stay under 600 words.
- Output the summary only — no preamble like "Here is the summary".

Tool: {tool_name}
Original output ({original_tokens} tokens):
---
{output}
---

Summary:"""


class CostSettings(BaseModel):
    """Tool-result post-processor knobs (Phase A.1).

    When a tool opted into summarization via ``@jac_tool(summarizable=True)``
    returns output above ``tool_result_threshold_tokens``, JAC routes the
    result through the active profile's *small* tier model and returns the
    summary in place of the raw output. The original is saved to disk so
    the agent can re-read it via ``read_file`` if needed.

    Summarization is skipped (passthrough) when:

    - no ``small`` tier is configured in the active profile, or
    - the small-tier model isn't strictly cheaper per output token than
      the current tier (pricing lookup via :mod:`jac.providers.registry`).

    Use ``no_summarize_tools`` to force-off a specific tool even when its
    decorator says ``summarizable=True``; use ``summarize_tools`` to force-on
    a tool whose decorator default is ``False``.
    """

    tool_result_threshold_tokens: int = 8000
    """Outputs over this estimated token count get routed through the summarizer."""

    no_summarize_tools: list[str] = Field(default_factory=list)
    """Tool names to force-skip summarization for. User override."""

    summarize_tools: list[str] = Field(default_factory=list)
    """Tool names to force-summarize even if the decorator says no. User override."""

    summarize_prompt_template: str = _DEFAULT_SUMMARIZE_PROMPT
    """Prompt sent to the small-tier model. Receives ``tool_name``,
    ``original_tokens``, ``output`` via ``str.format``."""

    sub_agent_bidirectional: bool = True
    """Bidirectional sub-agent ↔ main-agent comms (D41).

    When ``True``, spawned sub-agents get an ``ask_main_agent`` tool and
    the main agent gets ``respond_to_sub_agent``. The sub-agent can pause
    mid-run, ask one focused clarifying question, and resume on the main
    agent's reply. Hard cap is **5 round-trips per spawn**; a sixth call
    returns a graceful "finalize with what you have" directive to the
    sub-agent rather than raising — so the spawn always produces a
    coherent final answer even if the conversation runs long.

    Default ``True`` since v0.4.x — the UX validation pass on 2026-05-28
    confirmed the happy paths and the per-question cost is bounded by
    the 5-round-trip cap. Set to ``False`` if you'd rather sub-agents
    finalize with what they have rather than pause for clarification."""


class Settings(BaseSettings):
    """Top-level JAC configuration.

    Profile model selection happens through the ``JAC_MODEL`` env var set by
    :func:`jac.secrets.apply_profile_env`. See :mod:`jac.profiles` for
    profile management.
    """

    model_config = SettingsConfigDict(
        env_prefix="JAC_",
        env_nested_delimiter="__",  # JAC_SECRETS__BACKEND=...
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow a field literally named ``model`` without pydantic's namespace warning.
        protected_namespaces=(),
    )

    model: str | None = None
    """Active model identifier. Normally set by profile activation; can be
    overridden via ``JAC_MODEL`` env or the ``--model`` CLI flag.

    No default is hardcoded (fail-first principle). See ``.env.template``."""

    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    """Secrets backend configuration. Defaults to OS keyring."""

    compaction: CompactionSettings = Field(default_factory=CompactionSettings)
    """History-compaction thresholds (D20). Override per-key via env
    ``JAC_COMPACTION__MAX_CONTEXT_TOKENS=400000`` or the ``compaction:``
    block in ``~/.jac/config.yaml`` / ``<repo>/.agents/config.yaml``."""

    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    """Token budgets (D25). Opt-in only — see :class:`BudgetSettings`."""

    cost: CostSettings = Field(default_factory=CostSettings)
    """Tool-result post-processor (Phase A.1). See :class:`CostSettings`."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Pydantic calls this when building Settings(). We plug in our YAML
        # layers (project → user → package) between dotenv and file secrets.
        return jac_config_sources(
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


@cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

    Constructed lazily so workspace bootstrap and profile activation can
    write the env first. Tests can call :func:`reset_settings_cache`.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings — useful in tests after changing the env."""
    get_settings.cache_clear()


# ---------- session-scoped context budget override (``/context <N>``) ----------

_session_context_override: int | None = None
"""Per-session budget set via ``/context``. Beats config; reset on session end.
Module-level (not on the cached Settings singleton, which we never mutate)."""


def set_session_context_budget(tokens: int | None) -> int | None:
    """Set (or clear, with ``None``) the session context budget override.

    Returns the value actually stored — clamped to the 512k ceiling so a
    fat-fingered ``/context 9000000`` lands somewhere sane. The caller reports
    the clamp to the user (we never silently swallow it)."""
    global _session_context_override
    if tokens is None:
        _session_context_override = None
        return None
    _session_context_override = min(tokens, MAX_CONTEXT_CEILING)
    return _session_context_override


def get_session_context_override() -> int | None:
    """Return the active session budget override, or ``None`` if unset."""
    return _session_context_override


def resolve_context_budget(model_id: str | None = None) -> int:
    """Resolve the effective context budget, highest precedence first:

    1. session override (``/context <N>``),
    2. per-model entry in ``compaction.model_context_tokens``,
    3. ``compaction.max_context_tokens`` default.

    ``model_id`` defaults to the active ``settings.model``."""
    if _session_context_override is not None:
        return _session_context_override
    settings = get_settings()
    model = model_id if model_id is not None else settings.model
    if model:
        per_model = settings.compaction.model_context_tokens.get(model)
        if per_model:
            return per_model
    return settings.compaction.max_context_tokens
