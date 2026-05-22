"""Tests for the tiered profile schema (D22) and the old-shape migration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jac.errors import JacConfigError
from jac.profiles import (
    Profile,
    add_or_update_profile,
    detect_old_profiles,
    list_profiles,
    migrate_old_profiles,
)
from jac.providers.registry import reset_provider_registry_cache
from jac.workspace import paths


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect USER_CONFIG_FILE to a temp file for every test in this module."""
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_CONFIG_FILE", user_jac / "config.yaml")
    reset_provider_registry_cache()
    yield
    reset_provider_registry_cache()


# ---------- Profile schema ----------


def test_profile_default_model_picks_active_tier_first_entry() -> None:
    p = Profile(
        tiers={
            "small": ["anthropic:claude-haiku-4-5"],
            "medium": ["anthropic:claude-sonnet-4-5", "openai:gpt-4o"],
        },
        active_tier="medium",
    )
    assert p.default_model() == "anthropic:claude-sonnet-4-5"


def test_profile_required_env_keys_unions_across_tiers() -> None:
    p = Profile(
        tiers={
            "small": ["openai:gpt-4o-mini", "anthropic:claude-haiku-4-5"],
            "medium": ["anthropic:claude-sonnet-4-5"],
        },
        active_tier="medium",
    )
    # First-seen order, no duplicates.
    assert p.required_env_keys() == ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]


def test_profile_explicit_requires_env_overrides_inference() -> None:
    p = Profile(
        tiers={"medium": ["anthropic:claude-sonnet-4-5"]},
        active_tier="medium",
        requires_env=["CUSTOM_KEY"],
    )
    assert p.required_env_keys() == ["CUSTOM_KEY"]


def test_profile_all_models_preserves_first_seen_order_and_dedupes() -> None:
    p = Profile(
        tiers={
            "small": ["a:1", "b:2"],
            "medium": ["b:2", "c:3"],
        },
        active_tier="small",
    )
    assert p.all_models() == ["a:1", "b:2", "c:3"]


def test_profile_tier_lookup_raises_for_unknown_tier() -> None:
    p = Profile(tiers={"medium": ["a:1"]}, active_tier="medium")
    with pytest.raises(JacConfigError, match="no tier 'large'"):
        p.tier("large")


# ---------- Profile validation ----------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"tiers": {}, "active_tier": "medium"}, "at least one tier"),
        (
            {"tiers": {"medium": []}, "active_tier": "medium"},
            "at least one model",
        ),
        (
            {"tiers": {"medium": [""]}, "active_tier": "medium"},
            "empty or non-string",
        ),
        (
            {"tiers": {"small": ["a:1"]}, "active_tier": "medium"},
            "not defined in tiers",
        ),
        (
            {"tiers": {"-bad": ["a:1"]}, "active_tier": "-bad"},
            "invalid tier name",
        ),
    ],
)
def test_profile_rejects_invalid_shapes(kwargs: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Profile(**kwargs)


# ---------- list_profiles / fail-first on old shape ----------


def test_list_profiles_returns_validated_profiles() -> None:
    paths.USER_CONFIG_FILE.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "claude": {
                        "tiers": {"medium": ["anthropic:claude-sonnet-4-5"]},
                        "active_tier": "medium",
                    },
                },
            }
        )
    )
    profiles = list_profiles()
    assert set(profiles) == {"claude"}
    assert profiles["claude"].default_model() == "anthropic:claude-sonnet-4-5"


def test_list_profiles_fails_first_on_old_shape() -> None:
    paths.USER_CONFIG_FILE.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "claude": {"model": "anthropic:claude-sonnet-4-5"},
                    "new": {
                        "tiers": {"medium": ["anthropic:claude-opus-4-7"]},
                        "active_tier": "medium",
                    },
                },
            }
        )
    )
    with pytest.raises(JacConfigError, match="pre-D22 `model:` schema"):
        list_profiles()


# ---------- migration ----------


def test_detect_old_profiles_lists_only_old_shape() -> None:
    paths.USER_CONFIG_FILE.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "claude": {"model": "anthropic:claude-sonnet-4-5"},
                    "new": {
                        "tiers": {"medium": ["anthropic:claude-opus-4-7"]},
                        "active_tier": "medium",
                    },
                },
            }
        )
    )
    assert detect_old_profiles() == ["claude"]


def test_migrate_old_profiles_rewrites_and_is_idempotent() -> None:
    paths.USER_CONFIG_FILE.write_text(
        yaml.safe_dump(
            {
                "default_profile": "claude",
                "profiles": {
                    "claude": {
                        "model": "anthropic:claude-sonnet-4-5",
                        "env": {"OLLAMA_BASE_URL": "http://x"},
                        "requires_env": ["CUSTOM_KEY"],
                    },
                },
            }
        )
    )

    assert migrate_old_profiles() == ["claude"]
    # Idempotent
    assert migrate_old_profiles() == []

    profiles = list_profiles()
    assert profiles["claude"].tiers == {"medium": ["anthropic:claude-sonnet-4-5"]}
    assert profiles["claude"].active_tier == "medium"
    assert profiles["claude"].env == {"OLLAMA_BASE_URL": "http://x"}
    assert profiles["claude"].requires_env == ["CUSTOM_KEY"]


def test_migrate_returns_empty_when_no_old_profiles_present() -> None:
    paths.USER_CONFIG_FILE.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "new": {
                        "tiers": {"medium": ["anthropic:claude-opus-4-7"]},
                        "active_tier": "medium",
                    },
                },
            }
        )
    )
    assert migrate_old_profiles() == []


def test_migrate_handles_missing_config_file() -> None:
    # USER_CONFIG_FILE doesn't exist yet — nothing to migrate, no crash.
    assert detect_old_profiles() == []
    assert migrate_old_profiles() == []


# ---------- add_or_update_profile round-trip ----------


def test_add_or_update_profile_persists_tiered_shape() -> None:
    p = Profile(
        tiers={
            "small": ["anthropic:claude-haiku-4-5"],
            "medium": ["anthropic:claude-sonnet-4-5"],
        },
        active_tier="medium",
    )
    add_or_update_profile("claude", p, set_default=True)

    profiles = list_profiles()
    assert "claude" in profiles
    assert profiles["claude"].default_model() == "anthropic:claude-sonnet-4-5"
    assert profiles["claude"].tier("small") == ["anthropic:claude-haiku-4-5"]
