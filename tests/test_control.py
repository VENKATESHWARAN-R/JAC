"""Tests for the surface-agnostic control plane (:mod:`jac.runtime.control`).

The control plane is where the model/profile rebuild dance and the MCP/skills
mutations live once, so both the CLI and the web drive identical behavior. The
rollback path exercises the genuine apply→fail→restore flow (no provider
needed — switching to an anthropic model with no key fails for real); the
success path monkeypatches model construction.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import jac.runtime.control as controlmod
from jac.config import reset_settings_cache
from jac.providers.registry import reset_provider_registry_cache
from jac.runtime.control import SessionController
from jac.runtime.events import EventBus
from jac.workspace import paths


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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reset_settings_cache()
    reset_provider_registry_cache()
    yield
    reset_settings_cache()
    reset_provider_registry_cache()


def _fake_runtime(model_id: str = "ollama:llama3", **extra) -> SimpleNamespace:
    fields: dict = {
        "active_profile": None,
        "profile_name": None,
        "persisted_capabilities": [],
        "bus": EventBus(),
        "driver": SimpleNamespace(gru="OLD_GRU"),
        "gru": "OLD_GRU",
        "model_id": model_id,
        "a2a_capability": None,
        "mcp_capability": None,
        "skills_capability": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


class _FakeMcp:
    """Minimal MCPCapability stand-in: a one-server catalog + set_enabled/reload."""

    def __init__(self, enabled: bool = False) -> None:
        server = SimpleNamespace(knobs=SimpleNamespace(enabled=enabled))
        self.catalog = SimpleNamespace(
            servers={"fs": server}, enabled=[] if not enabled else ["fs"]
        )
        self.set_enabled_calls: list[tuple[str, bool]] = []
        self.reload_calls = 0

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.set_enabled_calls.append((name, enabled))
        self.catalog.servers[name].knobs.enabled = enabled
        self.catalog.enabled = [name] if enabled else []

    def reload(self) -> None:
        self.reload_calls += 1


# ---------- model / profile switching ----------


def test_switch_model_empty_is_rejected() -> None:
    r = SessionController(_fake_runtime()).switch_model("  ")
    assert r.ok is False


def test_switch_profile_unknown_is_graceful() -> None:
    r = SessionController(_fake_runtime()).switch_profile("nope")
    assert r.ok is False
    assert "nope" in r.message or "profile" in r.message.lower()


def test_switch_model_rolls_back_on_missing_key() -> None:
    # Switching to an anthropic model with no ANTHROPIC_API_KEY must fail and
    # leave the running agent + env untouched (snapshot/restore).
    rt = _fake_runtime()
    r = SessionController(rt).switch_model("anthropic:claude-sonnet-4-6")
    assert r.ok is False
    assert "ANTHROPIC_API_KEY" in r.message
    assert rt.model_id == "ollama:llama3"  # unchanged
    assert rt.gru == "OLD_GRU"  # agent not swapped
    assert os.environ.get("JAC_MODEL") is None  # env restored


def test_switch_model_swaps_agent_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controlmod, "build_gru", lambda **kw: "NEW_GRU")
    rt = _fake_runtime()
    r = SessionController(rt).switch_model("ollama:llama3.2")
    assert r.ok is True
    assert rt.gru == "NEW_GRU"
    assert rt.driver.gru == "NEW_GRU"
    assert rt.model_id == "ollama:llama3.2"
    assert r.data == {"model": "ollama:llama3.2", "profile": None}


# ---------- MCP knobs ----------


def test_set_mcp_enabled_unknown_server() -> None:
    rt = _fake_runtime(mcp_capability=_FakeMcp())
    r = SessionController(rt).set_mcp_enabled("nope", True)
    assert r.ok is False
    assert "nope" in r.message


def test_set_mcp_enabled_persists_and_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controlmod, "build_gru", lambda **kw: "NEW_GRU")
    mcp = _FakeMcp(enabled=False)
    rt = _fake_runtime(mcp_capability=mcp)
    r = SessionController(rt).set_mcp_enabled("fs", True)
    assert r.ok is True
    assert mcp.set_enabled_calls == [("fs", True)]  # persisted
    assert rt.driver.gru == "NEW_GRU"  # rebuilt so the change takes effect now


def test_set_mcp_enabled_noop_when_already_in_state() -> None:
    mcp = _FakeMcp(enabled=True)
    rt = _fake_runtime(mcp_capability=mcp)
    r = SessionController(rt).set_mcp_enabled("fs", True)
    assert r.ok is True
    assert mcp.set_enabled_calls == []  # nothing persisted, no rebuild
    assert rt.driver.gru == "OLD_GRU"


def test_reload_mcp_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controlmod, "build_gru", lambda **kw: "NEW_GRU")
    mcp = _FakeMcp()
    rt = _fake_runtime(mcp_capability=mcp)
    r = SessionController(rt).reload_mcp()
    assert r.ok is True
    assert mcp.reload_calls == 1
    assert rt.driver.gru == "NEW_GRU"


# ---------- toolset refresh guard ----------


def test_refresh_toolsets_refuses_without_model() -> None:
    rt = _fake_runtime(model_id="unknown")
    r = SessionController(rt).refresh_toolsets()
    assert r.ok is False
    assert "no model" in r.message.lower()


# ---------- skills ----------


def test_reload_skills() -> None:
    skills = SimpleNamespace(reload=lambda: calls.append(1), skills={"a": 1, "b": 2})
    calls: list[int] = []
    rt = _fake_runtime(skills_capability=skills)
    r = SessionController(rt).reload_skills()
    assert r.ok is True
    assert calls == [1]
    assert r.data == {"count": 2}
