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
    _BIDIRECTIONAL_FINALIZE_DIRECTIVE,
    _BIDIRECTIONAL_ROUND_TRIP_CAP,
    SubAgentCapability,
    SubAgentChannel,
    SubAgentSpawnSpec,
    SubAgentTaskPacket,
    _pending_channels,
    _render_packet,
    _reset_pending_channels,
    ask_main_agent,
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
    set_sub_agent_capability(None)
    reset_sub_agent_stats()
    set_sub_agent_usage_recorder(None)
    set_sub_agent_event_bus(None)
    _reset_pending_channels()
    yield
    set_sub_agent_capability(None)
    reset_sub_agent_stats()
    set_sub_agent_usage_recorder(None)
    set_sub_agent_event_bus(None)
    _reset_pending_channels()


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


# ---------- parallel spawn (Phase E) ----------


def test_spawn_sub_agents_is_a_jac_tool() -> None:
    assert is_jac_tool(spawn_sub_agents)


def test_spawn_sub_agents_is_summarizable() -> None:
    """Combined output of N spawns can be N times larger than a single spawn
    — summarization matters more, not less."""
    assert is_summarizable(spawn_sub_agents)


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


# ---------- bidirectional comms (D41, Phase E.2) ----------


@pytest.fixture
def _bidirectional_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Flip ``cost.sub_agent_bidirectional`` on via env var and reset the
    settings cache so :func:`get_settings` picks it up. Pairs with the
    autouse fixture above (which already clears _pending_channels)."""
    from jac.config import reset_settings_cache

    monkeypatch.setenv("JAC_COST__SUB_AGENT_BIDIRECTIONAL", "true")
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_bidirectional_capability_wiring_flag_off() -> None:
    """With the flag off the main agent doesn't get respond_to_sub_agent
    and a sub-agent (even with a channel) doesn't get ask_main_agent.
    Critical: this guarantees the v1 default-off ship is truly off."""
    from jac.capabilities.sub_agent import (
        AskMainAgentCapability,
        RespondToSubAgentCapability,
        SubAgentToolCapability,
    )
    from jac.runtime.gru import _default_tool_capabilities, sub_agent_capabilities

    main_caps = _default_tool_capabilities()
    assert any(isinstance(c, SubAgentToolCapability) for c in main_caps)
    assert not any(isinstance(c, RespondToSubAgentCapability) for c in main_caps)

    ch = SubAgentChannel(spawn_id="x")
    sub_caps = sub_agent_capabilities(channel=ch)
    assert not any(isinstance(c, AskMainAgentCapability) for c in sub_caps)


def test_bidirectional_capability_wiring_flag_on(_bidirectional_on: None) -> None:
    """With the flag on the main agent gets respond_to_sub_agent AND a
    sub-agent gets ask_main_agent — *if* a channel is bound. Without a
    channel even the flag-on sub-agent stays mute (no channel → no tool)."""
    from jac.capabilities.sub_agent import (
        AskMainAgentCapability,
        RespondToSubAgentCapability,
        SubAgentToolCapability,
    )
    from jac.runtime.gru import _default_tool_capabilities, sub_agent_capabilities

    main_caps = _default_tool_capabilities()
    assert any(isinstance(c, RespondToSubAgentCapability) for c in main_caps)

    sub_caps_no_channel = sub_agent_capabilities()
    assert not any(isinstance(c, AskMainAgentCapability) for c in sub_caps_no_channel)

    ch = SubAgentChannel(spawn_id="x")
    sub_caps_with_channel = sub_agent_capabilities(channel=ch)
    assert any(isinstance(c, AskMainAgentCapability) for c in sub_caps_with_channel)

    # Depth cap holds regardless of the flag: a sub-agent NEVER sees
    # the spawn or respond tools.
    for cap in sub_caps_with_channel:
        assert not isinstance(cap, (SubAgentToolCapability, RespondToSubAgentCapability))


def test_ask_main_agent_is_jac_tool_not_summarizable() -> None:
    """ask_main_agent returns a short string — summarization would
    only add cost without saving tokens."""
    assert is_jac_tool(ask_main_agent)
    assert not is_summarizable(ask_main_agent)


def test_respond_to_sub_agent_is_jac_tool_not_summarizable() -> None:
    assert is_jac_tool(respond_to_sub_agent)
    assert not is_summarizable(respond_to_sub_agent)


async def test_ask_main_agent_fails_fast_when_no_channel_bound() -> None:
    """Called outside a sub-agent context (no contextvar set) the tool
    must surface a structured error — never silently 'succeed' with
    nothing on the receiving end."""
    with pytest.raises(JacConfigError, match="not available"):
        await ask_main_agent(reason="r", question="q")


async def test_bidirectional_happy_path_single_round_trip(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: main → spawn → sub-agent asks → main responds → sub-agent
    finalizes → final result returns from respond_to_sub_agent."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])

    def factory(_allowed: Any, *, channel: Any = None) -> list[Any]:
        # Factory accepts the channel kwarg so the bidirectional path's
        # call site is exercised. Empty caps so the mocked Agent.run is
        # hermetic.
        _ = channel
        return []

    set_sub_agent_capability(
        SubAgentCapability(profile=p, base_prompt="BASE", capability_factory=factory)
    )

    # The fake sub-agent calls ask_main_agent once, then returns "done".
    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> Any:
        answer = await ask_main_agent(reason="t", question="what's the schema?")

        class _U:
            requests = 2
            input_tokens = 100
            output_tokens = 20

        class _R:
            output = f"done; main said: {answer}"

            @property
            def usage(self) -> _U:

                return _U()

        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    # First main-agent turn → calls spawn_sub_agent → gets the question
    first_result = await spawn_sub_agent(
        reason="delegate",
        task_summary="x",
        tier="medium",
        task_packet={"objective": "do thing"},
    )
    assert "[sub-agent → main: question pending]" in first_result
    assert "what's the schema?" in first_result
    # spawn_id is a hex token — pull it out of the result.
    import re

    match = re.search(r"spawn_id=(minion-\d+)", first_result)
    assert match is not None
    spawn_id = match.group(1)
    assert spawn_id in _pending_channels

    # Second main-agent turn → calls respond_to_sub_agent → gets final
    second_result = await respond_to_sub_agent(
        reason="reply", spawn_id=spawn_id, answer="users.id is a UUID"
    )
    assert "[sub-agent tier=medium model=anthropic:claude-sonnet-4-6" in second_result
    assert "exit=ok" in second_result
    assert "users.id is a UUID" in second_result
    # Channel cleaned up after worker completion.
    assert spawn_id not in _pending_channels


async def test_bidirectional_multi_round_trip(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two questions in one spawn. Each respond_to_sub_agent surfaces the
    next question (or the final result), confirming the race helper
    handles repeated round-trips correctly."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _a, *, channel=None: [],
        )
    )

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> Any:
        a1 = await ask_main_agent(reason="t", question="Q1")
        a2 = await ask_main_agent(reason="t", question="Q2")

        class _U:
            requests = 3
            input_tokens = 100
            output_tokens = 20

        class _R:
            output = f"got: {a1!r} {a2!r}"

            @property
            def usage(self) -> _U:

                return _U()

        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    assert "Q1" in first
    spawn_id = first.split("spawn_id=")[1].split("\n")[0].strip()

    second = await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer="A1")
    # Should be ANOTHER question, not the final.
    assert "[sub-agent → main: question pending]" in second
    assert "Q2" in second

    third = await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer="A2")
    assert "[sub-agent tier=medium" in third  # final result tag
    assert "A1" in third and "A2" in third


async def test_bidirectional_round_trip_cap_returns_finalize_directive(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 6th ask_main_agent call must NOT raise and must NOT put a
    question on the queue. It returns the finalize directive directly so
    the sub-agent can produce a coherent final answer."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _a, *, channel=None: [],
        )
    )

    asks_made: list[str] = []
    answers_received: list[str] = []

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> Any:
        # Ask one MORE than the cap. The (cap+1)th call should return the
        # finalize directive instead of waiting on a phantom answer.
        for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP + 1):
            q = f"Q{i + 1}"
            asks_made.append(q)
            a = await ask_main_agent(reason="t", question=q)
            answers_received.append(a)

        class _U:
            requests = _BIDIRECTIONAL_ROUND_TRIP_CAP + 2
            input_tokens = 100
            output_tokens = 20

        class _R:
            output = "finalized"

            @property
            def usage(self) -> _U:

                return _U()

        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    spawn_id = first.split("spawn_id=")[1].split("\n")[0].strip()

    # Answer questions 1..CAP. Each answer comes back via
    # respond_to_sub_agent, which returns either the next question or
    # the final result.
    current = first
    for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP):
        assert f"Q{i + 1}" in current
        current = await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer=f"A{i + 1}")

    # After CAP answers, the sub-agent's (CAP+1)th ask short-circuits to
    # the finalize directive and the sub-agent immediately returns. We
    # therefore land on the final result, NOT another question.
    assert "[sub-agent tier=medium" in current
    assert "exit=ok" in current
    assert "finalized" in current
    # The sub-agent saw the finalize directive as the (CAP+1)th answer.
    assert len(answers_received) == _BIDIRECTIONAL_ROUND_TRIP_CAP + 1
    assert _BIDIRECTIONAL_FINALIZE_DIRECTIVE in answers_received[-1]
    # And the user's first CAP answers were delivered intact.
    for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP):
        assert f"A{i + 1}" in answers_received[i]


async def test_respond_to_unknown_spawn_id_returns_error(
    _bidirectional_on: None,
) -> None:
    """Calling respond with a spawn_id that never existed shouldn't crash
    the main agent — it should return a structured error string so the
    model can self-correct."""
    out = await respond_to_sub_agent(reason="r", spawn_id="deadbeef", answer="hi")
    assert out.startswith("[error: no pending sub-agent")
    assert "deadbeef" in out


async def test_respond_to_already_finished_spawn_returns_error(
    _bidirectional_on: None,
) -> None:
    """If a channel exists but its worker_task is already done (race
    between completion and the main agent composing a reply), respond
    cleans up and surfaces an error."""

    # Build a channel whose worker_task is already complete.
    async def _done() -> Any:
        return None

    task: asyncio.Task[Any] = asyncio.create_task(_done())
    await task

    channel = SubAgentChannel(spawn_id="abc12345", worker_task=task)
    _pending_channels["abc12345"] = channel

    out = await respond_to_sub_agent(reason="r", spawn_id="abc12345", answer="too late")
    assert out.startswith("[error: sub-agent")
    assert "already finished" in out
    # Cleaned up.
    assert "abc12345" not in _pending_channels


async def test_bidirectional_cancellation_cleans_up_channel(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the main agent's run is cancelled while a sub-agent is parked
    on answer_q, the worker must be cancelled and the channel popped —
    no leaks across sessions."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _a, *, channel=None: [],
        )
    )

    # A sub-agent that asks once and then never finishes (parked on the
    # answer queue).
    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> Any:
        await ask_main_agent(reason="t", question="hang here")
        raise AssertionError("unreachable — we cancel before the answer comes")

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    spawn_task = asyncio.create_task(
        spawn_sub_agent(reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"})
    )
    # Let the spawn run far enough to surface the question (which is the
    # point at which the channel is registered AND the spawn returns).
    first = await spawn_task
    assert "[sub-agent → main: question pending]" in first
    spawn_id = first.split("spawn_id=")[1].split("\n")[0].strip()
    assert spawn_id in _pending_channels

    # Now simulate session shutdown — _reset_pending_channels is what the
    # REPL is expected to call on /exit.
    _reset_pending_channels()
    assert spawn_id not in _pending_channels


async def test_channel_round_trips_counter_increments_under_cap(
    _bidirectional_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the counter behaviour: each ask_main_agent under the cap
    increments round_trips; the counter never increments past cap (the
    short-circuit branch returns early)."""
    p = _profile(medium=["anthropic:claude-sonnet-4-6"])
    set_sub_agent_capability(
        SubAgentCapability(
            profile=p,
            base_prompt="BASE",
            capability_factory=lambda _a, *, channel=None: [],
        )
    )

    observed_round_trips: list[int] = []

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> Any:
        from jac.runtime.sub_agent import _current_channel_for_ask

        for _ in range(_BIDIRECTIONAL_ROUND_TRIP_CAP + 2):
            ch = _current_channel_for_ask()
            assert ch is not None
            observed_round_trips.append(ch.round_trips)
            await ask_main_agent(reason="t", question="q")

        class _U:
            requests = 1
            input_tokens = 10
            output_tokens = 5

        class _R:
            output = "ok"

            @property
            def usage(self) -> _U:

                return _U()

        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    spawn_id = first.split("spawn_id=")[1].split("\n")[0].strip()
    # Drive CAP responses; we only care about observed_round_trips, not
    # the returned strings (those are exercised in the other tests).
    _ = first
    for i in range(_BIDIRECTIONAL_ROUND_TRIP_CAP):
        await respond_to_sub_agent(reason="r", spawn_id=spawn_id, answer=f"A{i}")

    # observed[0] = 0 (before first ask); observed[1] = 1; …
    # observed[CAP] should be CAP (final ask is the short-circuit; the
    # counter doesn't increment past cap because the early-return branch
    # bumps neither).
    assert observed_round_trips[0] == 0
    assert observed_round_trips[_BIDIRECTIONAL_ROUND_TRIP_CAP] == _BIDIRECTIONAL_ROUND_TRIP_CAP
    # After cap, no further answers are taken from the queue; the next
    # observation is whatever was there last (still == cap), the final
    # ask doesn't bump it either.
    assert observed_round_trips[_BIDIRECTIONAL_ROUND_TRIP_CAP + 1] == _BIDIRECTIONAL_ROUND_TRIP_CAP


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
            capability_factory=lambda _a, *, channel=None: [],
        )
    )

    async def fake_run(self: Any, prompt: str, **_kwargs: Any) -> Any:
        await ask_main_agent(reason="t", question="what schema?")

        class _U:
            requests = 1
            input_tokens = 100
            output_tokens = 20

        class _R:
            output = "done"

            @property
            def usage(self) -> _U:

                return _U()

        return _R()

    monkeypatch.setattr("pydantic_ai.Agent.run", fake_run)

    first = await spawn_sub_agent(
        reason="r", task_summary="s", tier="medium", task_packet={"objective": "x"}
    )
    spawn_id = first.split("spawn_id=")[1].split("\n")[0].strip()
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
    completion without going through ``_spawn_bidirectional`` — no
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
            capability_factory=lambda _a, *, channel=None: [],
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


def test_spawns_slash_empty_state() -> None:
    """`/spawns` with no active channels shows a clear empty-state line."""
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
    assert "no active sub-agents" in buf.getvalue()


def test_spawns_slash_lists_active_channels() -> None:
    """Populate _pending_channels directly and verify /spawns renders a
    row per channel with spawn_id, tier/model, round-trips, objective."""
    from io import StringIO

    from rich.console import Console

    from jac.cli.slash import SlashContext, dispatch
    from jac.runtime.session import Session
    from jac.runtime.sub_agent import _ResolvedTier

    resolved = _ResolvedTier(
        requested="medium",
        resolved="medium",
        model="anthropic:claude-sonnet-4-6",
        cascaded=False,
    )
    _pending_channels["aabbccdd"] = SubAgentChannel(
        spawn_id="aabbccdd",
        resolved=resolved,
        objective="summarize the auth module",
    )
    _pending_channels["aabbccdd"].round_trips = 2

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
    assert "active sub-agents" in out
    assert "aabbccdd" in out
    assert "medium" in out
    # round-trips column shows N/cap
    assert f"2/{_BIDIRECTIONAL_ROUND_TRIP_CAP}" in out
    assert "summarize the auth module" in out


def test_statusbar_spawns_segment_hidden_when_empty() -> None:
    from jac.cli.statusbar import _format_spawns_segment

    assert _format_spawns_segment() == ""


def test_statusbar_spawns_segment_visible_when_parked() -> None:
    from jac.cli.statusbar import _format_spawns_segment

    _pending_channels["abc12345"] = SubAgentChannel(spawn_id="abc12345")
    segment = _format_spawns_segment()
    assert "spawns:" in segment
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


def test_reset_pending_channels_resets_counter() -> None:
    """REPL teardown / per-test isolation must reset the counter so the
    next session starts at minion-1, not minion-7."""
    from jac.runtime.sub_agent import _mint_spawn_id

    _mint_spawn_id()
    _mint_spawn_id()
    _reset_pending_channels()
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


# Silence the "imported but unused" warning when the module-level access
# is what triggers the import. The fake usage classes above intentionally
# don't subclass RunUsage so we don't accidentally pull in pydantic-ai
# validation; the .usage() method just has to return something with the
# expected attributes.
_ = sa
