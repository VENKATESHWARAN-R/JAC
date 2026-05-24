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
from jac.cli.slash.handlers.a2a import _parse_serve_args, a2a_handler
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


# ---------- profile defaults ----------


def test_profile_defaults_used_when_no_flag(ctx: _Captured, monkeypatch):
    """When the profile sets a2a.port, /a2a serve uses it as the default."""
    ctx.ctx.profile.a2a = A2AProfileConfig(host="0.0.0.0", port=9999)  # type: ignore[union-attr]
    result = a2a_handler(ctx.ctx, "serve")
    assert isinstance(result, StartA2AServer)
    assert result.host == "0.0.0.0"
    assert result.port == 9999
