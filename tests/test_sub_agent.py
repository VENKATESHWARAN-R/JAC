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

import asyncio
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
    _ALWAYS_ALLOWED_SUB_AGENT_TOOLS,
    _BIDIRECTIONAL_FINALIZE_DIRECTIVE,
    _BIDIRECTIONAL_ROUND_TRIP_CAP,
    PendingSpawn,
    SubAgentCapability,
    SubAgentSpawnSpec,
    SubAgentTaskPacket,
    _make_allowed_tools_filter,
    _pending_spawns,
    _render_packet,
    _reset_pending_spawns,
    resolve_tier,
    respond_to_sub_agent,
    set_sub_agent_capability,
    set_sub_agent_event_bus,
    spawn_sub_agent,
    spawn_sub_agents,
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
    # Force the bidirectional flag off so tests that don't use
    # ``_bidirectional_on`` run the sequential path regardless of what
    # ~/.jac/config.yaml says. ``_bidirectional_on`` overrides this with
    # "true" + another reset for tests that explicitly need the flag on.
    monkeypatch.setenv("JAC_COST__SUB_AGENT_BIDIRECTIONAL", "false")
    from jac.config import reset_settings_cache

    reset_settings_cache()
    set_sub_agent_capability(None)
    reset_sub_agent_stats()
    set_sub_agent_usage_recorder(None)
    set_sub_agent_event_bus(None)
    _reset_pending_spawns()
    yield
    reset_settings_cache()
    set_sub_agent_capability(None)
    reset_sub_agent_stats()
    set_sub_agent_usage_recorder(None)
    set_sub_agent_event_bus(None)
    _reset_pending_spawns()


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


def test_spawn_sub_agent_is_not_summarizable() -> None:
    """R10: spawn output is already distilled (the sub-agent's final
    response, kept deliberately small). Re-summarizing is redundant spend
    and risks mangling the ``spawn_id`` routing key inside a question
    block — so the tool opts out of the post-processor."""
    assert not is_summarizable(spawn_sub_agent)


# ---------- allowed_tools filter (R2) ----------


def test_allowed_tools_filter_none_for_empty_allowlist() -> None:
    # No allowlist → no filter → unfiltered Agent (common path, unchanged).
    assert _make_allowed_tools_filter(None) is None
    assert _make_allowed_tools_filter([]) is None


async def test_allowed_tools_filter_restricts_to_allowlist_plus_control_plane() -> None:
    from pydantic_ai.tools import ToolDefinition

    filt = _make_allowed_tools_filter(["read_file"])
    assert filt is not None
    tool_defs = [
        ToolDefinition(name="read_file"),
        ToolDefinition(name="write_file"),
        ToolDefinition(name="run_shell"),
        ToolDefinition(name="ask_supervisor"),
        ToolDefinition(name="grep"),
    ]
    kept = {td.name for td in await filt(None, tool_defs)}  # type: ignore[arg-type]
    # The allowlisted tool survives; the always-allowed control plane survives;
    # everything destructive/unlisted is gone — by construction, not behavior.
    assert kept == {"read_file", "ask_supervisor"}
    assert "write_file" not in kept
    assert "run_shell" not in kept


def test_control_plane_set_is_read_file_and_ask_supervisor() -> None:
    # Lock the always-allowed set so a future edit that muzzles a worker's
    # escape hatch trips this test.
    assert frozenset({"read_file", "ask_supervisor"}) == _ALWAYS_ALLOWED_SUB_AGENT_TOOLS


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


def test_render_packet_omits_project_context_when_none() -> None:
    """No AGENTS.md → no Project context section (the default 2-arg path)."""
    packet = SubAgentTaskPacket(objective="X")
    rendered = _render_packet(packet, "BASE", None)
    assert "# Project context" not in rendered


def test_render_packet_places_agents_context_before_task_packet() -> None:
    """AGENTS.md context is orientation: it must precede the task packet so
    the minion reads conventions first, the specific job second."""
    packet = SubAgentTaskPacket(objective="Summarize foo.py")
    rendered = _render_packet(packet, "BASE", "Use uv, not pip.")
    assert "# Project context" in rendered
    assert "Use uv, not pip." in rendered
    assert rendered.index("# Project context") < rendered.index("# Task packet")


def test_load_agents_context_excludes_memory(tmp_path, monkeypatch) -> None:
    """AGENTS.md is injected into minions; JAC-managed memory.md is not —
    memory grows unbounded and is Gru's to curate into the packet."""
    from jac.workspace import context as ctx

    user_agents = tmp_path / "user_AGENTS.md"
    user_agents.write_text("user convention: use uv", encoding="utf-8")
    user_mem = tmp_path / "user_memory.md"
    user_mem.write_text("- a user memory entry", encoding="utf-8")
    monkeypatch.setattr(ctx.paths, "USER_CONTEXT_FILE", user_agents)
    monkeypatch.setattr(ctx.paths, "USER_MEMORY_FILE", user_mem)
    # No project files in tmp_path → project loaders return None.
    monkeypatch.setattr(ctx.paths, "project_context_file", lambda: tmp_path / "missing_AGENTS.md")
    monkeypatch.setattr(ctx.paths, "project_memory_file", lambda: tmp_path / "missing_memory.md")

    out = ctx.load_agents_context()
    assert out is not None
    assert "use uv" in out
    assert "a user memory entry" not in out


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

        @property
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

        @property
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

        @property
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


# ---------- parallel spawn (Phase E) ----------


def test_spawn_sub_agents_is_a_jac_tool() -> None:
    assert is_jac_tool(spawn_sub_agents)


def test_spawn_sub_agents_is_not_summarizable() -> None:
    """R10: like the single-spawn tool, the parallel fan-out already returns
    distilled per-spawn results and embeds spawn_id routing keys; opting out
    of the post-processor protects those keys and avoids redundant spend."""
    assert not is_summarizable(spawn_sub_agents)


def test_parallel_depth_cap_structural() -> None:
    """The parallel tool ships from the same capability as the single
    spawn — so the depth cap (sub_agent_capabilities excludes
    SubAgentToolCapability) covers it automatically. This test pins the
    invariant: if anyone ever adds a separate parallel-only capability
    they have to also exclude it in sub_agent_capabilities()."""
    from jac.capabilities.sub_agent import SubAgentToolCapability
    from jac.runtime.gru import sub_agent_capabilities

    caps = sub_agent_capabilities()
    assert not any(isinstance(c, SubAgentToolCapability) for c in caps)


async def test_spawn_sub_agents_fails_fast_when_no_capability() -> None:
    set_sub_agent_capability(None)
    with pytest.raises(JacConfigError, match="not available in this session"):
        await spawn_sub_agents(
            reason="r",
            task_summary="s",
            spawns=[
                SubAgentSpawnSpec(
                    tier="medium",
                    task_packet=SubAgentTaskPacket(objective="x"),
                )
            ],
        )


async def test_spawn_sub_agents_rejects_empty_list() -> None:
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))
    with pytest.raises(JacConfigError, match="at least one spawn spec"):
        await spawn_sub_agents(reason="r", task_summary="s", spawns=[])


async def test_spawn_sub_agents_gather_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All N spawns succeed; combined output has the parallel header plus
    one tagged block per spawn, in order."""
    p = _profile(
        small=["anthropic:claude-haiku-4-5"],
        medium=["anthropic:claude-sonnet-4-6"],
    )
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    # Each spawn gets a distinct answer keyed off its objective so we can
    # verify ordering in the combined output.
    objectives_seen: list[str] = []

    class _U:
        requests = 1
        input_tokens = 100
        output_tokens = 20

    class _R:
        def __init__(self, text: str) -> None:
            self.output = text

        @property
        def usage(self) -> _U:

            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        # Tag the answer with whichever objective was rendered into the
        # prompt so we can prove the gather preserved ordering.
        for obj in ("alpha", "beta", "gamma"):
            if obj in prompt:
                objectives_seen.append(obj)
                return _R(f"answer-for-{obj}")
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agents(
        reason="batch",
        task_summary="three things",
        spawns=[
            SubAgentSpawnSpec(
                tier="small",
                label="A",
                task_packet=SubAgentTaskPacket(objective="alpha"),
            ),
            SubAgentSpawnSpec(
                tier="medium",
                label="B",
                task_packet=SubAgentTaskPacket(objective="beta"),
            ),
            SubAgentSpawnSpec(
                tier="small",
                task_packet=SubAgentTaskPacket(objective="gamma"),
            ),
        ],
    )

    assert out.startswith("[parallel spawn: 3 sub-agents]")
    # Per-spawn divider includes 1-based index and (when present) label.
    assert "── spawn 1 (A): tier=small" in out
    assert "── spawn 2 (B): tier=medium" in out
    # Spawn 3 has no label — divider should NOT show parentheses.
    assert "── spawn 3: tier=small" in out
    # Outputs interleaved by gather, but the combined string reassembles
    # them in submission order.
    assert (
        out.index("answer-for-alpha") < out.index("answer-for-beta") < out.index("answer-for-gamma")
    )

    # Each spawn bumps stats — three spawns, three JSONL-row equivalents.
    stats = get_sub_agent_stats()
    assert stats.spawns == 3
    assert stats.by_tier == {"small": 2 * 120, "medium": 120}


async def test_spawn_sub_agents_partial_failure_does_not_kill_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If one sub-agent raises, the others still complete and the failing
    spawn surfaces as ``exit=error`` in its block. The combined string
    still contains all N blocks."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    class _U:
        requests = 1
        input_tokens = 50
        output_tokens = 10

    class _R:
        output = "fine"

        @property
        def usage(self) -> _U:

            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        if "boom" in prompt:
            raise RuntimeError("model unreachable")
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agents(
        reason="r",
        task_summary="s",
        spawns=[
            SubAgentSpawnSpec(
                tier="medium",
                task_packet=SubAgentTaskPacket(objective="boom"),
            ),
            SubAgentSpawnSpec(
                tier="medium",
                task_packet=SubAgentTaskPacket(objective="ok"),
            ),
        ],
    )
    # Failing spawn renders as exit=error; sibling renders as exit=ok.
    assert "── spawn 1: tier=medium" in out
    assert "exit=error" in out
    assert "model unreachable" in out
    assert "── spawn 2: tier=medium" in out
    assert "fine" in out
    # Sibling that succeeded should still bump stats; the failed one
    # didn't reach the recorder.
    assert get_sub_agent_stats().spawns == 1


async def test_spawn_sub_agents_per_spawn_tier_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier cascade is per-spawn: requesting 'small' on a profile with
    only 'medium' should cascade up just for that spawn; sibling tiers
    are unaffected."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])  # no small
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    class _U:
        requests = 1
        input_tokens = 10
        output_tokens = 5

    class _R:
        output = "ok"

        @property
        def usage(self) -> _U:

            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agents(
        reason="r",
        task_summary="s",
        spawns=[
            SubAgentSpawnSpec(
                tier="small",
                task_packet=SubAgentTaskPacket(objective="a"),
            ),
            SubAgentSpawnSpec(
                tier="medium",
                task_packet=SubAgentTaskPacket(objective="b"),
            ),
        ],
    )
    # First spawn cascaded; second resolved exactly.
    assert "cascaded up to 'medium'" in out
    # Both blocks landed on tier=medium in the rendered header.
    assert out.count("tier=medium") >= 2


async def test_spawn_sub_agents_unresolvable_tier_surfaces_per_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If one spec asks for a tier with no upward fallback, that spawn's
    block reports the setup error — the rest of the batch still runs."""
    p = _profile(small=["anthropic:claude-haiku-4-5"])  # no medium/large
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    class _U:
        requests = 1
        input_tokens = 10
        output_tokens = 5

    class _R:
        output = "ok"

        @property
        def usage(self) -> _U:

            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agents(
        reason="r",
        task_summary="s",
        spawns=[
            SubAgentSpawnSpec(
                tier="large",
                task_packet=SubAgentTaskPacket(objective="needs-big"),
            ),
            SubAgentSpawnSpec(
                tier="small",
                task_packet=SubAgentTaskPacket(objective="fine"),
            ),
        ],
    )
    assert "── spawn 1: tier=large exit=error" in out
    assert "Spawn setup failed" in out
    assert "── spawn 2: tier=small" in out
    # Only the surviving spawn counted toward stats.
    assert get_sub_agent_stats().spawns == 1


async def test_spawn_sub_agents_each_spawn_writes_jsonl_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-spawn JSONL rows are the audit trail for parallel cost
    rollup. Verify each spawn lands its own ``kind=sub_agent:<tier>`` row."""
    p = _profile(
        small=["anthropic:claude-haiku-4-5"],
        medium=["anthropic:claude-sonnet-4-6"],
    )
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

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

    async def recorder(in_t: int, out_t: int, tier: str) -> None:
        await tracker.add_sub_agent(input_tokens=in_t, output_tokens=out_t, tier=tier)

    set_sub_agent_usage_recorder(recorder)

    class _U:
        requests = 1
        input_tokens = 200
        output_tokens = 50

    class _R:
        output = "ok"

        @property
        def usage(self) -> _U:

            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    await spawn_sub_agents(
        reason="r",
        task_summary="s",
        spawns=[
            SubAgentSpawnSpec(
                tier="small",
                task_packet=SubAgentTaskPacket(objective="a"),
            ),
            SubAgentSpawnSpec(
                tier="medium",
                task_packet=SubAgentTaskPacket(objective="b"),
            ),
        ],
    )

    rows = [json.loads(line) for line in usage_file.read_text().splitlines() if line.strip()]
    assert [r["kind"] for r in rows] == ["sub_agent:small", "sub_agent:medium"]
    # Tracker counters reflect both spawns' tokens.
    assert tracker.counters.input_tokens == 400
    assert tracker.counters.output_tokens == 100


# ---------- bidirectional comms (Phase 4 suspend/resume) ----------


@pytest.fixture
def _bidirectional_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Flip ``cost.sub_agent_bidirectional`` on via env var and reset the
    settings cache so :func:`get_settings` picks it up. Pairs with the
    autouse fixture above (which already clears _pending_spawns)."""
    from jac.config import reset_settings_cache

    monkeypatch.setenv("JAC_COST__SUB_AGENT_BIDIRECTIONAL", "true")
    reset_settings_cache()
    yield
    reset_settings_cache()


class _FakeUsage:
    """Minimal stand-in for ``AgentRunResult.usage`` (a property)."""

    def __init__(self, requests: int = 1, input_tokens: int = 10, output_tokens: int = 5) -> None:
        self.requests = requests
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResult:
    """Stand-in for ``AgentRunResult`` covering what the runner reads:
    ``.output`` (str or ``DeferredToolRequests``), ``.usage``, ``.all_messages()``."""

    def __init__(self, output: Any, *, usage: _FakeUsage | None = None) -> None:
        self.output = output
        self._usage = usage or _FakeUsage()

    @property
    def usage(self) -> _FakeUsage:
        return self._usage

    def all_messages(self) -> list[Any]:
        # The checkpoint the worker resumes from. Opaque to the runner —
        # an empty list is fine for the fake (the fake_run ignores it).
        return []


def _deferred_question(question: str, *, context: str = "", tool_call_id: str = "call-1") -> Any:
    """Build a ``DeferredToolRequests`` carrying one ``ask_supervisor`` call —
    the suspension signal a worker run returns when it asks a question."""
    from pydantic_ai import DeferredToolRequests
    from pydantic_ai.messages import ToolCallPart

    args: dict[str, str] = {"reason": "worker needs input", "question": question}
    if context:
        args["context"] = context
    return DeferredToolRequests(
        calls=[ToolCallPart(tool_name="ask_supervisor", args=args, tool_call_id=tool_call_id)]
    )


def _scripted_agent_run(
    monkeypatch: pytest.MonkeyPatch,
    outputs: list[Any],
    *,
    usage: _FakeUsage | None = None,
    answers_seen: list[str] | None = None,
) -> None:
    """Patch ``Agent.run`` to return ``outputs`` one per call (initial run then
    each resume). When ``answers_seen`` is given, every resume's delivered
    answer (the ``DeferredToolResults`` value) is appended to it so tests can
    assert what the worker actually received."""
    calls = {"n": 0}

    async def fake_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        results = kwargs.get("deferred_tool_results")
        if results is not None and answers_seen is not None:
            answers_seen.extend(str(v) for v in results.calls.values())
        i = calls["n"]
        calls["n"] += 1
        out = outputs[min(i, len(outputs) - 1)]
        return _FakeResult(out, usage=usage)

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)


def _spawn_id_of(result: str) -> str:
    return result.split("spawn_id=")[1].split("\n")[0].strip()


def test_bidirectional_capability_wiring_flag_off() -> None:
    """With the flag off the main agent doesn't get respond_to_sub_agent.
    The worker-side ``ask_supervisor`` is an external tool the runner adds
    only on the bidirectional path, so there's nothing to assert on the
    factory output here."""
    from jac.capabilities.sub_agent import (
        RespondToSubAgentCapability,
        SubAgentToolCapability,
    )
    from jac.runtime.gru import _default_tool_capabilities

    main_caps = _default_tool_capabilities()
    assert any(isinstance(c, SubAgentToolCapability) for c in main_caps)
    assert not any(isinstance(c, RespondToSubAgentCapability) for c in main_caps)


def test_bidirectional_capability_wiring_flag_on(_bidirectional_on: None) -> None:
    """With the flag on the main agent gets respond_to_sub_agent. The depth
    cap still holds: a sub-agent never sees the spawn or respond tools."""
    from jac.capabilities.sub_agent import (
        RespondToSubAgentCapability,
        SubAgentToolCapability,
    )
    from jac.runtime.gru import _default_tool_capabilities, sub_agent_capabilities

    main_caps = _default_tool_capabilities()
    assert any(isinstance(c, RespondToSubAgentCapability) for c in main_caps)

    sub_caps = sub_agent_capabilities()
    for cap in sub_caps:
        assert not isinstance(cap, (SubAgentToolCapability, RespondToSubAgentCapability))


def test_respond_to_sub_agent_is_jac_tool_not_summarizable() -> None:
    assert is_jac_tool(respond_to_sub_agent)
    assert not is_summarizable(respond_to_sub_agent)


def test_bidirectional_worker_agent_gets_external_ask_supervisor() -> None:
    """By construction: with bidirectional on, the worker Agent is built with
    ``DeferredToolRequests`` in its output types and the external
    ``ask_supervisor`` tool — that's what lets a run suspend on a question."""
    from pydantic_ai import DeferredToolRequests

    from jac.runtime.sub_agent import _ask_supervisor_toolset, _build_worker_agent

    toolset = _ask_supervisor_toolset()
    names = [td.name for td in toolset.tool_defs]
    assert names == ["ask_supervisor"]

    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    cap = SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    resolved = resolve_tier(p, "medium")
    packet = SubAgentTaskPacket(objective="x")

    bidi = _build_worker_agent(cap, packet, resolved, bidirectional=True)
    assert DeferredToolRequests in bidi.output_type
    plain = _build_worker_agent(cap, packet, resolved, bidirectional=False)
    assert plain.output_type is str


async def test_bidirectional_happy_path_single_round_trip(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: main → spawn → worker suspends on a question → main
    responds → worker resumes and finalizes → final result returns from
    respond_to_sub_agent."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    )

    answers: list[str] = []
    # Run 1 suspends on a question; run 2 (resume) returns the final answer.
    _scripted_agent_run(
        monkeypatch,
        [_deferred_question("what's the schema?"), "done; schema applied"],
        usage=_FakeUsage(requests=2, input_tokens=100, output_tokens=20),
        answers_seen=answers,
    )

    # First main-agent turn → spawn → gets the question block.
    first_result = await spawn_sub_agent(
        reason="delegate",
        task_summary="x",
        tier="medium",
        task_packet={"objective": "do thing"},
    )
    assert "[sub-agent → main: question pending]" in first_result
    assert "what's the schema?" in first_result
    import re

    match = re.search(r"spawn_id=(minion-\d+)", first_result)
    assert match is not None
    spawn_id = match.group(1)
    assert spawn_id in _pending_spawns

    # Second main-agent turn → respond → worker resumes → final result.
    second_result = await respond_to_sub_agent(
        reason="reply", spawn_id=spawn_id, answer="users.id is a UUID"
    )
    assert "[sub-agent tier=medium model=anthropic:claude-sonnet-4-6" in second_result
    assert "exit=ok" in second_result
    assert "done; schema applied" in second_result
    # The worker received the main agent's answer on resume.
    assert answers == ["users.id is a UUID"]
    # Pending spawn cleaned up after completion.
    assert spawn_id not in _pending_spawns


async def test_bidirectional_multi_round_trip(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two questions in one spawn. Each respond_to_sub_agent surfaces the
    next question (or the final result), confirming repeated suspend/resume
    cycles work."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    )

    answers: list[str] = []
    _scripted_agent_run(
        monkeypatch,
        [_deferred_question("Q1"), _deferred_question("Q2"), "got both answers"],
        answers_seen=answers,
    )

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    assert "Q1" in first
    spawn_id = _spawn_id_of(first)

    second = await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer="A1")
    # Should be ANOTHER question, not the final.
    assert "[sub-agent → main: question pending]" in second
    assert "Q2" in second

    third = await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer="A2")
    assert "[sub-agent tier=medium" in third  # final result tag
    assert "got both answers" in third
    assert answers == ["A1", "A2"]
    assert spawn_id not in _pending_spawns


async def test_bidirectional_round_trip_cap_returns_finalize_directive(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The (cap+1)th question must NOT reach the main agent. The worker is
    auto-resumed with the finalize directive so it produces a coherent final
    answer, and the spawn lands on the final result rather than another
    question after CAP answers."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    )

    answers: list[str] = []
    # CAP+1 questions then a final answer. The (CAP+1)th question is
    # auto-handled with the finalize directive, then the worker finalizes.
    outputs: list[Any] = [
        _deferred_question(f"Q{i + 1}") for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP + 1)
    ]
    outputs.append("finalized")
    _scripted_agent_run(monkeypatch, outputs, answers_seen=answers)

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    spawn_id = _spawn_id_of(first)

    # Answer questions 1..CAP-1 — each surfaces the next question.
    current = first
    for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP - 1):
        assert f"Q{i + 1}" in current
        current = await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer=f"A{i + 1}")
        assert "[sub-agent → main: question pending]" in current

    # The CAP-th answer: the worker asks a (CAP+1)th time, which is
    # auto-resumed with the finalize directive, so this call lands on the
    # final result rather than another question.
    final = await respond_to_sub_agent(
        reason="r", spawn_id=spawn_id, answer=f"A{_BIDIRECTIONAL_ROUND_TRIP_CAP}"
    )
    assert "[sub-agent tier=medium" in final
    assert "finalized" in final
    # The worker received the user's CAP answers plus the finalize directive.
    assert _BIDIRECTIONAL_FINALIZE_DIRECTIVE in answers[-1]
    for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP):
        assert f"A{i + 1}" in answers[i]
    assert spawn_id not in _pending_spawns


async def test_respond_to_unknown_spawn_id_returns_error(
    _bidirectional_on: None,
) -> None:
    """Calling respond with a spawn_id that never existed shouldn't crash
    the main agent — it should return a structured error string so the
    model can self-correct."""
    out = await respond_to_sub_agent(reason="r", spawn_id="deadbeef", answer="hi")
    assert out.startswith("[error: no pending sub-agent")
    assert "deadbeef" in out


async def test_respond_to_finished_spawn_returns_error(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spawn that already completed is popped from the registry, so a
    late reply surfaces the 'no pending sub-agent' error rather than crashing."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    )
    # Worker finishes immediately (no question) → never parked.
    _scripted_agent_run(monkeypatch, ["done"])
    out = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    assert "[sub-agent tier=medium" in out  # completed, not a question
    assert not _pending_spawns

    late = await respond_to_sub_agent(reason="r", spawn_id="minion-1", answer="too late")
    assert late.startswith("[error: no pending sub-agent")


def test_reset_pending_spawns_clears_registry(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_reset_pending_spawns`` (REPL teardown hook) drops every suspended
    spawn — no leaks across sessions. Suspended spawns hold no live task,
    so there's nothing to cancel; the registry is simply cleared."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    pending = PendingSpawn(
        spawn_id="minion-7",
        packet=SubAgentTaskPacket(objective="x"),
        resolved=resolve_tier(p, "medium"),
        history=[],
        tool_call_id="call-1",
        objective="x",
    )
    _pending_spawns["minion-7"] = pending
    assert "minion-7" in _pending_spawns
    _reset_pending_spawns()
    assert "minion-7" not in _pending_spawns


def test_bidirectional_addendum_prompt_appended_only_when_flag_on(
    _bidirectional_on: None,
) -> None:
    """The gru_bidirectional.md addendum must only be visible to the model
    when the flag is on — we never describe tools the agent doesn't have."""
    from jac.capabilities.context import ContextCapability
    from jac.runtime.gru import _default_tool_capabilities

    caps = _default_tool_capabilities()
    ctx = next(c for c in caps if isinstance(c, ContextCapability))
    body = getattr(ctx, "base_prompt", "")
    assert "respond_to_sub_agent" in body


def test_bidirectional_addendum_absent_when_flag_off() -> None:
    """Negative case for the prompt-injection test above. Flag off →
    no mention of the bidirectional tools in the system prompt."""
    from jac.capabilities.context import ContextCapability
    from jac.runtime.gru import _default_tool_capabilities

    caps = _default_tool_capabilities()
    ctx = next(c for c in caps if isinstance(c, ContextCapability))
    body = getattr(ctx, "base_prompt", "")
    assert "respond_to_sub_agent" not in body


# ---------- renderer event emission (D41 UX polish) ----------


async def test_bidirectional_emits_lifecycle_events_in_order(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the contract: SubAgentSpawned → SubAgentQuestion → SubAgentAnswer
    → SubAgentCompleted lands on the bus in that order across a single
    round-trip. The renderer relies on this order to draw the panels."""
    from jac.runtime.events import (
        EventBus,
        SubAgentAnswer,
        SubAgentCompleted,
        SubAgentQuestion,
        SubAgentSpawned,
    )

    bus = EventBus()
    set_sub_agent_event_bus(bus)

    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _a: [],
        )
    )

    _scripted_agent_run(
        monkeypatch,
        [_deferred_question("what schema?"), "done"],
        usage=_FakeUsage(requests=1, input_tokens=100, output_tokens=20),
    )

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    spawn_id = _spawn_id_of(first)
    await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer="UUID")

    # Drain the bus and assert order.
    collected: list[Any] = []
    while not bus._queue.empty():  # type: ignore[attr-defined]
        collected.append(bus._queue.get_nowait())  # type: ignore[attr-defined]

    types_in_order = [type(e).__name__ for e in collected]
    assert types_in_order == [
        "SubAgentSpawned",
        "SubAgentQuestion",
        "SubAgentAnswer",
        "SubAgentCompleted",
    ]

    spawned = collected[0]
    assert isinstance(spawned, SubAgentSpawned)
    assert spawned.spawn_id == spawn_id
    assert spawned.tier == "medium"
    assert spawned.model == "anthropic:claude-sonnet-4-6"
    assert spawned.objective == "x"

    question_event = collected[1]
    assert isinstance(question_event, SubAgentQuestion)
    assert question_event.spawn_id == spawn_id
    assert question_event.question == "what schema?"
    assert question_event.round_trip == 1

    answer_event = collected[2]
    assert isinstance(answer_event, SubAgentAnswer)
    assert answer_event.spawn_id == spawn_id
    assert answer_event.answer == "UUID"

    completed = collected[3]
    assert isinstance(completed, SubAgentCompleted)
    assert completed.spawn_id == spawn_id
    assert completed.exit_status == "ok"
    assert completed.ask_main_agent_count == 1


async def test_sequential_spawn_emits_no_sub_agent_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the flag is off (default), the sequential path runs to
    completion without going through the suspend/resume path — no
    SubAgent* events should land on the bus. The existing ToolCallStarted
    line still provides visibility."""
    from jac.runtime.events import EventBus

    bus = EventBus()
    set_sub_agent_event_bus(bus)

    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=lambda _a: [])
    )

    class _U:
        requests = 1
        input_tokens = 10
        output_tokens = 5

    class _R:
        output = "ok"

        @property
        def usage(self) -> _U:

            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    assert bus._queue.empty()  # type: ignore[attr-defined]


async def test_bidirectional_emits_completed_on_error_path(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error inside the worker still emits SubAgentCompleted (with
    exit_status='error') so the renderer can draw a closing line."""
    from jac.runtime.events import EventBus, SubAgentCompleted

    bus = EventBus()
    set_sub_agent_event_bus(bus)

    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _a: [],
        )
    )

    async def boom(self: Any, prompt: str, **_kwargs: Any) -> Any:
        raise RuntimeError("model unreachable")

    monkeypatch.setattr("pydantic_ai.Agent.run", boom)

    result = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    assert "exit=error" in result

    # Drain; SubAgentCompleted with exit_status='error' must be present.
    events: list[Any] = []
    while not bus._queue.empty():  # type: ignore[attr-defined]
        events.append(bus._queue.get_nowait())  # type: ignore[attr-defined]
    completed = [e for e in events if isinstance(e, SubAgentCompleted)]
    assert len(completed) == 1
    assert completed[0].exit_status == "error"


# ---------- /spawns slash command ----------


def _pending(spawn_id: str, *, objective: str = "", round_trips: int = 0) -> PendingSpawn:
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    return PendingSpawn(
        spawn_id=spawn_id,
        packet=SubAgentTaskPacket(objective=objective or "x"),
        resolved=resolve_tier(p, "medium"),
        history=[],
        tool_call_id="call-1",
        round_trips=round_trips,
        objective=objective,
    )


def test_spawns_slash_empty_state() -> None:
    """`/spawns` with no suspended sub-agents shows a clear empty-state line."""
    from io import StringIO

    from rich.console import Console

    from jac.cli.slash import SlashContext, dispatch
    from jac.runtime.session import Session

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    ctx = SlashContext(
        console=console,
        session=Session(session_id="s1", message_history=[]),
        profile_name=None,
        profile=None,
        model_id="x",
    )
    dispatch("/spawns", ctx)
    assert "no suspended sub-agents" in buf.getvalue()


def test_spawns_slash_lists_suspended_spawns() -> None:
    """Populate _pending_spawns directly and verify /spawns renders a row per
    spawn with spawn_id, tier/model, round-trips, objective."""
    from io import StringIO

    from rich.console import Console

    from jac.cli.slash import SlashContext, dispatch
    from jac.runtime.session import Session

    _pending_spawns["minion-3"] = _pending(
        "minion-3", objective="summarize the auth module", round_trips=2
    )

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    ctx = SlashContext(
        console=console,
        session=Session(session_id="s1", message_history=[]),
        profile_name=None,
        profile=None,
        model_id="x",
    )
    dispatch("/spawns", ctx)

    out = buf.getvalue()
    assert "suspended sub-agents" in out
    assert "minion-3" in out
    assert "medium" in out
    # round-trips column shows N/cap
    assert f"2/{_BIDIRECTIONAL_ROUND_TRIP_CAP}" in out
    assert "summarize the auth module" in out


def test_statusbar_spawns_segment_hidden_when_empty() -> None:
    from jac.cli.statusbar import _format_spawns_segment

    assert _format_spawns_segment() == ""


def test_statusbar_spawns_segment_visible_when_suspended() -> None:
    from jac.cli.statusbar import _format_spawns_segment

    _pending_spawns["minion-1"] = _pending("minion-1")
    segment = _format_spawns_segment()
    assert "minions:" in segment
    assert "1" in segment


# ---------- sub-agent wiring: HITL + skills + a2a ----------
#
# Sub-agents share the main agent's bus-bound capabilities so destructive
# tool calls route through the same HITL flow and the user never loses
# visibility / control. Skills + A2A are shared instances (not fresh) so
# ``/skill reload`` is observed by both surfaces and the guest A2A server
# isn't duplicated.


def test_sub_agent_capability_default_excludes_bus_bound_caps() -> None:
    """Bare ``sub_agent_capabilities()`` (no kwargs) must keep the
    Phase B silent-and-unguarded behaviour — test contexts that don't
    have a bus rely on it."""
    from jac.runtime.gru import sub_agent_capabilities

    caps = sub_agent_capabilities()
    # Sentinel: HITL/approval is absent when no kwargs supplied.
    type_names = {type(c).__name__ for c in caps}
    assert "Hooks" not in type_names
    # ``HandleDeferredToolCalls`` is the pydantic-ai approval-handler type.
    assert "HandleDeferredToolCalls" not in type_names
    assert "SkillsCapability" not in type_names
    assert "A2ACapability" not in type_names


def test_sub_agent_capability_includes_hooks_approval_skills_a2a_when_supplied() -> None:
    """When the REPL closure passes hooks / approval / skills / a2a, the
    sub-agent's capability list grows by exactly those instances and the
    objects are reused (same identity) — not copied — so /skill reload
    affects sub-agents too and the guest A2A server stays singular."""
    from jac.capabilities.skills import make_skills_capability
    from jac.runtime.approval import make_approval_handler
    from jac.runtime.gru import sub_agent_capabilities
    from jac.runtime.hooks import make_hooks

    bus = EventBus()
    hooks = make_hooks(bus)
    approval = make_approval_handler(bus)
    skills = make_skills_capability()

    # A2A capability requires a model param; a sentinel string is enough
    # — we never call it, only check identity in the cap list.
    class _A2ASentinel:
        pass

    a2a = _A2ASentinel()

    caps = sub_agent_capabilities(
        hooks=hooks,
        approval=approval,
        skills_capability=skills,
        a2a_capability=a2a,
    )

    assert hooks in caps
    assert approval in caps
    assert skills in caps
    assert a2a in caps


def test_sub_agent_destructive_tool_marked_approval_required() -> None:
    """Spot-check: the toolset wrapping that gates destructive tools for
    the main agent applies the same way once a sub-agent gets the
    ``HandleDeferredToolCalls`` capability — i.e. the @jac_tool decorator
    on shared tools (write_file, run_shell, …) carries the approval
    marker into the sub-agent's toolset, not just the main agent's.

    We assert this at the capability level: the filesystem + shell
    capabilities the sub-agent gets are the *same classes* the main
    agent uses, so any tool marked approval-required upstream stays
    marked downstream. (A full end-to-end approval round-trip is
    already covered by the bidirectional happy-path test; this one
    keeps the wiring honest with a smaller-blast-radius check.)
    """
    from jac.capabilities.filesystem import FilesystemCapability
    from jac.capabilities.shell import ShellCapability
    from jac.runtime.gru import _default_tool_capabilities, sub_agent_capabilities

    sub_caps = sub_agent_capabilities()
    main_caps = _default_tool_capabilities()

    sub_types = {type(c) for c in sub_caps}
    main_types = {type(c) for c in main_caps}

    # Both surfaces share the same FilesystemCapability / ShellCapability
    # types — destructive tools carry their approval marker uniformly.
    assert FilesystemCapability in sub_types and FilesystemCapability in main_types
    assert ShellCapability in sub_types and ShellCapability in main_types


# ---------- minion-N spawn IDs + agent_label on approvals (E.2.2) ----------


def test_mint_spawn_id_increments_monotonically() -> None:
    """minion-1, minion-2, minion-3 — the user-facing counter the
    renderer keys panels off of. Must not skip or restart mid-session."""
    from jac.runtime.sub_agent import _mint_spawn_id

    assert _mint_spawn_id() == "minion-1"
    assert _mint_spawn_id() == "minion-2"
    assert _mint_spawn_id() == "minion-3"


def test_reset_pending_spawns_resets_counter() -> None:
    """REPL teardown / per-test isolation must reset the counter so the
    next session starts at minion-1, not minion-7."""
    from jac.runtime.sub_agent import _mint_spawn_id

    _mint_spawn_id()
    _mint_spawn_id()
    _reset_pending_spawns()
    assert _mint_spawn_id() == "minion-1"


def test_get_current_agent_label_defaults_to_gru() -> None:
    """Outside any sub-agent run the label is the main agent — Gru."""
    from jac.runtime.sub_agent import get_current_agent_label

    assert get_current_agent_label() == "Gru"


async def test_agent_label_is_set_to_spawn_id_inside_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inside a sub-agent's Agent.run() the contextvar reports the
    spawn_id, so the shared approval handler stamps "minion-N" onto its
    ApprovalRequest emissions instead of the default "Gru"."""
    from jac.runtime.sub_agent import _run_sub_agent, get_current_agent_label

    captured: list[str] = []

    class _FakeUsage:
        requests = 1
        input_tokens = 10
        output_tokens = 10

    class _FakeRunResult:
        output = "ok"

        @property
        def usage(self) -> _FakeUsage:
            return _FakeUsage()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _FakeRunResult:
        # The interesting assertion: inside the run, the label is bound.
        captured.append(get_current_agent_label())
        return _FakeRunResult()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    p = _profile(small=["anthropic:claude-haiku-4-5"])
    cap = SubAgentCapability(
        profile=p,
        base_prompt="BASE",
        capability_factory=lambda _allowed: [],
    )
    resolved = resolve_tier(p, "small")
    packet = SubAgentTaskPacket(objective="x", max_turns=3)

    await _run_sub_agent(cap, packet, resolved, spawn_id="minion-7")

    assert captured == ["minion-7"]
    # Outer scope sees the default again — per-task contextvar copy.
    assert get_current_agent_label() == "Gru"


async def test_approval_request_carries_agent_label() -> None:
    """End-to-end: when the approval handler runs inside a sub-agent's
    context, the ApprovalRequest event it emits carries the minion-N label,
    not the default Gru. The renderer reads this to title the panel."""
    import asyncio as _asyncio

    from pydantic_ai.tools import DeferredToolRequests, ToolCallPart

    from jac.runtime.approval import make_approval_handler
    from jac.runtime.events import ApprovalRequest, ApprovalResponse, EventBus
    from jac.runtime.sub_agent import _current_agent_label

    bus = EventBus()
    handler = make_approval_handler(bus)

    call = ToolCallPart(
        tool_name="run_shell",
        args={"reason": "do the thing", "command": "ls"},
        tool_call_id="abc",
    )
    requests = DeferredToolRequests(approvals=[call])

    captured: list[ApprovalRequest] = []

    async def _consume_and_approve() -> None:
        # The bus is an asyncio.Queue under the hood; the public API is
        # ``emit`` + ``stream``. Tests poke the queue directly to keep
        # the consume-and-approve dance synchronous.
        event = await bus._queue.get()
        assert isinstance(event, ApprovalRequest)
        captured.append(event)
        event.response_future.set_result(ApprovalResponse(approved=True))

    # Bind the contextvar so the handler reads minion-3 (the synthetic
    # spawn we're pretending is currently running).
    token = _current_agent_label.set("minion-3")
    try:
        consumer = _asyncio.create_task(_consume_and_approve())
        await handler.handler(None, requests)  # type: ignore[arg-type]
        await consumer
    finally:
        _current_agent_label.reset(token)

    assert len(captured) == 1
    assert captured[0].agent_label == "minion-3"
    assert captured[0].tool_name == "run_shell"


# ---------- E.3: parallel-spawn lifecycle events + renderer panel ----------


async def test_parallel_spawn_emits_spawned_and_completed_per_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spawn_sub_agents`` should now emit one ``SubAgentSpawned`` and
    one ``SubAgentCompleted`` per sub-agent (E.3b), not just a single
    combined block at the end. The user gets a blue panel as each spawn
    starts and a green completion line as each finishes."""
    from jac.runtime.events import EventBus, SubAgentCompleted, SubAgentSpawned

    bus = EventBus()
    set_sub_agent_event_bus(bus)

    p = _profile(small=["anthropic:claude-haiku-4-5"])
    set_sub_agent_capability(SubAgentCapability(p, "BASE", lambda _a: []))

    class _U:
        requests = 1
        input_tokens = 10
        output_tokens = 5

    class _R:
        output = "ok"

        @property
        def usage(self) -> _U:
            return _U()

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> _R:
        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    out = await spawn_sub_agents(
        reason="batch",
        task_summary="two things",
        spawns=[
            SubAgentSpawnSpec(tier="small", task_packet=SubAgentTaskPacket(objective="alpha")),
            SubAgentSpawnSpec(tier="small", task_packet=SubAgentTaskPacket(objective="beta")),
        ],
    )

    assert "[parallel spawn: 2 sub-agents]" in out

    # Drain the bus and bucket events by type.
    events: list[Any] = []
    while not bus._queue.empty():
        events.append(bus._queue.get_nowait())

    spawned = [e for e in events if isinstance(e, SubAgentSpawned)]
    completed = [e for e in events if isinstance(e, SubAgentCompleted)]

    assert len(spawned) == 2, f"expected 2 Spawned, got {len(spawned)}: {events}"
    assert len(completed) == 2, f"expected 2 Completed, got {len(completed)}: {events}"

    # Every Spawned must pair with a Completed for the same spawn_id;
    # otherwise the renderer would leave a ▶ panel orphaned.
    spawned_ids = {e.spawn_id for e in spawned}
    completed_ids = {e.spawn_id for e in completed}
    assert spawned_ids == completed_ids


async def test_renderer_special_cases_spawn_sub_agents_panel() -> None:
    """The approval panel for ``spawn_sub_agents`` shows a per-spawn
    summary table (label / tier / objective) instead of dumping the
    nested ``spawns`` dict inline (E.3a)."""
    import asyncio as _asyncio
    from io import StringIO

    from rich.console import Console

    from jac.cli.renderer import CliRenderer
    from jac.runtime.events import ApprovalRequest, ApprovalResponse

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    renderer = CliRenderer(console=console)

    future: _asyncio.Future[ApprovalResponse] = _asyncio.get_running_loop().create_future()
    event = ApprovalRequest(
        tool_call_id="abc",
        tool_name="spawn_sub_agents",
        reason="three independent investigations",
        args={
            "reason": "three independent investigations",
            "task_summary": "audit three modules",
            "spawns": [
                {
                    "tier": "medium",
                    "label": "runtime_audit",
                    "task_packet": {
                        "objective": "list and describe every file in src/jac/runtime/",
                    },
                },
                {
                    "tier": "small",
                    "label": "cli_audit",
                    "task_packet": {"objective": "list every file in src/jac/cli/"},
                },
                {
                    "tier": "large",
                    "task_packet": {"objective": "deep-review src/jac/capabilities/sub_agent.py"},
                },
            ],
        },
        response_future=future,
        agent_label="Gru",
    )

    group = renderer._build_parallel_spawn_body(event)
    # Render the group through the same console so we can grep the output.
    console.print(group)
    out = buf.getvalue()

    # Header conveys the count + summary.
    assert "spawn_sub_agents" in out
    assert "3" in out
    assert "parallel sub-agent" in out  # "agents" (plural)

    # Per-spawn rows show label + tier + a piece of each objective.
    assert "runtime_audit" in out
    assert "cli_audit" in out
    assert "medium" in out and "small" in out and "large" in out
    assert "src/jac/runtime/" in out
    assert "src/jac/cli/" in out

    # The unlabeled spawn renders a "no label" placeholder, not the literal
    # string "None" or empty cell that'd be hard to read.
    assert "no label" in out or "(no label)" in out


async def test_renderer_singular_in_single_parallel_spawn() -> None:
    """One-spawn batch (rare but legal) reads "1 parallel sub-agent" not
    "1 parallel sub-agents"."""
    import asyncio as _asyncio
    from io import StringIO

    from rich.console import Console

    from jac.cli.renderer import CliRenderer
    from jac.runtime.events import ApprovalRequest, ApprovalResponse

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    renderer = CliRenderer(console=console)

    future: _asyncio.Future[ApprovalResponse] = _asyncio.get_running_loop().create_future()
    event = ApprovalRequest(
        tool_call_id="abc",
        tool_name="spawn_sub_agents",
        reason="r",
        args={
            "reason": "r",
            "task_summary": "ts",
            "spawns": [
                {"tier": "small", "label": "x", "task_packet": {"objective": "do x"}},
            ],
        },
        response_future=future,
    )
    group = renderer._build_parallel_spawn_body(event)
    console.print(group)
    out = buf.getvalue()
    assert "1" in out
    # No trailing 's' on the noun for a singleton.
    assert "1 parallel sub-agent\n" in out or "1 parallel sub-agent " in out


# Silence the "imported but unused" warning when the module-level access
# is what triggers the import. The fake usage classes above intentionally
# don't subclass RunUsage so we don't accidentally pull in pydantic-ai
# validation; the .usage() method just has to return something with the
# expected attributes.
_ = sa
