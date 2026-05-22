"""Tests for the slash command registry + dispatch + first batch of handlers."""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from jac.cli.slash import (
    Exit,
    Handled,
    SlashContext,
    SwitchSession,
    UnknownSlashCommand,
    command_names,
    dispatch,
    parse,
)
from jac.cli.slash.registry import SLASH_COMMANDS, SlashCommand, register
from jac.errors import JacConfigError
from jac.runtime.session import Session
from jac.workspace import paths

# ---------- fixtures ----------


@pytest.fixture
def ctx() -> SlashContext:
    """A handler context with a captured stdout console + a fresh session."""
    buf = StringIO()
    return SlashContext(
        console=Console(file=buf, force_terminal=False, width=120, record=True),
        session=Session(session_id="20260523T00-00-00", message_history=[]),
        profile_name="claude",
        model_id="anthropic:claude-sonnet-4-5",
    )


@pytest.fixture
def isolated_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect project_sessions_dir() to a tmp dir for session-related tests."""
    sessions_dir = tmp_path / ".agents" / "sessions"
    sessions_dir.mkdir(parents=True)
    # Clear find_project_root's @cache BEFORE the monkeypatch replaces it.
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(paths, "project_sessions_dir", lambda: sessions_dir)
    monkeypatch.setattr(paths, "find_project_root", lambda start=None: tmp_path)
    yield sessions_dir


# ---------- registry / parse / dispatch ----------


def test_parse_strips_leading_slash_and_partitions() -> None:
    assert parse("/help") == ("help", "")
    assert parse("/resume 20260523-1200") == ("resume", "20260523-1200")
    # Multiple spaces between slash and command name are tolerated.
    assert parse("/  hello world") == ("hello", "world")
    # Case-insensitive name match
    assert parse("/HELP") == ("help", "")


def test_parse_rejects_non_slash() -> None:
    with pytest.raises(ValueError, match="must start with '/'"):
        parse("help")


def test_dispatch_routes_to_handler(ctx: SlashContext) -> None:
    result = dispatch("/help", ctx)
    assert isinstance(result, Handled)


def test_dispatch_raises_on_unknown(ctx: SlashContext) -> None:
    with pytest.raises(UnknownSlashCommand) as info:
        dispatch("/nope", ctx)
    assert info.value.name == "nope"


def test_duplicate_register_raises() -> None:
    @register("__test_dup__", summary="x", usage="/__test_dup__")
    def _h(ctx: SlashContext, args: str) -> Handled:
        return Handled()

    try:
        with pytest.raises(RuntimeError, match="already registered"):

            @register("__test_dup__", summary="y", usage="/__test_dup__")
            def _h2(ctx: SlashContext, args: str) -> Handled:
                return Handled()

    finally:
        SLASH_COMMANDS.pop("__test_dup__", None)


def test_command_names_includes_first_batch() -> None:
    names = command_names()
    for expected in ("help", "exit", "clear", "sessions", "resume"):
        assert expected in names


def test_slash_command_dataclass_fields() -> None:
    cmd = SLASH_COMMANDS["help"]
    assert isinstance(cmd, SlashCommand)
    assert cmd.name == "help"
    assert callable(cmd.handler)


# ---------- /help ----------


def test_help_lists_every_registered_command(ctx: SlashContext) -> None:
    dispatch("/help", ctx)
    output = ctx.console.export_text()
    for name in command_names():
        assert f"/{name}" in output


# ---------- /exit ----------


def test_exit_returns_exit_result(ctx: SlashContext) -> None:
    assert isinstance(dispatch("/exit", ctx), Exit)


# ---------- /clear ----------


def test_clear_returns_switch_with_fresh_session(ctx: SlashContext) -> None:
    result = dispatch("/clear", ctx)
    assert isinstance(result, SwitchSession)
    assert result.session.session_id != ctx.session.session_id
    assert result.session.message_history == []


# ---------- /sessions ----------


def test_sessions_lists_known_ids(ctx: SlashContext, isolated_sessions: Path) -> None:
    # Pre-populate a couple of session dirs.
    for sid in ("20260101T00-00-00", "20260201T00-00-00"):
        d = isolated_sessions / sid
        d.mkdir()
        (d / "messages.json").write_text("[]")
    dispatch("/sessions", ctx)
    out = ctx.console.export_text()
    assert "20260101T00-00-00" in out
    assert "20260201T00-00-00" in out
    assert "(latest)" in out


def test_sessions_when_empty(ctx: SlashContext, isolated_sessions: Path) -> None:
    dispatch("/sessions", ctx)
    out = ctx.console.export_text()
    assert "no sessions yet" in out


# ---------- /resume ----------


def test_resume_no_arg_loads_latest(ctx: SlashContext, isolated_sessions: Path) -> None:
    for sid in ("20260101T00-00-00", "20260201T00-00-00"):
        d = isolated_sessions / sid
        d.mkdir()
        (d / "messages.json").write_text("[]")
    result = dispatch("/resume", ctx)
    assert isinstance(result, SwitchSession)
    assert result.session.session_id == "20260201T00-00-00"


def test_resume_specific_id(ctx: SlashContext, isolated_sessions: Path) -> None:
    d = isolated_sessions / "20260101T00-00-00"
    d.mkdir()
    (d / "messages.json").write_text("[]")
    result = dispatch("/resume 20260101T00-00-00", ctx)
    assert isinstance(result, SwitchSession)
    assert result.session.session_id == "20260101T00-00-00"


def test_resume_unknown_id_surfaces_error(ctx: SlashContext, isolated_sessions: Path) -> None:
    result = dispatch("/resume nope", ctx)
    assert isinstance(result, Handled)
    assert "no session" in ctx.console.export_text()


def test_resume_no_sessions_surfaces_error(ctx: SlashContext, isolated_sessions: Path) -> None:
    result = dispatch("/resume", ctx)
    assert isinstance(result, Handled)
    assert "no sessions" in ctx.console.export_text()


def test_resume_already_on_session_is_noop(ctx: SlashContext, isolated_sessions: Path) -> None:
    d = isolated_sessions / ctx.session.session_id
    d.mkdir()
    (d / "messages.json").write_text("[]")
    result = dispatch(f"/resume {ctx.session.session_id}", ctx)
    assert isinstance(result, Handled)
    assert "already on" in ctx.console.export_text()


# ---------- Session.resume raises ----------


def test_resume_handler_catches_jacconfigerror(
    ctx: SlashContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_id: str) -> Session:
        raise JacConfigError("forced failure")

    monkeypatch.setattr(Session, "resume", staticmethod(_raise))
    result = dispatch("/resume some-id", ctx)
    assert isinstance(result, Handled)
    assert "forced failure" in ctx.console.export_text()
