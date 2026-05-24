"""Tests for jac.cli.slash.handlers.a2a.

The slash handler is sync; async work happens in the REPL via the
:class:`StartA2AServer` / :class:`StopA2AServer` result types. So the
test surface here is small: parse-args correctness, the right result
type comes back, status/token gracefully handle "not running".

End-to-end server start/stop integration is exercised by
``test_a2a_server.py`` (PR1 covers the wiring; full request round-trips
land alongside PR2 outbound).
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

import pytest
from rich.console import Console

from jac.capabilities.a2a import make_a2a_capability
from jac.cli.slash.context import SlashContext
from jac.cli.slash.handlers.a2a import a2a_handler
from jac.cli.slash.handlers.a2a._args import parse_serve_args as _parse_serve_args
from jac.cli.slash.result import Handled, StartA2AServer
from jac.profiles import A2AProfileConfig, Profile
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

    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
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


# ---------- _parse_serve_args ----------


def test_parse_serve_args_uses_defaults_when_empty():
    host, port, unsafe = _parse_serve_args("", default_host="127.0.0.1", default_port=8001)
    assert (host, port, unsafe) == ("127.0.0.1", 8001, False)


def test_parse_serve_args_picks_up_port():
    _host, port, _unsafe = _parse_serve_args(
        "--port 9000", default_host="127.0.0.1", default_port=8001
    )
    assert port == 9000


def test_parse_serve_args_picks_up_host():
    host, _port, _ = _parse_serve_args(
        "--host 0.0.0.0", default_host="127.0.0.1", default_port=8001
    )
    assert host == "0.0.0.0"


def test_parse_serve_args_picks_up_unsafe():
    *_, unsafe = _parse_serve_args("--unsafe", default_host="127.0.0.1", default_port=8001)
    assert unsafe is True


def test_parse_serve_args_handles_all_three_in_any_order():
    host, port, unsafe = _parse_serve_args(
        "--unsafe --port 9000 --host 0.0.0.0",
        default_host="127.0.0.1",
        default_port=8001,
    )
    assert (host, port, unsafe) == ("0.0.0.0", 9000, True)


def test_parse_serve_args_rejects_unknown_flag():
    with pytest.raises(ValueError, match="unknown arg"):
        _parse_serve_args("--cool-feature", default_host="127.0.0.1", default_port=8001)


def test_parse_serve_args_rejects_non_integer_port():
    with pytest.raises(ValueError, match="must be an integer"):
        _parse_serve_args("--port banana", default_host="127.0.0.1", default_port=8001)


def test_parse_serve_args_rejects_out_of_range_port():
    with pytest.raises(ValueError, match="must be 1-65535"):
        _parse_serve_args("--port 70000", default_host="127.0.0.1", default_port=8001)


# ---------- handler dispatch ----------


def test_serve_returns_start_result(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "serve --port 9001")
    assert isinstance(result, StartA2AServer)
    assert result.port == 9001
    assert result.host == "127.0.0.1"  # profile default
    assert result.unsafe is False


def test_serve_with_unsafe_warns_and_returns_unsafe_flag(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "serve --unsafe")
    assert isinstance(result, StartA2AServer)
    assert result.unsafe is True
    # Loud red warning landed in the console
    assert "--unsafe" in ctx.buf.getvalue()


def test_serve_when_already_running_does_not_start_again(ctx: _Captured, monkeypatch):
    # Fake "server is running" without actually binding a port.
    class _FakeServer:
        is_running = True
        info = None

    ctx.ctx.a2a.server = _FakeServer()  # type: ignore[assignment]
    result = a2a_handler(ctx.ctx, "serve")
    assert isinstance(result, Handled)
    assert "already running" in ctx.buf.getvalue()


def test_stop_when_not_running_is_handled(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "stop")
    assert isinstance(result, Handled)
    assert "not running" in ctx.buf.getvalue()


def test_status_when_not_running(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "status")
    assert isinstance(result, Handled)
    assert "not running" in ctx.buf.getvalue()


def test_token_when_not_running(ctx: _Captured):
    result = a2a_handler(ctx.ctx, "token")
    assert isinstance(result, Handled)
    out = ctx.buf.getvalue()
    assert "not running" in out


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


# ---------- profile defaults ----------


def test_profile_defaults_used_when_no_flag(ctx: _Captured, monkeypatch):
    """When the profile sets a2a.port, /a2a serve uses it as the default."""
    ctx.ctx.profile.a2a = A2AProfileConfig(host="0.0.0.0", port=9999)  # type: ignore[union-attr]
    result = a2a_handler(ctx.ctx, "serve")
    assert isinstance(result, StartA2AServer)
    assert result.host == "0.0.0.0"
    assert result.port == 9999
