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


def test_unknown_prefix_warning_lists_known_prefixes(
    recwarn: pytest.WarningsRecorder,
) -> None:
    # R12: the warning must be loud — name the known prefixes so a typo is
    # diagnosable at config time, not opaque at request time.
    registry = get_provider_registry()
    registry.required_env_for_prefix("totally-unknown")
    messages = [str(w.message) for w in recwarn.list]
    assert any("Known prefixes:" in m and "anthropic" in m for m in messages)


def test_unknown_prefix_warning_suggests_close_match() -> None:
    # R12: a near-miss typo gets a "Did you mean ...?" hint via difflib.
    registry = get_provider_registry()
    with pytest.warns(UserWarning, match="Did you mean 'anthropic'"):
        registry.required_env_for_prefix("anthropc")


def test_get_pricing_returns_known_model() -> None:
    registry = get_provider_registry()
    pricing = registry.get_pricing("anthropic:claude-haiku-4-5")
    assert pricing is not None
    assert pricing.input == 1.00
    assert pricing.output == 5.00


def test_get_pricing_returns_none_for_unknown_model() -> None:
    registry = get_provider_registry()
    assert registry.get_pricing("anthropic:claude-nonexistent") is None


def test_get_pricing_returns_none_for_unknown_prefix() -> None:
    registry = get_provider_registry()
    assert registry.get_pricing("totally-unknown:foo") is None


def test_pricing_overlay_from_user_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_PROVIDERS_FILE", user_jac / "providers.yaml")
    (user_jac / "providers.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "anthropic": {
                        "pricing": {
                            "claude-custom": {"input": 0.5, "output": 2.0},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    reset_provider_registry_cache()
    registry = get_provider_registry()
    custom = registry.get_pricing("anthropic:claude-custom")
    assert custom is not None
    assert custom.input == 0.5
    # Package defaults still present alongside the user addition.
    assert registry.get_pricing("anthropic:claude-haiku-4-5") is not None


def test_invalid_user_yaml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_PROVIDERS_FILE", user_jac / "providers.yaml")
    (user_jac / "providers.yaml").write_text("providers: not-a-map\n", encoding="utf-8")
    reset_provider_registry_cache()
    with pytest.raises(JacConfigError, match=r"providers.*must be a mapping"):
        get_provider_registry()
