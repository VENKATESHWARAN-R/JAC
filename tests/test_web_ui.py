"""Tests for the local-first web UI surface (D48).

Covers the Slice 1 control panel: page rendering, the Post/Redirect/Get action
flow, action-layer validation, and two invariants that matter for a panel that
handles credentials — secret *values* are never surfaced, and writing through
the ``env-only`` backend fails cleanly rather than touching the OS keychain.

The fixture forces ``env-only`` secrets precisely so ``resolve()`` reads only
``os.environ`` and never the real macOS/Linux keychain during CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from jac.config import reset_settings_cache
from jac.providers.registry import reset_provider_registry_cache
from jac.runtime.session import Session
from jac.web import actions, panel
from jac.web.server import create_app
from jac.workspace import paths

_VALID_YAML = "tiers:\n  medium:\n    - anthropic:claude-sonnet-4-6\nactive_tier: medium\n"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate user config, project state, and force the env-only secrets backend."""
    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    monkeypatch.setattr(paths, "USER_CONFIG_FILE", user_jac / "config.yaml")

    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    monkeypatch.chdir(project)
    paths.project_root.cache_clear()  # type: ignore[attr-defined]
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]

    # env-only: resolve() reads os.environ only — never the OS keychain.
    monkeypatch.setenv("JAC_SECRETS__BACKEND", "env-only")
    reset_settings_cache()
    reset_provider_registry_cache()
    yield
    reset_settings_cache()
    reset_provider_registry_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ---------- page rendering ----------


@pytest.mark.parametrize("path", ["/chat", "/profiles", "/keys", "/settings"])
def test_pages_render(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200
    assert "JAC" in resp.text


def test_root_redirects_to_chat(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/chat"


def test_static_css_served(client: TestClient) -> None:
    resp = client.get("/static/jac.css")
    assert resp.status_code == 200
    assert "sidebar" in resp.text


# ---------- profile CRUD over HTTP (Post/Redirect/Get) ----------


def test_profile_save_then_delete(client: TestClient) -> None:
    save = client.post(
        "/profiles/save",
        data={"name": "webtest", "yaml": _VALID_YAML},
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert "ok=" in save.headers["location"]
    assert "webtest" in client.get("/profiles").text

    delete = client.post("/profiles/delete", data={"name": "webtest"}, follow_redirects=False)
    assert delete.status_code == 303
    assert "ok=" in delete.headers["location"]
    assert "webtest" not in client.get("/profiles").text


def test_invalid_profile_yaml_flashes_error(client: TestClient) -> None:
    resp = client.post(
        "/profiles/save",
        data={"name": "bad", "yaml": "active_tier: medium\n"},  # no tiers
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "err=" in resp.headers["location"]


# ---------- action-layer validation ----------


def test_action_validation_rejects_empty_input() -> None:
    assert not actions.save_profile_action("", _VALID_YAML).ok
    assert not actions.save_profile_action("x", "   ").ok
    assert not actions.set_secret_action("KEY", "").ok
    assert not actions.delete_session_action("").ok


def test_set_secret_envonly_fails_cleanly() -> None:
    # env-only backend refuses to store — must surface as a failed result,
    # never raise, and never touch a real keychain.
    result = actions.set_secret_action("ANTHROPIC_API_KEY", "sk-test")
    assert not result.ok
    assert "env-only" in result.message


def test_delete_missing_session_fails_cleanly() -> None:
    result = actions.delete_session_action("2099-01-01T00-00-00")
    assert not result.ok


# ---------- security invariant: secret values are never surfaced ----------


def test_keys_context_never_exposes_values() -> None:
    ctx = panel.keys_context()
    assert ctx["backend"] == "env-only"
    for group in ctx["groups"]:
        for entry in group["entries"]:
            assert set(entry) == {"key", "set", "source"}
            assert "value" not in entry


# ---------- sessions are scoped to the launch directory ----------


def test_session_listed_and_deleted(client: TestClient) -> None:
    session = Session.new()
    session.save([])  # empty history is enough to register the session on disk

    # Sessions now live in the shared left rail, present on every page.
    listing = client.get("/chat")
    assert session.session_id in listing.text

    resp = client.post("/sessions/delete", data={"id": session.session_id}, follow_redirects=False)
    assert resp.status_code == 303
    # Lands back on chat (home), not the removed /sessions page (which would 404).
    assert resp.headers["location"].startswith("/chat?")
    assert "ok=" in resp.headers["location"]
    assert session.session_id not in client.get("/chat").text
