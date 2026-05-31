"""Tests for the web's live model/profile switch (R2): the mid-session rebuild.

The one new engine seam in the web redesign is rebuilding Gru in place when the
operator switches model/profile from the top bar. It must snapshot env and roll
back on failure so a missing key (or an unknown model) leaves the running agent
untouched — exactly like the REPL's ``/model`` / ``/profile``.

The real model construction is monkeypatched for the success path (no provider
needed); the rollback path exercises the genuine apply→fail→restore flow.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import jac.web.chat as chatmod
from jac.config import reset_settings_cache
from jac.providers.registry import reset_provider_registry_cache
from jac.runtime.events import EventBus
from jac.web.chat import WebChatManager
from jac.web.server import create_app
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
    monkeypatch.setattr(chatmod, "_MANAGER", None)
    yield
    reset_settings_cache()
    reset_provider_registry_cache()


def _fake_runtime(model_id: str = "ollama:llama3") -> SimpleNamespace:
    return SimpleNamespace(
        active_profile=None,
        persisted_capabilities=[],
        bus=EventBus(),
        driver=SimpleNamespace(gru="OLD_GRU"),
        gru="OLD_GRU",
        model_id=model_id,
        a2a_capability=None,
    )


# ---------- input validation / graceful no-runtime ----------


def test_switch_model_empty_is_rejected() -> None:
    assert asyncio.run(WebChatManager().switch_model(""))["ok"] is False


def test_switch_profile_unknown_is_graceful() -> None:
    r = asyncio.run(WebChatManager().switch_profile("nope"))
    assert r["ok"] is False
    assert "nope" in r["reason"] or "profile" in r["reason"].lower()


def test_switch_model_without_profile_is_graceful() -> None:
    # No profile in this workspace → no runtime can be built → friendly refusal.
    r = asyncio.run(WebChatManager().switch_model("anthropic:claude-x"))
    assert r["ok"] is False


# ---------- switcher options ----------


def test_switcher_options_shape_without_profile() -> None:
    opts = WebChatManager().switcher_options()
    assert set(opts) == {"profiles", "current_profile", "models", "current_model"}
    assert opts["models"] == []


# ---------- the rebuild guard ----------


def test_rebuild_rolls_back_on_missing_key() -> None:
    # Switching to an anthropic model with no ANTHROPIC_API_KEY must fail and
    # leave the running agent + env untouched (snapshot/restore).
    mgr = WebChatManager()
    mgr.runtime = _fake_runtime()  # type: ignore[assignment]
    mgr.profile_name = None
    ok, msg = mgr._rebuild(new_model_id="anthropic:claude-sonnet-4-6", new_profile_name=None)
    assert ok is False
    assert "ANTHROPIC_API_KEY" in msg
    assert mgr.runtime.model_id == "ollama:llama3"  # unchanged
    assert mgr.runtime.gru == "OLD_GRU"  # agent not swapped
    assert os.environ.get("JAC_MODEL") is None  # env restored


def test_rebuild_swaps_agent_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # Monkeypatch model construction so no provider is needed; assert the agent
    # and model id are swapped in place (history/bus untouched is implicit —
    # _rebuild never touches them).
    monkeypatch.setattr(chatmod, "build_gru", lambda **kw: "NEW_GRU")
    mgr = WebChatManager()
    mgr.runtime = _fake_runtime()  # type: ignore[assignment]
    mgr.profile_name = None
    ok, msg = mgr._rebuild(new_model_id="ollama:llama3.2", new_profile_name=None)
    assert ok is True
    assert msg == "ollama:llama3.2"
    assert mgr.runtime.gru == "NEW_GRU"
    assert mgr.runtime.driver.gru == "NEW_GRU"
    assert mgr.runtime.model_id == "ollama:llama3.2"
    assert mgr.model_override == "ollama:llama3.2"  # ad-hoc model, no profile


# ---------- routes ----------


def test_switcher_route_shape() -> None:
    client = TestClient(create_app())
    resp = client.get("/chat/switcher")
    assert resp.status_code == 200
    assert set(resp.json()) == {"profiles", "current_profile", "models", "current_model"}


def test_switch_routes_graceful_without_model() -> None:
    client = TestClient(create_app())
    assert client.post("/chat/switch-model", json={"model": "x"}).json()["ok"] is False
    assert client.post("/chat/switch-profile", json={"profile": "x"}).json()["ok"] is False
