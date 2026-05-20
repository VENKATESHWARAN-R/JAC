"""JAC runtime configuration.

Reads from a layered stack:

  CLI args > env vars > .env > <repo>/.agents/config.yaml >
  ~/.jac/config.yaml > <package>/defaults.yaml

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
