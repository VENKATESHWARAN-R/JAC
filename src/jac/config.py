"""Runtime configuration. Reads from environment variables.

Env vars are prefixed with ``JAC_``. Example: ``JAC_MODEL=anthropic:claude-opus-4-6``.
A ``.env`` file in the current working directory is also honored.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level JAC configuration.

    Keep this small. New tunables go here, not into ad-hoc env reads scattered
    across modules.
    """

    model_config = SettingsConfigDict(
        env_prefix="JAC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow a field literally named ``model`` without pydantic's
        # ``model_*`` namespace warning.
        protected_namespaces=(),
    )

    model: str | None = None
    """Model identifier (``provider:name``). **Required** — set via ``JAC_MODEL``,
    the ``--model`` CLI flag, or ``model = "..."`` in a config file.

    No default is hardcoded in code (fail-first principle, see CLAUDE.md).
    See ``.env.template`` for example values."""


settings = Settings()
