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
from jac.runtime.control import ControlResult
from jac.runtime.session import Session
from jac.workspace import paths

# ---------- fixtures ----------


class _FakeController:
    """Records control-plane verb calls and returns a success ControlResult.

    The mutating handlers (``/model``, ``/profile``, ``/mcp``, ``/mode``) are
    thin adapters that call ``ctx.controller`` and render the result; here we
    assert they call the right verb. The control plane's own behavior (rebuild,
    rollback, persistence) is tested directly in ``test_control.py``.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def switch_model(self, model_id: str) -> ControlResult:
        self.calls.append(("switch_model", model_id))
        return ControlResult(
            True, f"switched to {model_id}", {"model": model_id, "profile": "claude"}
        )

    def switch_profile(self, name: str) -> ControlResult:
        self.calls.append(("switch_profile", name))
        return ControlResult(
            True, f"switched to {name}-model", {"model": f"{name}-model", "profile": name}
        )

    def set_mcp_enabled(self, name: str, enabled: bool) -> ControlResult:
        self.calls.append(("set_mcp_enabled", name, enabled))
        verb = "enabled" if enabled else "disabled"
        return ControlResult(True, f"{verb} MCP server {name}")

    def reload_mcp(self) -> ControlResult:
        self.calls.append(("reload_mcp",))
        return ControlResult(True, "reloaded MCP catalog (0 servers enabled)")

    def reload_skills(self) -> ControlResult:
        self.calls.append(("reload_skills",))
        return ControlResult(True, "reloaded skills (0 available)")

    def refresh_toolsets(self, *, note: str = "") -> ControlResult:
        self.calls.append(("refresh_toolsets", note))
        return ControlResult(True, note or "toolsets refreshed")


@pytest.fixture
def ctx() -> SlashContext:
    """A handler context with a captured stdout console + a fresh session."""
    from jac.profiles import Profile

    buf = StringIO()
    profile = Profile(
        tiers={
            "small": ["anthropic:claude-haiku-4-5"],
            "medium": ["anthropic:claude-sonnet-4-5"],
            "large": ["anthropic:claude-opus-4-7"],
        },
        active_tier="medium",
    )
    return SlashContext(
        console=Console(file=buf, force_terminal=False, width=120, record=True),
        session=Session(session_id="20260523T00-00-00", message_history=[]),
        profile_name="claude",
        profile=profile,
        model_id="anthropic:claude-sonnet-4-5",
        controller=_FakeController(),  # type: ignore[arg-type]
    )


@pytest.fixture
def isolated_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect project_sessions_dir() to a tmp dir for session-related tests."""
    sessions_dir = tmp_path / ".agents" / "sessions"
    sessions_dir.mkdir(parents=True)
    # Patch project_root so project_state_root → tmp_path/.agents and every
    # session helper (imported directly into session.py) follows. Cache
    # clearing is handled by the autouse _clear_root_caches in conftest.
    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
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


def _make_session_dir(isolated_sessions: Path, sid: str) -> None:
    d = isolated_sessions / sid
    d.mkdir()
    (d / "messages.json").write_text("[]")


def test_sessions_delete_removes_session(ctx: SlashContext, isolated_sessions: Path) -> None:
    _make_session_dir(isolated_sessions, "2026-05-01T10-00-00")
    dispatch("/sessions delete 2026-05-01T10-00-00", ctx)
    assert "deleted" in ctx.console.export_text()
    assert not (isolated_sessions / "2026-05-01T10-00-00").exists()


def test_sessions_delete_refuses_active_session(ctx: SlashContext, isolated_sessions: Path) -> None:
    _make_session_dir(isolated_sessions, ctx.session.session_id)
    dispatch(f"/sessions delete {ctx.session.session_id}", ctx)
    assert "active session" in ctx.console.export_text()
    assert (isolated_sessions / ctx.session.session_id).exists()  # not removed


def test_sessions_prune_previews_without_yes(ctx: SlashContext, isolated_sessions: Path) -> None:
    _make_session_dir(isolated_sessions, "2026-01-01T10-00-00")
    dispatch("/sessions prune 1d", ctx)
    out = ctx.console.export_text()
    assert "2026-01-01T10-00-00" in out
    assert "to delete them" in out
    # Preview only — still on disk.
    assert (isolated_sessions / "2026-01-01T10-00-00").exists()


def test_sessions_prune_deletes_with_yes(ctx: SlashContext, isolated_sessions: Path) -> None:
    _make_session_dir(isolated_sessions, "2026-01-01T10-00-00")
    dispatch("/sessions prune 1d yes", ctx)
    assert "pruned" in ctx.console.export_text()
    assert not (isolated_sessions / "2026-01-01T10-00-00").exists()


def test_sessions_prune_rejects_bad_duration(ctx: SlashContext, isolated_sessions: Path) -> None:
    dispatch("/sessions prune nonsense", ctx)
    assert "invalid duration" in ctx.console.export_text()


# ---------- /memory ----------


@pytest.fixture
def isolated_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point user + project memory at tmp_path for /memory dispatch tests."""
    monkeypatch.setattr(paths, "USER_MEMORY_FILE", tmp_path / "user-memory.md")
    monkeypatch.setattr(paths, "project_memory_file", lambda: tmp_path / "project-memory.md")
    monkeypatch.setattr(paths, "in_project", lambda start=None: True)
    yield tmp_path


def test_memory_empty_reports_not_created(ctx: SlashContext, isolated_memory: Path) -> None:
    result = dispatch("/memory", ctx)
    assert isinstance(result, Handled)
    out = ctx.console.export_text()
    assert "User memory" in out and "Project memory" in out
    assert "not created yet" in out


def test_memory_lists_stored_entries(ctx: SlashContext, isolated_memory: Path) -> None:
    from jac.capabilities.memory import remember

    remember(reason="r", content="uses uv, not pip", category="convention", scope="user")
    dispatch("/memory user", ctx)
    out = ctx.console.export_text()
    assert "Conventions" in out
    assert "uses uv, not pip" in out


def test_memory_rejects_unknown_scope(ctx: SlashContext, isolated_memory: Path) -> None:
    dispatch("/memory bogus", ctx)
    assert "unknown scope" in ctx.console.export_text()


# ---------- /remember + /forget (user-driven) ----------


def test_remember_slash_stores_entry(ctx: SlashContext, isolated_memory: Path) -> None:
    result = dispatch("/remember user convention uses uv, not pip", ctx)
    assert isinstance(result, Handled)
    assert "stored under Conventions" in ctx.console.export_text()
    assert "uses uv, not pip" in paths.USER_MEMORY_FILE.read_text()


def test_remember_slash_multiword_content_preserved(
    ctx: SlashContext, isolated_memory: Path
) -> None:
    dispatch("/remember user fact tests live in the tests directory", ctx)
    assert "tests live in the tests directory" in paths.USER_MEMORY_FILE.read_text()


def test_remember_slash_bad_scope(ctx: SlashContext, isolated_memory: Path) -> None:
    dispatch("/remember nope convention x", ctx)
    assert "bad scope" in ctx.console.export_text()


def test_remember_slash_bad_category(ctx: SlashContext, isolated_memory: Path) -> None:
    dispatch("/remember user bogus some text", ctx)
    assert "bad category" in ctx.console.export_text()


def test_remember_slash_too_few_args_shows_usage(ctx: SlashContext, isolated_memory: Path) -> None:
    dispatch("/remember user convention", ctx)
    assert "usage:" in ctx.console.export_text()


def test_forget_slash_removes_entry(ctx: SlashContext, isolated_memory: Path) -> None:
    dispatch("/remember user convention uses uv, not pip", ctx)
    dispatch("/forget user uses uv, not pip", ctx)
    assert "removed from Conventions" in ctx.console.export_text()
    assert "uses uv, not pip" not in paths.USER_MEMORY_FILE.read_text()


def test_forget_slash_no_match_reports_error(ctx: SlashContext, isolated_memory: Path) -> None:
    dispatch("/remember user convention something real", ctx)
    dispatch("/forget user not present", ctx)
    assert "no entry matching" in ctx.console.export_text()


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


# ---------- /model ----------


def test_model_explicit_id_drives_switch(ctx: SlashContext) -> None:
    result = dispatch("/model anthropic:claude-opus-4-7", ctx)
    assert isinstance(result, Handled)
    assert ("switch_model", "anthropic:claude-opus-4-7") in ctx.controller.calls  # type: ignore[union-attr]
    assert "switched to" in ctx.console.export_text()


def test_model_explicit_id_already_active_is_noop(ctx: SlashContext) -> None:
    result = dispatch(f"/model {ctx.model_id}", ctx)
    assert isinstance(result, Handled)
    assert "already on" in ctx.console.export_text()


def test_model_explicit_id_outside_tiers_warns(ctx: SlashContext) -> None:
    result = dispatch("/model openai:gpt-99", ctx)
    assert isinstance(result, Handled)
    assert ("switch_model", "openai:gpt-99") in ctx.controller.calls  # type: ignore[union-attr]
    assert "isn't in profile" in ctx.console.export_text()


def test_model_picker_selects_by_index(ctx: SlashContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.cli.slash.handlers import model as model_mod

    # Picker enumerates: 1=small (haiku), 2=medium (sonnet, active), 3=large (opus)
    monkeypatch.setattr(model_mod.Prompt, "ask", lambda *a, **kw: "3")
    result = dispatch("/model", ctx)
    assert isinstance(result, Handled)
    assert ("switch_model", "anthropic:claude-opus-4-7") in ctx.controller.calls  # type: ignore[union-attr]


def test_model_picker_cancel(ctx: SlashContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from jac.cli.slash.handlers import model as model_mod

    monkeypatch.setattr(model_mod.Prompt, "ask", lambda *a, **kw: "c")
    result = dispatch("/model", ctx)
    assert isinstance(result, Handled)
    assert "cancelled" in ctx.console.export_text()


def test_model_picker_selecting_active_is_noop(
    ctx: SlashContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jac.cli.slash.handlers import model as model_mod

    # Index 2 is the medium tier (the active model).
    monkeypatch.setattr(model_mod.Prompt, "ask", lambda *a, **kw: "2")
    result = dispatch("/model", ctx)
    assert isinstance(result, Handled)
    assert "already on" in ctx.console.export_text()


def test_model_picker_no_profile_directs_to_explicit_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buf = StringIO()
    ctx = SlashContext(
        console=Console(file=buf, force_terminal=False, width=120, record=True),
        session=Session(session_id="x", message_history=[]),
        profile_name=None,
        profile=None,
        model_id="anthropic:claude-sonnet-4-5",
    )
    result = dispatch("/model", ctx)
    assert isinstance(result, Handled)
    assert "no profile" in ctx.console.export_text()


# ---------- /profile ----------


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    import yaml

    user_jac = tmp_path / ".jac"
    user_jac.mkdir()
    cfg = user_jac / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "default_profile": "claude",
                "profiles": {
                    "claude": {
                        "tiers": {
                            "small": ["anthropic:claude-haiku-4-5"],
                            "medium": ["anthropic:claude-sonnet-4-5"],
                        },
                        "active_tier": "medium",
                    },
                    "openai": {
                        "tiers": {"medium": ["openai:gpt-4o"]},
                        "active_tier": "medium",
                    },
                },
            }
        )
    )
    monkeypatch.setattr(paths, "USER_CONFIG_FILE", cfg)
    yield cfg


def test_profile_no_arg_lists_with_active_marker(ctx: SlashContext, isolated_config: Path) -> None:
    result = dispatch("/profile", ctx)
    assert isinstance(result, Handled)
    out = ctx.console.export_text()
    assert "claude" in out
    assert "openai" in out
    assert "(active)" in out


def test_profile_switch_drives_switch(ctx: SlashContext, isolated_config: Path) -> None:
    result = dispatch("/profile openai", ctx)
    assert isinstance(result, Handled)
    assert ("switch_profile", "openai") in ctx.controller.calls  # type: ignore[union-attr]
    assert "switched to" in ctx.console.export_text()


def test_profile_switch_to_same_is_noop(ctx: SlashContext, isolated_config: Path) -> None:
    result = dispatch("/profile claude", ctx)
    assert isinstance(result, Handled)
    assert "already on" in ctx.console.export_text()


def test_profile_switch_to_unknown_surfaces_error(ctx: SlashContext, isolated_config: Path) -> None:
    result = dispatch("/profile does-not-exist", ctx)
    assert isinstance(result, Handled)
    assert "no profile" in ctx.console.export_text()


# ---------- prompt-toolkit slash-only completer ----------


def _complete(text: str) -> list[str]:
    """Run the REPL's slash-only completer against ``text`` and return suggestions."""
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from jac.cli.repl import _SlashOnlyCompleter

    completer = _SlashOnlyCompleter(["help", "exit", "clear", "sessions", "resume"])
    document = Document(text=text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(document, CompleteEvent())]


def test_completer_fires_on_slash_prefix() -> None:
    suggestions = _complete("/")
    assert "/help" in suggestions
    assert "/exit" in suggestions


def test_completer_filters_by_typed_prefix() -> None:
    suggestions = _complete("/re")
    assert "/resume" in suggestions
    assert "/help" not in suggestions


def test_completer_silent_on_plain_text() -> None:
    """Typing regular prose must NOT trigger the slash dropdown — the bug fix."""
    assert _complete("hello world") == []
    assert _complete("write a file") == []


def test_completer_silent_after_first_word_completes() -> None:
    """Once the user has typed a space, slash-command suggestions stop —
    the rest of the line is arguments, not a command name."""
    assert _complete("/model ") == []
    assert _complete("/model anthropic:") == []


def test_completer_silent_on_empty_input() -> None:
    assert _complete("") == []


# ---------- /mode, /compact, /context (v0.7.0) ----------


@pytest.fixture
def _reset_session_policy() -> Iterator[None]:
    from jac.config import set_session_context_budget
    from jac.runtime import modes

    modes.reset_mode()
    set_session_context_budget(None)
    yield
    modes.reset_mode()
    set_session_context_budget(None)


def test_mode_no_args_shows_current(ctx: SlashContext, _reset_session_policy: None) -> None:
    result = dispatch("/mode", ctx)
    assert isinstance(result, Handled)
    assert "normal" in ctx.console.export_text()


def test_mode_switch_to_plan_rebuilds(ctx: SlashContext, _reset_session_policy: None) -> None:
    from jac.runtime import modes

    result = dispatch("/mode plan", ctx)
    assert isinstance(result, Handled)
    assert modes.get_mode() == "plan"
    # The mode is set first (build_gru reads it), then Gru is rebuilt via the
    # control plane so the new mode's system-prompt guidance applies.
    assert ("refresh_toolsets", "") in ctx.controller.calls  # type: ignore[union-attr]


def test_mode_unknown_is_rejected(ctx: SlashContext, _reset_session_policy: None) -> None:
    from jac.runtime import modes

    result = dispatch("/mode bogus", ctx)
    assert isinstance(result, Handled)
    assert "unknown mode" in ctx.console.export_text()
    assert modes.get_mode() == "normal"


def test_compact_returns_compactnow(ctx: SlashContext) -> None:
    from jac.cli.slash import CompactNow

    assert isinstance(dispatch("/compact", ctx), CompactNow)


def test_context_no_args_shows_budget(ctx: SlashContext, _reset_session_policy: None) -> None:
    result = dispatch("/context", ctx)
    assert isinstance(result, Handled)
    assert "context budget" in ctx.console.export_text()


def test_context_sets_session_override(ctx: SlashContext, _reset_session_policy: None) -> None:
    from jac.config import get_session_context_override

    result = dispatch("/context 400k", ctx)
    assert isinstance(result, Handled)
    assert get_session_context_override() == 400_000


def test_context_reset_clears_override(ctx: SlashContext, _reset_session_policy: None) -> None:
    from jac.config import get_session_context_override

    dispatch("/context 400k", ctx)
    dispatch("/context reset", ctx)
    assert get_session_context_override() is None


def test_context_clamps_to_ceiling(ctx: SlashContext, _reset_session_policy: None) -> None:
    from jac.config import MAX_CONTEXT_CEILING, get_session_context_override

    dispatch("/context 9m", ctx)
    assert get_session_context_override() == MAX_CONTEXT_CEILING
    assert "clamped" in ctx.console.export_text()


def test_context_rejects_junk(ctx: SlashContext, _reset_session_policy: None) -> None:
    from jac.config import get_session_context_override

    result = dispatch("/context wat", ctx)
    assert isinstance(result, Handled)
    assert get_session_context_override() is None
    assert "could not parse" in ctx.console.export_text()
