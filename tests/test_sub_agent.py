"""Tests for the spawn_sub_agent tool (Phase B).

Covers:

- Tier cascade (small → medium → large; never down)
- Unknown tier raises
- No tier available raises
- Depth cap = 1: ``sub_agent_capabilities()`` does NOT include
  ``SubAgentToolCapability``
- Packet rendering includes every section
- Budget rollup: ``UsageTracker.add_sub_agent`` updates session totals
  and writes a JSONL row tagged with the tier
- ``spawn_sub_agent`` fails fast when no capability is installed
- Full spawn (with the model call mocked) returns a tagged result
  string and bumps the stats
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.runtime import sub_agent as sa
from jac.runtime.events import EventBus
from jac.runtime.sub_agent import (
    SubAgentCapability,
    SubAgentTaskPacket,
    _render_packet,
    resolve_tier,
    set_sub_agent_capability,
    spawn_sub_agent,
)
from jac.runtime.sub_agent_usage import (
    get_sub_agent_stats,
    reset_sub_agent_stats,
    set_sub_agent_usage_recorder,
)
from jac.runtime.usage import BudgetLimits, UsageTracker
from jac.tools import is_jac_tool, is_summarizable

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_sub_agent_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # pydantic-ai's Anthropic provider validates the API key at Agent()
    # construction time, before our monkeypatched `Agent.run` ever fires.
    # A dummy key keeps construction quiet without hitting the network.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    set_sub_agent_capability(None)
    reset_sub_agent_stats()
    set_sub_agent_usage_recorder(None)
    yield
    set_sub_agent_capability(None)
    reset_sub_agent_stats()
    set_sub_agent_usage_recorder(None)


def _profile(**tiers: list[str]) -> Profile:
    return Profile(tiers=tiers, active_tier=next(iter(tiers)))


# ---------- tier cascade ----------


def test_resolve_tier_exact_match_no_cascade() -> None:
    p = _profile(small=["anthropic:claude-haiku-4-5"], medium=["anthropic:claude-sonnet-4-6"])
    r = resolve_tier(p, "small")
    assert r.resolved == "small"
    assert r.cascaded is False
    assert r.cascade_note is None
    assert r.model == "anthropic:claude-haiku-4-5"


def test_resolve_tier_cascades_up_when_requested_missing() -> None:
    p = _profile(medium=["anthropic:claude-sonnet-4-6"], large=["anthropic:claude-opus-4-7"])
    r = resolve_tier(p, "small")
    assert r.resolved == "medium"
    assert r.cascaded is True
    assert r.cascade_note == "requested 'small', cascaded up to 'medium'"


def test_resolve_tier_never_cascades_down() -> None:
    """Requesting `large` when only `small` exists must raise — falling
    back to `small` would silently exceed the cost budget the caller
    asked for."""
    p = _profile(small=["anthropic:claude-haiku-4-5"])
    with pytest.raises(JacConfigError, match=r"no tier ≥ 'large' configured"):
        resolve_tier(p, "large")


def test_resolve_tier_unknown_name_raises() -> None:
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    with pytest.raises(JacConfigError, match=r"unknown sub-agent tier 'xl'"):
        resolve_tier(p, "xl")


# ---------- depth cap ----------


def test_sub_agent_capability_factory_excludes_spawn_tool() -> None:
    """Structural enforcement of D40: sub-agents must not get the spawn
    capability in their toolset list."""
    from jac.capabilities.sub_agent import SubAgentToolCapability
    from jac.runtime.gru import sub_agent_capabilities

    caps = sub_agent_capabilities()
    assert not any(isinstance(c, SubAgentToolCapability) for c in caps), (
        "sub_agent_capabilities() must exclude SubAgentToolCapability — depth cap = 1 (D40)"
    )


def test_main_gru_capability_list_includes_spawn_tool() -> None:
    """Counterpart: the main Gru's default capability list DOES include the
    spawn capability. Catches accidental removal."""
    from jac.capabilities.sub_agent import SubAgentToolCapability
    from jac.runtime.gru import _default_tool_capabilities

    caps = _default_tool_capabilities()
    assert any(isinstance(c, SubAgentToolCapability) for c in caps)


# ---------- @jac_tool surface ----------


def test_spawn_sub_agent_is_a_jac_tool() -> None:
    assert is_jac_tool(spawn_sub_agent)


def test_spawn_sub_agent_is_summarizable() -> None:
    """Spawn returns the sub-agent's final response — often a couple of
    paragraphs. Marking it summarizable lets the post-processor compress
    pathological cases (e.g. the sub-agent dumped a whole file)."""
    assert is_summarizable(spawn_sub_agent)


# ---------- packet rendering ----------


def test_render_packet_includes_every_populated_section() -> None:
    packet = SubAgentTaskPacket(
        objective="Summarize the auth module.",
        success_criteria=["covers the login flow", "≤ 3 paragraphs"],
        relevant_paths=["src/auth/", "tests/test_auth.py"],
        forbidden_actions=["do not run pytest"],
        expected_output="Three paragraphs of prose.",
        max_turns=5,
    )
    rendered = _render_packet(packet, "BASE PROMPT")
    assert "BASE PROMPT" in rendered
    assert "## Objective" in rendered
    assert "Summarize the auth module." in rendered
    assert "## Success criteria" in rendered
    assert "- covers the login flow" in rendered
    assert "## Relevant paths" in rendered
    assert "`src/auth/`" in rendered
    assert "## Forbidden actions" in rendered
    assert "- do not run pytest" in rendered
    assert "## Expected output shape" in rendered
    assert "Three paragraphs" in rendered
    assert "at most 5 model calls" in rendered


def test_render_packet_skips_empty_optional_sections() -> None:
    """A minimal packet shouldn't show empty headers — they'd just bulk
    the sub-agent's prompt for nothing."""
    packet = SubAgentTaskPacket(objective="X")
    rendered = _render_packet(packet, "BASE")
    assert "## Success criteria" not in rendered
    assert "## Relevant paths" not in rendered
    assert "## Forbidden actions" not in rendered
    assert "## Expected output shape" not in rendered


# ---------- budget rollup ----------


async def test_add_sub_agent_bumps_session_counters_and_writes_jsonl(
    tmp_path: Path,
) -> None:
    usage_file = tmp_path / "usage.jsonl"
    tracker = UsageTracker(
        session_id="s1",
        bus=EventBus(),
        usage_file=usage_file,
        limits=BudgetLimits(
            session_input_tokens=None,
            session_total_tokens=None,
            project_total_tokens=None,
            warn_pct=80,
            hardstop_pct=100,
        ),
    )
    await tracker.add_sub_agent(input_tokens=4_000, output_tokens=600, tier="medium")
    assert tracker.counters.input_tokens == 4_000
    assert tracker.counters.output_tokens == 600
    assert tracker.counters.total_tokens == 4_600
    # JSONL row tagged with the tier so baseline reconstruction can split.
    line = usage_file.read_text().strip()
    entry = json.loads(line)
    assert entry["kind"] == "sub_agent:medium"
    assert entry["input_tokens"] == 4_000
    assert entry["output_tokens"] == 600


# ---------- spawn_sub_agent end-to-end (model mocked) ----------


async def test_spawn_fails_fast_when_no_capability_installed() -> None:
    set_sub_agent_capability(None)
    with pytest.raises(JacConfigError, match="not available in this session"):
        await spawn_sub_agent(
            reason="test",
            task_summary="x",
            tier="medium",
            task_packet={"objective": "do thing"},
        )


async def test_spawn_runs_sub_agent_and_returns_tagged_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end happy path with the Agent.run call faked.

    Verifies: capability factory invoked, tier resolved, result tagged
    with `[sub-agent tier=... model=... turns=... exit=ok]` header,
    stats incremented.
    """
    # Build a capability whose factory returns an empty list — no
    # filesystem/shell capabilities so the mocked agent run is hermetic.
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _allowed: [],
        )
    )

    class _FakeUsage:
        requests = 2
        input_tokens = 1_200
        output_tokens = 150

    class _FakeRunResult:
        output = "the answer"

        def usage(self) -> _FakeUsage:
            return _FakeUsage()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _FakeRunResult:
        assert "do the thing" in prompt
        return _FakeRunResult()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agent(
        reason="delegate",
        task_summary="summarize",
        tier="medium",
        task_packet={"objective": "do the thing", "max_turns": 5},
    )
    assert isinstance(out, str)
    assert out.startswith("[sub-agent tier=medium model=anthropic:claude-sonnet-4-6")
    assert "turns=2" in out
    assert "exit=ok" in out
    assert "the answer" in out

    stats = get_sub_agent_stats()
    assert stats.spawns == 1
    assert stats.input_tokens == 1_200
    assert stats.output_tokens == 150
    assert stats.by_tier == {"medium": 1_350}


async def test_spawn_with_tier_cascade_notes_in_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])  # no small
    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    )

    class _U:
        requests = 1
        input_tokens = 10
        output_tokens = 5

    class _R:
        output = "ok"

        def usage(self) -> _U:
            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agent(
        reason="r",
        task_summary="s",
        tier="small",
        task_packet={"objective": "do x"},
    )
    assert "cascaded up to 'medium'" in out


async def test_spawn_error_path_returns_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    async def boom(self: Any, prompt: str, **_kwargs: Any) -> Any:
        raise RuntimeError("model unreachable")

    monkeypatch.setattr("pydantic_ai.Agent.run", boom)

    out = await spawn_sub_agent(
        reason="r",
        task_summary="s",
        tier="medium",
        task_packet={"objective": "do x"},
    )
    assert "exit=error" in out
    assert "model unreachable" in out
    # Stats should NOT be bumped on the error path.
    assert get_sub_agent_stats().spawns == 0


# ---------- recorder forwarding ----------


async def test_recorder_is_invoked_for_each_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REPL-installed recorder must fire so spawn cost rolls into
    UsageTracker. Test it independently of the actual tracker."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    received: list[tuple[int, int, str]] = []

    async def recorder(in_t: int, out_t: int, tier: str) -> None:
        received.append((in_t, out_t, tier))

    set_sub_agent_usage_recorder(recorder)

    class _U:
        requests = 1
        input_tokens = 500
        output_tokens = 50

    class _R:
        output = "ok"

        def usage(self) -> _U:
            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "do"}
    )
    assert received == [(500, 50, "medium")]


# ---------- /tokens line wired ----------


def test_tokens_handler_shows_sub_agent_line_when_spawned() -> None:
    """The /tokens handler reads from sub_agent_usage stats — bump them
    directly and confirm the line appears."""
    import asyncio
    from io import StringIO

    from rich.console import Console

    from jac.cli.slash import SlashContext, dispatch
    from jac.runtime.session import Session

    reset_sub_agent_stats()
    stats = get_sub_agent_stats()
    stats.spawns = 3
    stats.input_tokens = 9_000
    stats.output_tokens = 1_000
    stats.by_tier["small"] = 4_000
    stats.by_tier["medium"] = 6_000

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    tracker = UsageTracker(
        session_id="s1",
        bus=EventBus(),
        usage_file=None,
        limits=BudgetLimits(
            session_input_tokens=None,
            session_total_tokens=None,
            project_total_tokens=None,
            warn_pct=80,
            hardstop_pct=100,
        ),
    )
    asyncio.run(tracker.add_sub_agent(input_tokens=9_000, output_tokens=1_000, tier="small"))

    ctx = SlashContext(
        console=console,
        session=Session(session_id="s1", message_history=[]),
        profile_name=None,
        profile=None,
        model_id="anthropic:claude-sonnet-4-6",
        usage_tracker=tracker,
    )
    dispatch("/tokens", ctx)
    out = buf.getvalue()
    assert "sub_agents:" in out
    assert "spawns=3" in out
    assert "small=4,000" in out
    assert "medium=6,000" in out


# Silence the "imported but unused" warning when the module-level access
# is what triggers the import. The fake usage classes above intentionally
# don't subclass RunUsage so we don't accidentally pull in pydantic-ai
# validation; the .usage() method just has to return something with the
# expected attributes.
_ = sa
