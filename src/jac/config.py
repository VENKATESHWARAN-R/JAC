"""JAC runtime configuration.

Reads from a layered stack:

  CLI args > env vars > .env > <repo>/.agents/config.yaml >
  ~/.jac/config.yaml > <package>/defaults.yaml

The layering is plumbed in :mod:`jac.workspace.config_loader`. Required
values (no default in code) raise ``JacConfigError`` at the point of use —
see CLAUDE.md "Fail-first, no hardcoding".

``Settings()`` is constructed lazily via :func:`get_settings` so that
:func:`jac.workspace.bootstrap.ensure_user_workspace` can run first and
create the YAML files this loader will read.
"""

from __future__ import annotations

from functools import cache

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from jac.workspace.config_loader import jac_config_sources


class Settings(BaseSettings):
    """Top-level JAC configuration.

    Keep this class small. New tunables go here, not into ad-hoc env reads
    scattered across modules.
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
    the ``--model`` CLI flag, or ``model = "..."`` in any layered config file.

    No default is hardcoded (fail-first principle). See ``.env.template``."""

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

    Constructed lazily so workspace bootstrap can create missing YAML files
    first. Tests can call :func:`reset_settings_cache` to force a reload.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings — useful in tests after changing the env."""
    get_settings.cache_clear()
