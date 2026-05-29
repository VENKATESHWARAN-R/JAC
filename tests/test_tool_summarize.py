"""Tests for the tool-result post-processor (Phase A.1)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jac.config import reset_settings_cache
from jac.runtime import tool_summarize as ts
from jac.runtime.tool_summarize import (
    get_summarizer_stats,
    is_strictly_cheaper,
    maybe_summarize_tool_result,
    reset_summarizer_stats,
    set_summarizer_model,
    should_summarize,
)
from jac.tools import jac_function_toolset, jac_tool
from jac.workspace import paths
from jac.workspace.session_ctx import set_current_session_id

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(paths, "project_root", lambda start=None: tmp_path)
    set_current_session_id("test-session")
    set_summarizer_model(None)
    reset_summarizer_stats()
    reset_settings_cache()
    yield
    set_current_session_id(None)
    set_summarizer_model(None)
    reset_summarizer_stats()
    reset_settings_cache()


@pytest.fixture
def stub_summarizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the real model call — return a deterministic summary."""

    async def fake(
        text: str, tool_name: str, model_id: str, template: str
    ) -> tuple[str, int, int] | None:
        summary = f"FAKE SUMMARY of {tool_name} ({len(text)} chars) via {model_id}"
        return summary, len(text) // 4, len(summary) // 4

    monkeypatch.setattr(ts, "_summarize_text", fake)


# ---------- pure helpers ----------


def test_should_summarize_respects_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_COST__SUMMARIZE_TOOLS", "[]")
    monkeypatch.setenv("JAC_COST__NO_SUMMARIZE_TOOLS", "[]")
    reset_settings_cache()
    from jac.config import get_settings

    settings = get_settings().cost
    assert should_summarize("foo", tool_summarizable=True, settings=settings) is True
    assert should_summarize("foo", tool_summarizable=False, settings=settings) is False


def test_no_summarize_overrides_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_COST__NO_SUMMARIZE_TOOLS", '["run_shell"]')
    reset_settings_cache()
    from jac.config import get_settings

    settings = get_settings().cost
    assert should_summarize("run_shell", tool_summarizable=True, settings=settings) is False


def test_force_on_overrides_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_COST__SUMMARIZE_TOOLS", '["read_file"]')
    reset_settings_cache()
    from jac.config import get_settings

    settings = get_settings().cost
    assert should_summarize("read_file", tool_summarizable=False, settings=settings) is True


def test_is_strictly_cheaper_known_pair() -> None:
    assert is_strictly_cheaper("anthropic:claude-haiku-4-5", "anthropic:claude-sonnet-4-6") is True
    assert is_strictly_cheaper("anthropic:claude-sonnet-4-6", "anthropic:claude-haiku-4-5") is False


def test_is_strictly_cheaper_same_model_returns_false() -> None:
    assert is_strictly_cheaper("anthropic:claude-haiku-4-5", "anthropic:claude-haiku-4-5") is False


def test_is_strictly_cheaper_unknown_returns_false() -> None:
    assert is_strictly_cheaper("anthropic:claude-haiku-4-5", "unknown:model") is False
    assert is_strictly_cheaper("unknown:tiny", "anthropic:claude-sonnet-4-6") is False


# ---------- maybe_summarize_tool_result ----------


async def test_below_threshold_passes_through(stub_summarizer: None) -> None:
    set_summarizer_model("anthropic:claude-haiku-4-5")
    out = await maybe_summarize_tool_result(
        tool_name="run_shell",
        raw_output="small output",
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
    )
    assert out == "small output"


async def test_above_threshold_summarizes(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    set_summarizer_model("anthropic:claude-haiku-4-5")
    big = "x" * 5_000  # ~1250 tokens at chars/4
    out = await maybe_summarize_tool_result(
        tool_name="run_shell",
        raw_output=big,
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
        tool_call_id="call-001",
    )
    assert isinstance(out, str)
    assert out.startswith("[AI-summarized via anthropic:claude-haiku-4-5")
    assert "FAKE SUMMARY of run_shell" in out
    # Cache file written and contains the original.
    cache_path = tmp_path / ".agents" / "cache" / "tool-results" / "test-session" / "call-001.txt"
    assert cache_path.exists()
    assert cache_path.read_text() == big


async def test_no_summarizer_passes_through(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    # Summarizer model intentionally not set.
    big = "x" * 5_000
    out = await maybe_summarize_tool_result(
        tool_name="run_shell",
        raw_output=big,
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
    )
    assert out == big


async def test_summarizer_not_cheaper_passes_through(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    # "Small" set to a strictly more expensive model than "current".
    set_summarizer_model("anthropic:claude-opus-4-7")
    big = "x" * 5_000
    out = await maybe_summarize_tool_result(
        tool_name="run_shell",
        raw_output=big,
        tool_summarizable=True,
        current_model="anthropic:claude-haiku-4-5",
    )
    assert out == big


async def test_decorator_off_passes_through(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    set_summarizer_model("anthropic:claude-haiku-4-5")
    big = "x" * 5_000
    out = await maybe_summarize_tool_result(
        tool_name="read_file",
        raw_output=big,
        tool_summarizable=False,  # decorator default
        current_model="anthropic:claude-sonnet-4-6",
    )
    assert out == big


async def test_force_on_overrides_decorator_default(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    monkeypatch.setenv("JAC_COST__SUMMARIZE_TOOLS", '["read_file"]')
    reset_settings_cache()
    set_summarizer_model("anthropic:claude-haiku-4-5")
    big = "x" * 5_000
    out = await maybe_summarize_tool_result(
        tool_name="read_file",
        raw_output=big,
        tool_summarizable=False,
        current_model="anthropic:claude-sonnet-4-6",
    )
    assert isinstance(out, str) and out.startswith("[AI-summarized via ")


async def test_summarizer_failure_falls_back_to_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()

    async def failing(text, tool_name, model_id, template):  # noqa: ANN
        return None

    monkeypatch.setattr(ts, "_summarize_text", failing)
    set_summarizer_model("anthropic:claude-haiku-4-5")
    big = "x" * 5_000
    out = await maybe_summarize_tool_result(
        tool_name="run_shell",
        raw_output=big,
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
    )
    assert out == big


async def test_non_string_output_is_serialized_for_sizing(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    set_summarizer_model("anthropic:claude-haiku-4-5")
    payload = [{"title": "x" * 1000, "url": "y" * 1000} for _ in range(20)]
    out = await maybe_summarize_tool_result(
        tool_name="web_search",
        raw_output=payload,
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
    )
    assert isinstance(out, str)
    assert "FAKE SUMMARY" in out


# ---------- end-to-end via the SummarizingToolset wrapper ----------


@jac_tool(summarizable=True)
def _giant_string_tool(reason: str) -> str:
    return "Z" * 50_000


@jac_tool
def _exact_tool(reason: str) -> str:
    return "Z" * 50_000


async def test_toolset_wrapper_routes_through_summarizer(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    set_summarizer_model("anthropic:claude-haiku-4-5")

    toolset = jac_function_toolset(_giant_string_tool, _exact_tool)
    assert "_giant_string_tool" in toolset.summarizable_tools
    assert "_exact_tool" not in toolset.summarizable_tools


# ---------- stats ----------


async def test_stats_accumulate_across_calls(
    stub_summarizer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS", "100")
    reset_settings_cache()
    set_summarizer_model("anthropic:claude-haiku-4-5")
    big = "x" * 5_000
    await maybe_summarize_tool_result(
        tool_name="run_shell",
        raw_output=big,
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
    )
    await maybe_summarize_tool_result(
        tool_name="web_search",
        raw_output=big,
        tool_summarizable=True,
        current_model="anthropic:claude-sonnet-4-6",
    )
    stats = get_summarizer_stats()
    assert stats.calls == 2
    assert stats.original_tokens > 0
    assert stats.summary_tokens > 0
    assert stats.saved_tokens > 0
    assert stats.summarizer_input_tokens > 0
