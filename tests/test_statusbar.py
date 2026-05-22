"""Tests for the persistent bottom-toolbar status line (1.7.b)."""

from __future__ import annotations

import pytest

from jac.cli.statusbar import (
    StatusState,
    _BranchCache,
    _ctx_color,
    format_toolbar,
    short_model,
    tier_for_model,
)
from jac.config import reset_settings_cache
from jac.profiles import Profile


@pytest.fixture(autouse=True)
def _reset_settings() -> None:
    reset_settings_cache()
    yield
    reset_settings_cache()


# ---------- pure helpers ----------


def test_tier_for_model_found() -> None:
    p = Profile(
        tiers={"small": ["a:1"], "medium": ["b:2", "b:3"], "large": ["c:4"]},
        active_tier="medium",
    )
    assert tier_for_model(p, "a:1") == "small"
    assert tier_for_model(p, "b:3") == "medium"
    assert tier_for_model(p, "c:4") == "large"


def test_tier_for_model_unknown_is_none() -> None:
    p = Profile(tiers={"medium": ["a:1"]}, active_tier="medium")
    assert tier_for_model(p, "x:9") is None


def test_tier_for_model_no_profile() -> None:
    assert tier_for_model(None, "any:model") is None


def test_short_model_colon() -> None:
    assert short_model("anthropic:claude-sonnet-4-5") == "claude-sonnet-4-5"


def test_short_model_slash_then_colon() -> None:
    # Gateway-style id; the *colon* is rightmost, so we strip to after the colon.
    assert short_model("gateway/openai:gpt-4o") == "gpt-4o"


def test_short_model_slash_only() -> None:
    # No colon, just a path separator.
    assert short_model("gateway/openai-models") == "openai-models"


def test_short_model_plain() -> None:
    assert short_model("plain") == "plain"


# ---------- ctx color thresholds ----------


def test_ctx_color_neutral_below_warn() -> None:
    assert _ctx_color(0) == "ansiwhite"
    assert _ctx_color(50) == "ansiwhite"


def test_ctx_color_yellow_at_warn() -> None:
    assert _ctx_color(60) == "ansiyellow"
    assert _ctx_color(65) == "ansiyellow"


def test_ctx_color_brightred_at_auto_compact() -> None:
    assert _ctx_color(70) == "ansibrightred"
    assert _ctx_color(80) == "ansibrightred"


def test_ctx_color_red_at_refuse() -> None:
    assert _ctx_color(85) == "ansired"
    assert _ctx_color(99) == "ansired"


# ---------- _BranchCache debouncing ----------


def test_branch_cache_debounces_subprocess_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated get() calls within the debounce window must not re-shell."""
    call_count = {"n": 0}

    def fake_check_output(cmd, **_kwargs):
        call_count["n"] += 1
        if "symbolic-ref" in cmd:
            return b"main\n"
        return b""  # status --porcelain → clean

    monkeypatch.setattr("jac.cli.statusbar.subprocess.check_output", fake_check_output)
    monkeypatch.setattr("jac.cli.statusbar.time.monotonic", lambda: 100.0)

    cache = _BranchCache()
    assert cache.get() == ("main", False)
    initial_calls = call_count["n"]
    assert initial_calls > 0

    # Same timestamp — second get() must NOT shell.
    cache.get()
    cache.get()
    assert call_count["n"] == initial_calls


def test_branch_cache_refreshes_after_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_check_output(cmd, **_kwargs):
        call_count["n"] += 1
        if "symbolic-ref" in cmd:
            return b"main\n"
        return b""

    monkeypatch.setattr("jac.cli.statusbar.subprocess.check_output", fake_check_output)

    now = {"t": 100.0}
    monkeypatch.setattr("jac.cli.statusbar.time.monotonic", lambda: now["t"])

    cache = _BranchCache()
    cache.get()
    initial = call_count["n"]

    # Advance past the debounce window — next get() should re-shell.
    now["t"] = 110.0
    cache.get()
    assert call_count["n"] > initial


def test_branch_cache_handles_no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """When git isn't on PATH (or we're outside a repo), return empty/clean."""

    def fake_check_output(cmd, **_kwargs):
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr("jac.cli.statusbar.subprocess.check_output", fake_check_output)

    cache = _BranchCache()
    assert cache.get() == ("", False)


def test_branch_cache_dirty_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_check_output(cmd, **_kwargs):
        if "symbolic-ref" in cmd:
            return b"main\n"
        return b"M src/foo.py\n"  # porcelain output → dirty

    monkeypatch.setattr("jac.cli.statusbar.subprocess.check_output", fake_check_output)

    cache = _BranchCache()
    assert cache.get() == ("main", True)


# ---------- format_toolbar ----------


def _state_with_profile() -> StatusState:
    p = Profile(
        tiers={
            "small": ["anthropic:claude-haiku-4-5"],
            "medium": ["anthropic:claude-sonnet-4-5"],
        },
        active_tier="medium",
    )
    return StatusState(
        model_id="anthropic:claude-sonnet-4-5",
        session_id="20260523T20-00-00",
        profile_name="claude",
        profile=p,
        message_history=[],
    )


def test_format_toolbar_shows_profile_tier_and_short_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the branch cache to avoid touching real git.
    monkeypatch.setattr(_BranchCache, "get", lambda self: ("main", False))

    rendered = str(format_toolbar(_state_with_profile()))
    assert "profile:" in rendered and "claude" in rendered
    assert "tier:" in rendered and "medium" in rendered
    assert "claude-sonnet-4-5" in rendered  # short model
    assert "branch:" in rendered and "main" in rendered
    assert "ctx:" in rendered and "0%/200k" in rendered
    assert "session:" in rendered and "20260523T20-00-00" in rendered


def test_format_toolbar_ad_hoc_model_shows_model_label_not_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the running model isn't in any tier we surface 'model:' not 'tier:'."""
    monkeypatch.setattr(_BranchCache, "get", lambda self: ("", False))

    p = Profile(tiers={"medium": ["anthropic:claude-sonnet-4-5"]}, active_tier="medium")
    state = StatusState(
        model_id="openai:gpt-99",  # not in any tier
        session_id="x",
        profile_name="claude",
        profile=p,
        message_history=[],
    )
    rendered = str(format_toolbar(state))
    assert "model:" in rendered
    assert "tier:" not in rendered
    assert "gpt-99" in rendered


def test_format_toolbar_no_profile_hides_profile_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--model PROVIDER:ID` startup leaves profile fields blank — don't render them."""
    monkeypatch.setattr(_BranchCache, "get", lambda self: ("", False))

    state = StatusState(
        model_id="anthropic:claude-opus-4-7",
        session_id="x",
        profile_name=None,
        profile=None,
        message_history=[],
    )
    rendered = str(format_toolbar(state))
    assert "profile:" not in rendered
    assert "tier:" not in rendered
    assert "model:" in rendered
    assert "claude-opus-4-7" in rendered


def test_format_toolbar_branch_dirty_marker_present_when_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_BranchCache, "get", lambda self: ("feature", True))
    rendered = str(format_toolbar(_state_with_profile()))
    assert "feature" in rendered
    assert ">*<" in rendered  # the dirty `*` is wrapped in an HTML style tag


def test_format_toolbar_omits_branch_when_no_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_BranchCache, "get", lambda self: ("", False))
    rendered = str(format_toolbar(_state_with_profile()))
    assert "branch:" not in rendered
