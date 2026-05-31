"""Tests for the R3-R5 management panels: MCP, A2A, skills, context, providers,
memory, and the doctor/readiness aggregate.

Every ``~/.jac`` artifact is redirected into a tmp dir so the write paths never
touch the real workspace. Each test exercises an action's round-trip (write,
read-back) and the obvious rejections.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jac.capabilities.mcp import load_mcp_catalog
from jac.capabilities.skills import load_all_skills
from jac.config import reset_settings_cache
from jac.profiles import Profile
from jac.profiles_crud import add_or_update_profile
from jac.providers.registry import reset_provider_registry_cache
from jac.web import actions, panel
from jac.workspace import paths


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user = tmp_path / ".jac"
    user.mkdir()
    for attr, target in {
        "USER_CONFIG_FILE": user / "config.yaml",
        "USER_MCP_FILE": user / "mcp.json",
        "USER_SKILLS_DIR": user / "skills",
        "USER_PROMPTS_DIR": user / "prompts",
        "USER_CONTEXT_FILE": user / "AGENTS.md",
        "USER_MEMORY_FILE": user / "memory.md",
        "USER_PROVIDERS_FILE": user / "providers.yaml",
    }.items():
        monkeypatch.setattr(paths, attr, target)

    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    monkeypatch.chdir(project)
    paths.project_root.cache_clear()  # type: ignore[attr-defined]
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setenv("JAC_SECRETS__BACKEND", "env-only")
    reset_settings_cache()
    reset_provider_registry_cache()
    yield
    reset_settings_cache()
    reset_provider_registry_cache()


def _seed_profile() -> None:
    add_or_update_profile(
        "p",
        Profile(tiers={"medium": ["anthropic:claude-sonnet-4-6"]}, active_tier="medium"),
        set_default=True,
    )


# ---------- MCP ----------


def test_mcp_save_toggle_delete_roundtrip() -> None:
    r = actions.mcp_save_server_action("user", "github", '{"command": "npx", "args": ["x"]}')
    assert r.ok, r.message
    assert "github" in load_mcp_catalog().servers

    # default knob is enabled=True; disable it and read back
    assert actions.mcp_set_knob_action("user", "github", "enabled", "false").ok
    assert load_mcp_catalog().servers["github"].knobs.enabled is False

    assert actions.mcp_delete_server_action("user", "github").ok
    assert "github" not in load_mcp_catalog().servers


def test_mcp_rejects_bad_entry() -> None:
    assert not actions.mcp_save_server_action("user", "x", "{not json}").ok
    assert not actions.mcp_save_server_action("user", "x", '{"no": "transport"}').ok


def test_mcp_raw_rejects_non_object() -> None:
    assert not actions.mcp_raw_save_action("user", "[1,2,3]").ok
    assert actions.mcp_raw_save_action("user", '{"mcpServers": {}}').ok


# ---------- A2A ----------


def test_a2a_add_and_remove_peer() -> None:
    _seed_profile()
    assert actions.a2a_add_peer_action("research", "http://127.0.0.1:8001", "tok").ok
    ctx = panel.a2a_context()
    assert any(p["name"] == "research" and p["auth"] == "bearer" for p in ctx["a2a_peers"])
    assert actions.a2a_remove_peer_action("research").ok
    assert not panel.a2a_context()["a2a_peers"]


def test_a2a_add_peer_needs_profile() -> None:
    assert not actions.a2a_add_peer_action("x", "http://x", "").ok  # no default profile


def test_a2a_allow_private_toggle() -> None:
    _seed_profile()
    assert actions.a2a_set_allow_private_action("true").ok
    assert panel.a2a_context()["a2a_allow_private"] is True


# ---------- Skills ----------


def test_skill_save_and_delete() -> None:
    body = "---\nname: my-skill\ndescription: test\n---\n\n# Body\n\nadvice"
    assert actions.skill_save_action("user", "my-skill", body).ok
    assert "my-skill" in load_all_skills().active
    assert actions.skill_delete_action("user", "my-skill").ok
    assert "my-skill" not in load_all_skills().active


def test_skill_rejects_bad_name_and_missing_frontmatter() -> None:
    assert not actions.skill_save_action("user", "Bad Name", "---\nx\n---\nbody").ok
    assert not actions.skill_save_action("user", "ok-name", "no frontmatter here").ok


# ---------- Context & prompts ----------


def test_agents_and_prompt_roundtrip() -> None:
    assert actions.context_save_agents_action("user", "# user context").ok
    assert paths.USER_CONTEXT_FILE.read_text() == "# user context"

    assert actions.prompt_save_action("user", "greeting", "hello").ok
    prompts = {p["name"] for p in panel.context_context()["prompts"]}
    assert "greeting" in prompts
    assert actions.prompt_delete_action("user", "greeting").ok
    assert "greeting" not in {p["name"] for p in panel.context_context()["prompts"]}


# ---------- Providers ----------


def test_providers_raw_save() -> None:
    assert actions.providers_raw_save_action("providers: {}\n").ok
    assert paths.USER_PROVIDERS_FILE.is_file()
    assert not actions.providers_raw_save_action("- not a mapping").ok


# ---------- Memory ----------


def test_memory_raw_save() -> None:
    assert actions.memory_raw_save_action("user", "## fact\n- the sky is blue\n").ok
    assert "sky is blue" in paths.USER_MEMORY_FILE.read_text()


# ---------- Doctor ----------


def test_doctor_flags_missing_profile() -> None:
    assert panel.doctor_status() == "bad"  # no default profile in a fresh workspace


def test_doctor_ok_with_profile_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_profile()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reset_settings_cache()
    items = panel.doctor_items()
    assert panel.doctor_status(items) == "ok"
