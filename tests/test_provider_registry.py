"""Tests for the provider catalog loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jac.errors import JacConfigError
from jac.providers.registry import (
    get_provider_registry,
    provider_prefix,
    reset_provider_registry_cache,
)
from jac.workspace import paths


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> None:
    reset_provider_registry_cache()
    yield
    reset_provider_registry_cache()


def test_provider_prefix_gateway() -> None:
    assert provider_prefix("gateway/openai:gpt-4o") == "gateway"
    assert provider_prefix("anthropic:claude-sonnet-4-5") == "anthropic"


def test_package_registry_loads_anthropic_keys() -> None:
    registry = get_provider_registry()
    assert registry.required_env_for_prefix("anthropic") == ["ANTHROPIC_API_KEY"]


def test_wizard_providers_includes_anthropic() -> None:
    registry = get_provider_registry()
    ids = [entry[0] for entry in registry.wizard_providers()]
    assert "anthropic" in ids
    assert "google" in ids
    assert registry.get_provider("google").prefix == "google-gla"


def test_user_overlay_merges_suggested_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_PROVIDERS_FILE", user_jac / "providers.yaml")
    (user_jac / "providers.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "anthropic": {
                        "wizard": {"suggested_model": "claude-opus-4-6"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    reset_provider_registry_cache()
    registry = get_provider_registry()
    spec = registry.get_provider("anthropic")
    assert spec.wizard is not None
    assert spec.wizard.suggested_model == "claude-opus-4-6"
    assert spec.required_env == ["ANTHROPIC_API_KEY"]


def test_unknown_prefix_warns_and_returns_empty() -> None:
    registry = get_provider_registry()
    with pytest.warns(UserWarning, match="unknown model provider prefix"):
        keys = registry.required_env_for_prefix("totally-unknown")
    assert keys == []


def test_invalid_user_yaml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_PROVIDERS_FILE", user_jac / "providers.yaml")
    (user_jac / "providers.yaml").write_text("providers: not-a-map\n", encoding="utf-8")
    reset_provider_registry_cache()
    with pytest.raises(JacConfigError, match=r"providers.*must be a mapping"):
        get_provider_registry()
