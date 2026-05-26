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

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from jac.workspace.config_loader import jac_config_sources

SecretBackendName = Literal["keyring", "dotenv", "env-only"]


class SecretsSettings(BaseModel):
    """Where JAC stores credentials. Configured under ``secrets:`` in YAML."""

    backend: SecretBackendName = "keyring"


class CompactionSettings(BaseModel):
    """History-compaction thresholds (D20).

    Compaction operates against a **user-configurable budget**, not the
    model's published context window — recent models advertise 1M+ but
    quality typically degrades past ~200-300k. ``max_context_tokens``
    defaults to a conservative 200k; bump it if you trust your model with
    more, or lower it for cheaper models. Pcts apply against that budget.
    """

    max_context_tokens: int = 200_000
    """The "useful" context budget Gru runs against. Compaction ladder is
    measured as a percentage of this — not the model's raw window."""

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


_DEFAULT_SUMMARIZE_PROMPT = """\
You are summarizing the output of a developer tool for another AI agent
that needs to keep working on a task. Preserve every fact the agent might
need to act on:

- exit codes, error messages, file paths, line numbers
- counts (tests passed/failed, files changed)
- any URL, identifier, or token mentioned
- the first ~200 chars of any stack trace verbatim

Drop only noise: repeated lines, decorative banners, progress bars, ANSI
codes, irrelevant warnings. Stay under 600 words. If the output is mostly
structured (JSON, table), keep the structure.

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
