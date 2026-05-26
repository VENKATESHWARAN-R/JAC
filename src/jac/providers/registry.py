"""Load and merge the provider catalog from package + user YAML."""

from __future__ import annotations

import warnings
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from jac.errors import JacConfigError
from jac.workspace import paths

_REGISTRY_VERSION = 1


class WizardSpec(BaseModel):
    """Metadata shown in ``jac init`` for a provider."""

    label: str
    suggested_model: str
    model_format: str


class ModelPricing(BaseModel):
    """USD per 1M tokens for one model. Used by the cost gate in
    :mod:`jac.runtime.tool_summarize` to decide whether routing a large
    tool output through a small-tier model is strictly cheaper than
    keeping it raw in the current-tier context.
    """

    input: float
    output: float


class ProviderSpec(BaseModel):
    """One provider entry in the catalog."""

    prefix: str
    """Pydantic-ai model id prefix (e.g. ``anthropic``, ``google-gla``, ``gateway``)."""

    required_env: list[str] = Field(default_factory=list)
    wizard: WizardSpec | None = None
    profile_env_keys: list[str] = Field(default_factory=list)
    """Non-secret env vars prompted during init (e.g. ``OLLAMA_BASE_URL``)."""

    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    """Optional per-model pricing in USD/MTok. Keys are bare model ids (no
    provider prefix). Unknown models return ``None`` from
    :meth:`ProviderRegistry.get_pricing` — the summarizer treats that as
    "skip" rather than guessing."""


class InitDefaults(BaseModel):
    default_wizard_provider: str = "anthropic"
    default_secrets_backend: str = "keyring"
    env_defaults: dict[str, str] = Field(default_factory=dict)


class ProviderRegistry(BaseModel):
    """Merged provider catalog."""

    version: int = _REGISTRY_VERSION
    init: InitDefaults = Field(default_factory=InitDefaults)
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)

    def required_env_for_prefix(self, prefix: str) -> list[str]:
        """Return required secret env vars for a pydantic-ai model prefix."""
        for spec in self.providers.values():
            if spec.prefix == prefix:
                return list(spec.required_env)
        warnings.warn(
            f"unknown model provider prefix {prefix!r}; no credentials will be required. "
            "Add an entry to providers.yaml or set requires_env on the profile.",
            stacklevel=2,
        )
        return []

    def wizard_providers(self) -> list[tuple[str, ProviderSpec, WizardSpec]]:
        """Providers with a wizard block, in definition order."""
        result: list[tuple[str, ProviderSpec, WizardSpec]] = []
        for provider_id, spec in self.providers.items():
            if spec.wizard is not None:
                result.append((provider_id, spec, spec.wizard))
        return result

    def get_provider(self, provider_id: str) -> ProviderSpec:
        if provider_id not in self.providers:
            raise JacConfigError(
                f"unknown provider {provider_id!r} in provider catalog. "
                f"Available: {', '.join(self.providers)}."
            )
        return self.providers[provider_id]

    def get_pricing(self, model: str) -> ModelPricing | None:
        """Return pricing for a fully-qualified model id, or ``None`` if unknown.

        ``model`` is a pydantic-ai id like ``anthropic:claude-haiku-4-5`` or
        ``gateway/openai:gpt-5``. We map the prefix to a provider entry, then
        look up the bare model id in that provider's ``pricing`` table. A
        missing prefix, missing provider, or missing model all return ``None``.

        The cost gate treats ``None`` as "skip summarization" — never guesses.
        """
        prefix = provider_prefix(model)
        for spec in self.providers.values():
            if spec.prefix != prefix:
                continue
            bare = model.split(":", 1)[1] if ":" in model else model
            return spec.pricing.get(bare)
        return None


# Back-compat alias for callers that imported the old name from the plan sketch.
WizardProvider = tuple[str, ProviderSpec, WizardSpec]


def provider_prefix(model: str) -> str:
    """Map a pydantic-ai model id to the provider prefix used in the catalog.

    Most providers use ``provider:model``. Gateway uses ``gateway/<upstream>[:model]``,
    so a naive split on ``:`` yields ``gateway/google-cloud``, which would miss the
    ``gateway`` entry.
    """
    if model.startswith("gateway/"):
        return "gateway"
    if ":" in model:
        return model.split(":", 1)[0]
    return model


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise JacConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise JacConfigError(f"{path} must contain a YAML mapping at the top level")
    return data


def _merge_registry_data(package: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    merged = dict(package)
    if "init" in user:
        merged["init"] = _deep_merge_dict(
            merged.get("init", {}) if isinstance(merged.get("init"), dict) else {},
            user["init"] if isinstance(user["init"], dict) else {},
        )
    if "providers" in user:
        pkg_providers = merged.get("providers", {})
        if not isinstance(pkg_providers, dict):
            pkg_providers = {}
        usr_providers = user["providers"]
        if not isinstance(usr_providers, dict):
            raise JacConfigError("`providers` in user providers.yaml must be a mapping")
        merged_providers = dict(pkg_providers)
        for provider_id, overlay in usr_providers.items():
            if provider_id in merged_providers and isinstance(merged_providers[provider_id], dict):
                if isinstance(overlay, dict):
                    merged_providers[provider_id] = _deep_merge_dict(
                        merged_providers[provider_id], overlay
                    )
                else:
                    merged_providers[provider_id] = overlay
            else:
                merged_providers[provider_id] = overlay
        merged["providers"] = merged_providers
    if "version" in user:
        merged["version"] = user["version"]
    return merged


def _build_registry_from_raw(raw: dict[str, Any], *, source: str) -> ProviderRegistry:
    try:
        return ProviderRegistry.model_validate(raw)
    except Exception as exc:
        raise JacConfigError(f"invalid provider catalog in {source}: {exc}") from exc


@cache
def get_provider_registry() -> ProviderRegistry:
    """Return the merged package + user provider catalog (cached)."""
    package_path = paths.package_providers_file()
    user_path = paths.USER_PROVIDERS_FILE
    package_raw = _load_yaml_file(package_path)
    user_raw = _load_yaml_file(user_path)
    merged = _merge_registry_data(package_raw, user_raw)
    source = str(package_path)
    if user_raw:
        source = f"{package_path} + {user_path}"
    return _build_registry_from_raw(merged, source=source)


def reset_provider_registry_cache() -> None:
    """Drop the cached registry — for tests after changing YAML."""
    get_provider_registry.cache_clear()
