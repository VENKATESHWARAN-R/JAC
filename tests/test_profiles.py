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


# ---------- YAML round-trip (PR4) ----------


def test_profile_to_yaml_round_trips_through_load() -> None:
    from jac.profiles import load_profile_from_yaml, profile_to_yaml

    original = Profile(
        tiers={
            "small": ["anthropic:claude-haiku-4-5"],
            "medium": ["anthropic:claude-sonnet-4-5"],
        },
        active_tier="medium",
        env={"OLLAMA_BASE_URL": "http://x"},
    )
    rt = load_profile_from_yaml(profile_to_yaml(original))
    assert rt.tiers == original.tiers
    assert rt.active_tier == original.active_tier
    assert rt.env == original.env


def test_profile_to_yaml_omits_empty_optionals() -> None:
    from jac.profiles import profile_to_yaml

    p = Profile(
        tiers={"medium": ["anthropic:claude-sonnet-4-5"]},
        active_tier="medium",
    )
    out = profile_to_yaml(p)
    assert "env" not in out
    assert "requires_env" not in out


def test_load_profile_from_yaml_rejects_garbage() -> None:
    from jac.profiles import load_profile_from_yaml

    with pytest.raises(JacConfigError, match="invalid YAML"):
        load_profile_from_yaml("tiers: [unclosed\n")


def test_load_profile_from_yaml_rejects_empty() -> None:
    from jac.profiles import load_profile_from_yaml

    with pytest.raises(JacConfigError, match="empty"):
        load_profile_from_yaml("")


def test_load_profile_from_yaml_rejects_non_mapping() -> None:
    from jac.profiles import load_profile_from_yaml

    with pytest.raises(JacConfigError, match="mapping"):
        load_profile_from_yaml("- just\n- a list\n")


def test_load_profile_from_yaml_surfaces_schema_error() -> None:
    from jac.profiles import load_profile_from_yaml

    with pytest.raises(JacConfigError, match="malformed"):
        load_profile_from_yaml("tiers:\n  small: [a:1]\nactive_tier: medium\n")


# ---------- jac profiles edit (PR4) ----------


def _python_editor(action: str) -> str:
    import shutil
    import sys

    py = shutil.which("python3") or sys.executable
    return (
        f'{py} -c "import sys; p=sys.argv[1]; '
        f"text=open(p).read(); {action}; open(p,'w').write(text)\" "
    )


def test_edit_command_persists_valid_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from jac.cli.profiles_cmd import app as profiles_app

    add_or_update_profile(
        "claude",
        Profile(
            tiers={"medium": ["anthropic:claude-sonnet-4-5"]},
            active_tier="medium",
        ),
    )

    # Fake editor: append a "small" tier line to the YAML.
    monkeypatch.setenv(
        "EDITOR",
        _python_editor(
            "text = text.replace("
            "'tiers:\\n  medium:\\n  - anthropic:claude-sonnet-4-5\\n', "
            "'tiers:\\n  small:\\n  - anthropic:claude-haiku-4-5\\n"
            "  medium:\\n  - anthropic:claude-sonnet-4-5\\n')"
        ),
    )

    result = CliRunner().invoke(profiles_app, ["edit", "claude"])
    assert result.exit_code == 0, result.output

    reloaded = list_profiles()["claude"]
    assert reloaded.tier("small") == ["anthropic:claude-haiku-4-5"]
    assert reloaded.tier("medium") == ["anthropic:claude-sonnet-4-5"]


def test_edit_command_noop_when_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from jac.cli.profiles_cmd import app as profiles_app

    add_or_update_profile(
        "claude",
        Profile(
            tiers={"medium": ["anthropic:claude-sonnet-4-5"]},
            active_tier="medium",
        ),
    )
    monkeypatch.setenv("EDITOR", _python_editor("pass"))

    result = CliRunner().invoke(profiles_app, ["edit", "claude"])
    assert result.exit_code == 0
    assert "no changes" in result.output


def test_edit_command_unknown_profile_exits_nonzero() -> None:
    from typer.testing import CliRunner

    from jac.cli.profiles_cmd import app as profiles_app

    result = CliRunner().invoke(profiles_app, ["edit", "does-not-exist"])
    assert result.exit_code == 1
    assert "no profile named" in result.output


def test_edit_command_invalid_yaml_aborts_when_user_declines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from jac.cli.profiles_cmd import app as profiles_app

    add_or_update_profile(
        "claude",
        Profile(
            tiers={"medium": ["anthropic:claude-sonnet-4-5"]},
            active_tier="medium",
        ),
    )

    # Fake editor: replace whole content with invalid YAML.
    monkeypatch.setenv(
        "EDITOR",
        _python_editor("text = 'tiers: [unclosed\\n'"),
    )

    # Typer's CliRunner pipes "n\n" to the Confirm.ask("Re-open editor to fix?")
    result = CliRunner().invoke(profiles_app, ["edit", "claude"], input="n\n")
    assert result.exit_code == 0
    assert "invalid" in result.output
    assert "unchanged" in result.output

    # On-disk profile is intact.
    assert list_profiles()["claude"].default_model() == "anthropic:claude-sonnet-4-5"
