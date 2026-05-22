"""Layered configuration source assembly.

Precedence (highest to lowest):

1. CLI args (via ``Settings(**init_kwargs)``)
2. Environment variables (``JAC_*``)
3. ``.env`` file in CWD
4. Project config: ``<project_root>/.agents/config.yaml``
5. User config: ``~/.jac/config.yaml``
6. Package defaults: ``<package>/data/defaults.yaml``
7. file-secret-settings (pydantic-settings built-in)

Missing YAML files are tolerated (we substitute an empty source). Missing
**required** values raise ``JacConfigError`` at the point of use, not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    YamlConfigSettingsSource,
)

from . import paths


class _EmptySource(PydanticBaseSettingsSource):
    """A no-op settings source for missing config files."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {}


def _yaml_source(settings_cls: type[BaseSettings], path: Path) -> PydanticBaseSettingsSource:
    # Missing files are fine — return a no-op source so lower layers can still apply.
    if path.is_file():
        return YamlConfigSettingsSource(settings_cls, yaml_file=path)
    return _EmptySource(settings_cls)


def jac_config_sources(
    settings_cls: type[BaseSettings],
    init_settings: PydanticBaseSettingsSource,
    env_settings: PydanticBaseSettingsSource,
    dotenv_settings: PydanticBaseSettingsSource,
    file_secret_settings: PydanticBaseSettingsSource,
) -> tuple[PydanticBaseSettingsSource, ...]:
    """Return the JAC config source stack in precedence order (highest first).

    Pydantic walks this tuple top-to-bottom. The first source that has a value
    for a field wins; later sources only fill in fields that are still unset.
    """
    return (
        init_settings,  # CLI / explicit kwargs, e.g. Settings(model="...")
        env_settings,  # JAC_MODEL, JAC_* from the process environment
        dotenv_settings,  # same keys, loaded from .env in the current directory
        _yaml_source(settings_cls, paths.project_config_file()),  # <repo>/.agents/config.yaml
        _yaml_source(settings_cls, paths.USER_CONFIG_FILE),  # ~/.jac/config.yaml
        _yaml_source(settings_cls, paths.package_defaults_file()),  # shipped defaults.yaml
        file_secret_settings,  # pydantic-settings secret files (unused today)
    )
