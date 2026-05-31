"""Tests for jac.cli.slash.handlers.a2a.

The ``/a2a`` REPL surface is now *outbound peers only* — ``peers`` and
``peer add|remove``. The inbound server lifecycle (``serve``/``stop``/
``status``/``token``) was removed from the REPL and lives only in the
headless ``jac a2a serve`` command; the slash handler now redirects those
words there. So the test surface here is: peer add/remove/listing, and the
serve-family redirect.

End-to-end server start/stop is exercised by ``test_a2a_server.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

import pytest
from rich.console import Console

from jac.capabilities.a2a import make_a2a_capability
from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a import a2a_handler
from jac.cli.slash.result import Handled
from jac.profiles import Profile
from jac.runtime.session import Session


@dataclass
class _Captured:
    ctx: SlashContext
    console: Console
    buf: StringIO


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A real Session, anchored to tmp_path so we don't pollute the repo."""
    from jac.workspace import paths

    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    paths.find_project_root.cache_clear() if hasattr(
        paths.find_project_root, "cache_clear"
    ) else None
    return Session.new()


@pytest.fixture
def ctx(session) -> _Captured:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    profile = Profile(
        tiers={"medium": ["test:fake-model"]},
        active_tier="medium",
    )
    a2a = make_a2a_capability(model="test:fake-model", profile_name="test")
    sc = SlashContext(
        console=console,
        session=session,
        profile_name="test",
        profile=profile,
        model_id="test:fake-model",
        a2a=a2a,
    )
    return _Captured(ctx=sc, console=console, buf=buf)


# ---------- handler dispatch ----------


@pytest.mark.parametrize("sub", ["serve", "stop", "status", "token"])
def test_server_lifecycle_words_redirect_to_headless(ctx: _Captured, sub: str):
    """serve/stop/status/token are no longer REPL actions — they redirect to
    `jac a2a serve` and never return a start/stop result."""
    result = a2a_handler(ctx.ctx, sub)
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    assert "jac a2a serve" in out
    assert "removed from the REPL" in out


def test_serve_with_flags_still_redirects(ctx: _Captured):
    """Even with the old flags, `/a2a serve --port N` just redirects."""
    result = a2a_handler(ctx.ctx, "serve --port 9001 --unsafe")
    assert isinstance(result, Handled)
    assert "jac a2a serve" in ctx.buf.getvalue()


def test_unknown_subcommand_renders_help(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "frobulate")
    assert isinstance(result, Handled)
    assert "unknown /a2a subcommand" in ctx.buf.getvalue()


def test_no_subcommand_prints_usage(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "")
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    assert "usage" in out.lower()


# ---------- /a2a peers (PR2) ----------


def test_peers_empty_shows_hint(ctx: _Captured):
    """No peers configured → render a friendly hint pointing at profiles edit."""
    # Capability starts with empty peers (fixture default)
    result = a2a_handler(ctx.ctx, "peers")
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    assert "none configured" in out
    assert "profiles edit" in out.lower()


def test_peers_lists_configured_entries(ctx: _Captured):
    """Configured peers render with name, URL, auth status, description."""
    from jac.profiles import A2APeerConfig

    # Mutate profile_peers in place (the property `peers` is read-only and
    # returns the profile+session merged view).
    ctx.ctx.a2a.profile_peers.update(
        {
            "backend-jac": A2APeerConfig(
                url="http://localhost:9000", token="t1", description="backend repo"
            ),
            "no-auth-peer": A2APeerConfig(url="http://127.0.0.1:9001", description=""),
        }
    )
    result = a2a_handler(ctx.ctx, "peers")
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    # Both peer names listed
    assert "backend-jac" in out
    assert "no-auth-peer" in out
    # Auth state surfaced — bearer for one, none for the other
    assert "bearer" in out
    assert "none" in out
    # Description shown for the one that has it
    assert "backend repo" in out
    # Profile-provenance tag rendered
    assert "[profile]" in out


# ---------- /a2a peer add | remove (Phase 4.c) ----------


def test_peer_add_unauthenticated(ctx: _Captured):
    """`/a2a peer add NAME URL` with no auth flag adds an unauth peer."""
    result = a2a_handler(ctx.ctx, "peer add local-test http://127.0.0.1:9999")
    assert isinstance(result, Handled)
    assert "local-test" in ctx.ctx.a2a.session_peers
    peer = ctx.ctx.a2a.session_peers["local-test"]
    assert peer.url == "http://127.0.0.1:9999"
    assert peer.auth is None
    out = ctx.buf.getvalue()
    assert "no auth flag" in out
    assert "✓ session peer added" in out


def test_peer_add_with_bearer_prompts_for_token(ctx: _Captured, monkeypatch):
    """`--bearer` triggers an interactive prompt via getpass."""
    # Monkey-patch getpass to return a deterministic token.
    monkeypatch.setattr("getpass.getpass", lambda _label: "MY-SECRET-TOKEN")
    result = a2a_handler(ctx.ctx, "peer add backend http://localhost:9000 --bearer")
    assert isinstance(result, Handled)
    peer = ctx.ctx.a2a.session_peers["backend"]
    from jac.profiles import BearerAuth

    assert isinstance(peer.auth, BearerAuth)
    assert peer.auth.token == "MY-SECRET-TOKEN"
    # The token does NOT appear in the rendered output (it would have been
    # echoed to scrollback if we'd used Prompt.ask). getpass suppresses echo.
    assert "MY-SECRET-TOKEN" not in ctx.buf.getvalue()


def test_peer_add_with_api_key_prompts_for_value(ctx: _Captured, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _label: "K-VALUE")
    result = a2a_handler(ctx.ctx, "peer add saas https://api.example.com --api-key X-API-Key")
    assert isinstance(result, Handled)
    peer = ctx.ctx.a2a.session_peers["saas"]
    from jac.profiles import ApiKeyAuth

    assert isinstance(peer.auth, ApiKeyAuth)
    assert peer.auth.header == "X-API-Key"
    assert peer.auth.value == "K-VALUE"


def test_peer_add_with_oauth2_prompts_for_secret(ctx: _Captured, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _label: "S3CR3T")
    result = a2a_handler(
        ctx.ctx,
        "peer add azure https://prod.azurewebsites.net --oauth2 "
        "https://login/tok client-id --scope api/.default",
    )
    assert isinstance(result, Handled)
    peer = ctx.ctx.a2a.session_peers["azure"]
    from jac.profiles import OAuth2ClientCredentialsAuth

    assert isinstance(peer.auth, OAuth2ClientCredentialsAuth)
    assert peer.auth.token_url == "https://login/tok"
    assert peer.auth.client_id == "client-id"
    assert peer.auth.client_secret == "S3CR3T"
    assert peer.auth.scope == "api/.default"


def test_peer_add_rejects_invalid_url(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "peer add bad ftp://nope")
    assert isinstance(result, Handled)
    assert "ftp://nope" in ctx.buf.getvalue()
    assert "bad" not in ctx.ctx.a2a.session_peers


def test_peer_add_rejects_invalid_name(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "peer add Bad_Name http://x")
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    assert "invalid peer name" in out
    assert "Bad_Name" not in ctx.ctx.a2a.session_peers


def test_peer_add_rejects_unknown_auth_flag(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "peer add p http://x --tls")
    assert isinstance(result, Handled)
    assert "unknown auth flag" in ctx.buf.getvalue()


def test_peer_add_cancelled_when_secret_empty(ctx: _Captured, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _label: "")
    result = a2a_handler(ctx.ctx, "peer add x http://x --bearer")
    assert isinstance(result, Handled)
    assert "x" not in ctx.ctx.a2a.session_peers
    assert "cancelled" in ctx.buf.getvalue()


def test_peer_add_shadows_profile_entry_loudly(ctx: _Captured):
    """Session entry overriding a profile entry of the same name warns."""
    from jac.profiles import A2APeerConfig

    ctx.ctx.a2a.profile_peers["dup"] = A2APeerConfig(url="http://profile")
    result = a2a_handler(ctx.ctx, "peer add dup http://session")
    assert isinstance(result, Handled)
    assert "dup" in ctx.ctx.a2a.session_peers
    assert "shadows profile entry" in ctx.buf.getvalue()


def test_peer_remove_drops_session_entry(ctx: _Captured):
    from jac.profiles import A2APeerConfig

    ctx.ctx.a2a.session_peers["ephemeral"] = A2APeerConfig(url="http://x")
    result = a2a_handler(ctx.ctx, "peer remove ephemeral")
    assert isinstance(result, Handled)
    assert "ephemeral" not in ctx.ctx.a2a.session_peers
    assert "✓ session peer removed" in ctx.buf.getvalue()


def test_peer_remove_reverts_to_profile_when_shadowed(ctx: _Captured):
    from jac.profiles import A2APeerConfig

    ctx.ctx.a2a.profile_peers["dup"] = A2APeerConfig(url="http://profile")
    ctx.ctx.a2a.session_peers["dup"] = A2APeerConfig(url="http://session")
    result = a2a_handler(ctx.ctx, "peer remove dup")
    assert isinstance(result, Handled)
    assert "dup" not in ctx.ctx.a2a.session_peers
    assert "dup" in ctx.ctx.a2a.profile_peers
    assert "reverted to profile entry" in ctx.buf.getvalue()


def test_peer_remove_for_unknown_session_peer_says_so(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "peer remove nope")
    assert isinstance(result, Handled)
    assert "no session peer named" in ctx.buf.getvalue()


def test_peer_remove_requires_name(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "peer remove")
    assert isinstance(result, Handled)
    assert "usage:" in ctx.buf.getvalue()


def test_peers_listing_shows_session_overriding_profile(ctx: _Captured):
    """When a session peer shadows a profile peer of the same name,
    `/a2a peers` shows both rows, with the session row first and the
    shadowed profile row greyed-out underneath."""
    from jac.profiles import A2APeerConfig

    ctx.ctx.a2a.profile_peers["dup"] = A2APeerConfig(
        url="http://profile-url", description="from profile"
    )
    ctx.ctx.a2a.session_peers["dup"] = A2APeerConfig(
        url="http://session-url", description="(added via /a2a peer add)"
    )
    result = a2a_handler(ctx.ctx, "peers")
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    # Both URLs surface
    assert "http://session-url" in out
    assert "http://profile-url" in out
    # Provenance tags rendered (brackets escaped in the source via raw string)
    assert "[session]" in out
    assert "shadowed profile" in out
