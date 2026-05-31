"""Tests for the web config panel (R1): scope-aware writes + precedence badges.

The differentiator of the JAC config UI is honesty about the layered config:
every field reports which layer supplies it, edits land in exactly one scope,
and a field a higher-precedence layer overrides is locked. These tests pin that
behavior — plus the invariant that writing a config group never clobbers the
profiles that share the same ``config.yaml``.

The fixture isolates user + project config to a tmp dir (as the other web tests
do) so nothing touches the real workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from jac.config import get_settings, reset_settings_cache
from jac.providers.registry import reset_provider_registry_cache
from jac.web import actions, panel
from jac.web.server import create_app
from jac.workspace import config_io, paths


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_CONFIG_FILE", user_jac / "config.yaml")

    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    monkeypatch.chdir(project)
    paths.project_root.cache_clear()  # type: ignore[attr-defined]
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]

    monkeypatch.setenv("JAC_SECRETS__BACKEND", "env-only")
    monkeypatch.delenv("JAC_MODEL", raising=False)
    reset_settings_cache()
    reset_provider_registry_cache()
    yield
    reset_settings_cache()
    reset_provider_registry_cache()


def _field(ctx: dict, group: str, name: str) -> dict:
    grp = next(g for g in ctx["cfg_groups"] if g["key"] == group)
    return next(f for f in grp["fields"] if f["name"] == name)


# ---------- read-side: origins ----------


def test_default_field_origin_is_defaults_or_code() -> None:
    ctx = panel.config_context("user")
    # compaction.warn_pct ships in defaults.yaml
    assert _field(ctx, "compaction", "warn_pct")["origin"] == "defaults"
    # budget caps have no defaults.yaml entry → pure in-code default
    assert _field(ctx, "budget", "session_total_tokens")["origin"] == "code"


def test_env_var_makes_field_env_sourced_and_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_COMPACTION__WARN_PCT", "55")
    reset_settings_cache()
    field = _field(panel.config_context("user"), "compaction", "warn_pct")
    assert field["origin"] == "env"
    assert field["locked"] is True  # env outranks the user layer
    assert field["value_str"] == "55"


# ---------- write-side: round-trip + scope ----------


def test_set_writes_to_chosen_scope_and_changes_origin() -> None:
    result = actions.config_set_action("user", "compaction", "warn_pct", "int", "50")
    assert result.ok, result.message
    reset_settings_cache()
    assert get_settings().compaction.warn_pct == 50
    field = _field(panel.config_context("user"), "compaction", "warn_pct")
    assert field["origin"] == "user"
    assert field["in_scope"] is True


def test_project_overrides_user_in_resolution() -> None:
    actions.config_set_action("user", "compaction", "max_context_tokens", "int", "100000")
    actions.config_set_action("project", "compaction", "max_context_tokens", "int", "200000")
    reset_settings_cache()
    assert get_settings().compaction.max_context_tokens == 200000
    # editing the user layer is now locked — project wins
    assert (
        _field(panel.config_context("user"), "compaction", "max_context_tokens")["locked"] is True
    )
    assert (
        _field(panel.config_context("project"), "compaction", "max_context_tokens")["locked"]
        is False
    )


def test_validation_rejects_over_ceiling() -> None:
    result = actions.config_set_action("user", "compaction", "max_context_tokens", "int", "999999")
    assert not result.ok
    assert "ceiling" in result.message.lower() or "512" in result.message
    # nothing was written
    assert config_io.load_scope_raw("user") == {}


def test_validation_rejects_non_positive_budget() -> None:
    result = actions.config_set_action("user", "budget", "session_total_tokens", "int_opt", "0")
    assert not result.ok
    assert config_io.load_scope_raw("user") == {}


def test_blank_int_opt_unsets() -> None:
    actions.config_set_action("user", "budget", "session_total_tokens", "int_opt", "200000")
    reset_settings_cache()
    assert get_settings().budget.session_total_tokens == 200000
    # blank clears it
    result = actions.config_set_action("user", "budget", "session_total_tokens", "int_opt", "")
    assert result.ok
    reset_settings_cache()
    assert get_settings().budget.session_total_tokens is None


def test_unset_reverts_to_inherited() -> None:
    actions.config_set_action("user", "compaction", "warn_pct", "int", "50")
    reset_settings_cache()
    assert get_settings().compaction.warn_pct == 50
    actions.config_unset_action("user", "compaction", "warn_pct")
    reset_settings_cache()
    assert get_settings().compaction.warn_pct == 60  # back to defaults.yaml


def test_bad_scope_rejected() -> None:
    assert not actions.config_set_action("bogus", "compaction", "warn_pct", "int", "50").ok


# ---------- invariant: config writes preserve profiles in the same file ----------


def test_config_write_preserves_profiles() -> None:
    # seed a profile into the user config (the same file config groups live in)
    paths.USER_CONFIG_FILE.write_text(
        yaml.safe_dump(
            {
                "default_profile": "p",
                "profiles": {"p": {"tiers": {"medium": ["anthropic:x"]}, "active_tier": "medium"}},
            }
        ),
        encoding="utf-8",
    )
    actions.config_set_action("user", "compaction", "warn_pct", "int", "50")
    raw = config_io.load_scope_raw("user")
    assert raw["default_profile"] == "p"  # profile survived the config write
    assert raw["profiles"]["p"]["active_tier"] == "medium"
    assert raw["compaction"]["warn_pct"] == 50


# ---------- raw escape hatch ----------


def test_raw_save_writes_and_rejects_non_mapping() -> None:
    ok = actions.config_raw_save_action("user", "compaction:\n  warn_pct: 42\n")
    assert ok.ok
    reset_settings_cache()
    assert get_settings().compaction.warn_pct == 42
    bad = actions.config_raw_save_action("user", "- just\n- a list\n")
    assert not bad.ok


# ---------- routes: htmx vs PRG ----------


def test_config_set_route_htmx_returns_fragment() -> None:
    client = TestClient(create_app())
    resp = client.post(
        "/config/set",
        data={
            "scope": "user",
            "group": "compaction",
            "field": "warn_pct",
            "type": "int",
            "value": "50",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "cfg-src" in resp.text  # a re-rendered fragment, not a redirect


def test_config_set_route_plain_redirects() -> None:
    client = TestClient(create_app())
    resp = client.post(
        "/config/set",
        data={
            "scope": "user",
            "group": "compaction",
            "field": "warn_pct",
            "type": "int",
            "value": "50",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/config?scope=user")
