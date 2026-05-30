"""Interactive REPL loop for JAC.

Phase 1: the CLI is a pure renderer. It installs a ``Hooks`` capability on
Gru that pushes lifecycle events to an :class:`EventBus`, and a
:class:`CliRenderer` consumes the bus and draws to the terminal.

The agent run and the renderer run **concurrently** within each turn. The
renderer returns when it sees a terminal event (:class:`RunCompleted` /
:class:`RunFailed`); the REPL then awaits the agent task to harvest the
message history. Errors raised inside ``agent.run`` are emitted as
:class:`RunFailed`, rendered, and absorbed so the REPL continues to the
next prompt.

Session state (message history) persists to disk after every completed
turn via :class:`jac.runtime.session.Session`. Resume support is exposed
by the CLI's ``--resume`` / ``--session`` flags.
"""

from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from pydantic_ai import Agent, AgentRunResult, capture_run_messages
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from rich.console import Console

from jac import __version__
from jac.capabilities.a2a import make_a2a_capability
from jac.capabilities.clarify import make_clarify_capability
from jac.capabilities.history import estimate_text_tokens, estimate_tokens, force_compact
from jac.capabilities.mcp import make_mcp_capability
from jac.capabilities.plan import make_plan_capability
from jac.capabilities.process import make_process_capability
from jac.capabilities.skills import make_skills_capability
from jac.cli._a2a_banner import print_server_started_banner
from jac.cli.renderer import CliRenderer
from jac.cli.slash import (
    CompactNow,
    Exit,
    InjectUserText,
    RebuildGru,
    RefreshToolsets,
    SlashContext,
    StartA2AServer,
    StopA2AServer,
    SwitchSession,
    UnknownSlashCommand,
    command_names,
    dispatch,
)
from jac.cli.statusbar import StatusState, format_toolbar
from jac.config import get_settings, resolve_context_budget, set_session_context_budget
from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.profiles_crud import get_profile
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.runtime.approval import make_approval_handler
from jac.runtime.events import (
    BudgetHardStop,
    CompactionRefused,
    EventBus,
    PlanReplaced,
    RunCompleted,
    RunFailed,
)
from jac.runtime.gru import build_gru, sub_agent_capabilities
from jac.runtime.hooks import make_hooks
from jac.runtime.modes import reset_mode
from jac.runtime.session import Session
from jac.runtime.sub_agent import (
    SubAgentCapability,
    set_sub_agent_capability,
    set_sub_agent_event_bus,
)
from jac.runtime.sub_agent_usage import (
    reset_sub_agent_stats,
    set_sub_agent_usage_recorder,
)
from jac.runtime.tool_summarize import reset_summarizer_stats, set_summarizer_model
from jac.runtime.usage import BudgetLimits, UsageTracker, make_usage_tracker
from jac.secrets import (
    apply_ad_hoc_model_env,
    apply_profile_env,
    resolve_optional_keys,
    restore_env,
    snapshot_env,
)
from jac.workspace import paths
from jac.workspace.paths import load_prompt
from jac.workspace.session_ctx import set_current_session_id

_EXIT_WORDS = {"exit", "quit", ":q", ":quit"}

# Compact 5-line block-letter "JAC". Kept hard-coded so we don't take a
# runtime dep on figlet; backslashes are literal — raw triple-quoted string.
_BANNER = r"""
      ██╗ █████╗  ██████╗
      ██║██╔══██╗██╔════╝
      ██║███████║██║
 ██   ██║██╔══██║██║
 ╚█████╔╝██║  ██║╚██████╗
  ╚════╝ ╚═╝  ╚═╝ ╚═════╝"""

_BANNER_MIN_WIDTH = 30

console = Console()


class _SlashOnlyCompleter(Completer):
    """Yields completions only when the user is typing a slash command.

    The default ``WordCompleter`` matches the *current word* at the cursor,
    treating ``/`` as a non-word character — so it both (a) fires the
    dropdown after any space (the bug 1.7.b shipped with) and (b) silently
    fails to match ``/re`` against ``/resume``. We do our own matching:

    - The buffer (left of cursor) must start with ``/``.
    - The cursor must still be on the first word (no space yet). Once the
      user has typed ``/model openai...`` we stop offering completions; the
      command name is locked in and the rest is argument prose.
    - Within those rules we suggest every registered name whose ``/name``
      prefix matches the typed text.
    """

    def __init__(self, names: list[str]) -> None:
        self._candidates = [f"/{n}" for n in sorted(names)]

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text:
            return
        for candidate in self._candidates:
            if candidate.startswith(text):
                yield Completion(candidate, start_position=-len(text))


_TOOLBAR_STYLE = Style.from_dict(
    {
        # Dark canvas for the toolbar — ``noreverse`` disables prompt-toolkit's
        # default inverted-video look; explicit bg/fg take over from there.
        "bottom-toolbar": "noreverse bg:ansiblack fg:ansiwhite",
    }
)


def _make_prompt_session(status: StatusState) -> PromptSession[str]:
    paths.USER_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(paths.USER_HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=_SlashOnlyCompleter(list(command_names())),
        bottom_toolbar=lambda: format_toolbar(status),
        style=_TOOLBAR_STYLE,
        multiline=False,
    )


def _greet(
    *,
    model_id: str,
    session: Session,
    resumed: bool,
    restored_plan: list[dict[str, str]] | None = None,
    loose: bool = False,
) -> None:
    # TTY + width gate: don't dump a banner into pipes / CI / cramped panes.
    # highlight=False disables Rich's auto-highlighter so the model id and
    # session timestamp aren't rainbow-painted as if they were URLs/numbers.
    show_banner = console.is_terminal and console.width >= _BANNER_MIN_WIDTH
    if show_banner:
        console.print(f"[bold yellow]{_BANNER}[/bold yellow]", highlight=False)
        console.print(f"[dim]v{__version__}[/dim]", highlight=False)
        console.print()
    else:
        console.print(
            f"[bold yellow]JAC[/bold yellow] [dim]v{__version__}[/dim]",
            highlight=False,
        )

    console.print(f"[dim]model:[/dim]   {model_id}", highlight=False)
    if resumed:
        console.print(
            f"[dim]session:[/dim] {session.session_id} "
            f"[yellow](resumed, {len(session.message_history)} prior messages)[/yellow]",
            highlight=False,
        )
    else:
        console.print(
            f"[dim]session:[/dim] {session.session_id} [green](new)[/green]",
            highlight=False,
        )
    if restored_plan:
        pending = sum(1 for s in restored_plan if s["status"] == "pending")
        console.print(
            f"[dim]plan:[/dim]    {len(restored_plan)} step(s) restored "
            f"[yellow]({pending} pending)[/yellow] "
            "[dim]— Gru can read it via [bold]get_plan()[/bold][/dim]",
            highlight=False,
        )
    if loose:
        console.print(
            f"[dim]workspace:[/dim] [yellow]global[/yellow] "
            f"[dim]({paths.USER_WORKSPACE}) — no project here. "
            "Run [bold]jac init[/bold] to make this folder a project.[/dim]",
            highlight=False,
        )
    console.print("[dim]type 'exit' or Ctrl-D to quit[/dim]", highlight=False)


async def _run_turn(
    gru: Agent,
    bus: EventBus,
    text: str,
    message_history: list,
    usage_tracker: UsageTracker | None = None,
) -> list:
    """Run one agent turn through the bus. Returns the updated message history.

    On successful completion, records the turn's input / output token
    counts into ``usage_tracker`` (D25). The tracker handles JSONL
    persistence and threshold-crossing events; we just hand it the deltas.

    On a **hard failure** (a tool exhausting retries, an MCP server failing
    to connect, a model error) we don't silently discard the turn — that
    wiped the user's message and made Gru "forget" the conversation. Instead
    we persist the messages captured up to the crash (via
    ``capture_run_messages``), closing any dangling tool calls so the history
    stays resumable on the next turn.
    """
    renderer = CliRenderer(console)
    captured: list = []

    async def run_agent_and_signal() -> AgentRunResult[str]:
        nonlocal captured
        with capture_run_messages() as msgs:
            try:
                result = await gru.run(text, message_history=message_history)
            except Exception as exc:
                captured = list(msgs)
                await bus.emit(RunFailed(error=str(exc)))
                raise
        await bus.emit(RunCompleted(output=str(result.output)))
        return result

    agent_task = asyncio.create_task(run_agent_and_signal())
    await renderer.consume(bus)

    try:
        result = await agent_task
    except Exception:
        # The renderer already rendered the error from the bus event. Keep
        # the conversation alive: persist what we captured (sanitized) so the
        # next turn still has context instead of starting from a blank slate.
        renderer.print_final()
        return _recover_failed_history(message_history, captured, text)

    renderer.print_final()
    if usage_tracker is not None:
        usage = result.usage
        await usage_tracker.record(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_tokens", 0),
            cache_write_tokens=getattr(usage, "cache_write_tokens", 0),
        )
    return result.all_messages()


def _close_open_tool_calls(messages: list) -> list:
    """Append synthetic returns for any tool call left unanswered by a crash.

    pydantic-ai refuses to resume a history that ends with an unprocessed
    tool call ("Cannot provide a new user prompt when the message history
    contains unprocessed tool calls"). A run that died mid-tool (retries
    exhausted, server disconnected) leaves exactly that. We pair every open
    ``ToolCallPart`` with a ``ToolReturnPart`` marking it aborted so the next
    turn can continue.
    """
    answered: set[str] = set()
    calls: dict[str, str] = {}
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = part.tool_name
            elif isinstance(part, ToolReturnPart | RetryPromptPart):
                tcid = getattr(part, "tool_call_id", None)
                if tcid:
                    answered.add(tcid)
    open_calls = [(cid, name) for cid, name in calls.items() if cid not in answered]
    if not open_calls:
        return list(messages)
    returns = [
        ToolReturnPart(
            tool_name=name,
            content="(tool call aborted: the turn failed before it returned)",
            tool_call_id=cid,
        )
        for cid, name in open_calls
    ]
    return [*messages, ModelRequest(parts=returns)]


def _recover_failed_history(original: list, captured: list, text: str) -> list:
    """Build a resumable history after a turn crashed.

    Prefers the messages captured during the failed run (they include the
    user's prompt + whatever the model/tools produced), with dangling tool
    calls closed. If nothing was captured (the run died before recording the
    turn — e.g. an MCP server that failed to connect at run start), we
    synthesize the user turn plus a short failure note onto the prior history
    so the user's message and context survive.
    """
    if captured:
        return _close_open_tool_calls(captured)
    return [
        *original,
        ModelRequest(parts=[UserPromptPart(content=text)]),
        ModelResponse(
            parts=[TextPart(content="(the previous turn failed before it could complete)")]
        ),
    ]


async def _repl_loop(
    model_override: str | None = None,
    *,
    profile_name: str | None = None,
    resume_latest: bool = False,
    resume_id: str | None = None,
) -> None:
    # Resolve which session to attach.
    try:
        if resume_id is not None:
            session = Session.resume(resume_id)
            resumed = True
        elif resume_latest:
            session = Session.resume_latest()
            resumed = True
        else:
            session = Session.new()
            resumed = False
    except JacConfigError as exc:
        console.print(f"[red]session error:[/red] {exc}")
        return

    # Make the active session id discoverable to tools (e.g. `remember`)
    # without threading a session object through every call site.
    set_current_session_id(session.session_id)

    # Same pattern for the small-tier model: the tool-result post-processor
    # (Phase A.1) reads it via a ContextVar so capabilities don't have to
    # thread it through. ``None`` disables summarization.
    set_summarizer_model(_resolve_summarizer_model(profile_name))
    reset_summarizer_stats()

    # Best-effort resolve optional feature-keys from the configured secrets
    # backend into os.environ before any tool fires. Today this is just
    # TAVILY_API_KEY (upgrades web_search from DDG to Tavily); future
    # optional keys for non-model features go here too. Missing keys are
    # silently skipped — these gate optional features, not required ones.
    resolve_optional_keys(["TAVILY_API_KEY"])

    # Restore the in-session plan checklist if one was persisted (D27).
    # Malformed files are non-fatal: we warn yellow and continue empty.
    restored_plan, plan_warning = session.load_plan()
    if plan_warning is not None:
        console.print(f"[yellow]warning:[/yellow] {plan_warning}")

    # Build Gru (default tools + caller-supplied hooks/approval/extra caps).
    try:
        bus = EventBus()
        hooks = make_hooks(bus)
        approval = make_approval_handler(bus)
        plan_capability = make_plan_capability(
            bus,
            plan_file=session.plan_file,
            initial_steps=restored_plan or None,
        )
        process_capability = make_process_capability(bus)
        clarify_capability = make_clarify_capability(bus)
        # A2A capability holds the (initially-stopped) guest server.
        # The model is resolved below after `model_id` is finalized;
        # we pass profile_name now (it doesn't change mid-session
        # except via /profile, which rebuilds the capability list).
        a2a_capability = make_a2a_capability(
            bus=bus,
            model=None,  # filled after settings.model resolves below
            profile_name=profile_name,
        )
        # Skill loader (Phase D / D21). Discovers community-format skills
        # from project / user / package directories at construction. The
        # REPL holds a reference so /skill list|use|reload can read +
        # mutate the catalog without a Gru rebuild.
        skills_capability = make_skills_capability()
        # Lambda so /skill reload mid-session is reflected when a future
        # /a2a serve restarts the guest server's AgentCard.
        a2a_capability.skills_getter = lambda: skills_capability.skills
        # MCP server loader (Phase F / D28). Discovers external tool servers
        # from ~/.jac/mcp.json + <repo>/.agents/mcp.json; their tools are
        # deferred-loaded so tool search pulls them in on demand rather than
        # bloating the prompt. /mcp list|reload|enable|disable read + mutate it.
        mcp_capability = make_mcp_capability()
        gru = build_gru(
            model_override=model_override,
            extra_capabilities=[
                hooks,
                approval,
                plan_capability,
                process_capability,
                clarify_capability,
                a2a_capability,
                skills_capability,
                mcp_capability,
            ],
            bus=bus,
            summarizer_model=_resolve_summarizer_model(profile_name),
        )
    except JacConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        return

    # Load the active Profile object so /model and /profile can enumerate
    # tier models. None when the REPL was started with --model.
    active_profile: Profile | None = None
    if profile_name is not None:
        try:
            active_profile = get_profile(profile_name)
        except JacConfigError as exc:
            console.print(f"[red]config error:[/red] {exc}")
            return

    settings = get_settings()
    model_id = model_override or settings.model or "unknown"
    # Finalize the a2a capability's model now that we know what's bound.
    # The guest server can't start without a model, but we don't fail-first
    # here — the slash handler raises a friendly error if /a2a serve is
    # invoked with model_id == "unknown" (no profile / no env / no flag).
    if model_id != "unknown":
        a2a_capability.model = model_id
    # Pull retention + peers from the active profile (falls back to schema
    # defaults when no profile is in play). Both are refreshed on /profile.
    if active_profile is not None:
        a2a_capability.retention_days = active_profile.a2a.context_retention_days
        a2a_capability.allow_private_peers = active_profile.a2a.allow_private_peers
        a2a_capability.profile_peers = dict(active_profile.a2a.peers)
    message_history: list = list(session.message_history)

    # Token-budget tracker (D25). Baseline is summed from usage.jsonl
    # (excluding this session) so project_total survives across sessions.
    usage_tracker = make_usage_tracker(
        session_id=session.session_id,
        bus=bus,
        usage_file=paths.project_usage_file(),
        limits=BudgetLimits(
            session_input_tokens=settings.budget.session_input_tokens,
            session_total_tokens=settings.budget.session_total_tokens,
            project_total_tokens=settings.budget.project_total_tokens,
            warn_pct=settings.budget.warn_pct,
            hardstop_pct=settings.budget.hardstop_pct,
        ),
    )
    # Wire the tracker into the A2A capability so inbound guest calls
    # feed `project_total` (Phase 4.d). The cap was built before the
    # tracker (it needs bus + profile, both available earlier); attach
    # post-construction so the order stays readable.
    a2a_capability.usage_tracker = usage_tracker

    # Phase B — install the sub-agent factory. Requires the active
    # profile (for tier cascade) and a capability list factory (closes
    # over bus + summarizer_model so spawned sub-agents inherit them).
    # ``None`` profile disables spawning (the tool will error fail-fast
    # with a setup message).
    #
    # Closure captures the shared bus-bound capabilities so a spawned
    # sub-agent's tool calls emit onto the same bus the CLI renderer
    # reads (lifecycle events visible) AND its destructive tools route
    # through the same HITL approval handler the main agent uses. Same
    # ``skills_capability`` / ``a2a_capability`` instances are reused so
    # ``/skill reload`` is observed by both surfaces and the guest A2A
    # server isn't duplicated. ``allowed_tools`` / ``channel`` continue
    # to flow from the spawn call site untouched.
    def _sub_agent_capability_factory(
        allowed_tools: list[str] | None = None,
        *,
        channel: Any = None,
    ) -> list[Any]:
        return sub_agent_capabilities(
            allowed_tools,
            channel=channel,
            hooks=hooks,
            approval=approval,
            skills_capability=skills_capability,
            a2a_capability=a2a_capability,
            mcp_capability=mcp_capability,
        )

    if active_profile is not None:
        set_sub_agent_capability(
            SubAgentCapability(
                profile=active_profile,
                base_prompt=load_prompt("sub_agent_system").strip(),
                capability_factory=_sub_agent_capability_factory,
            )
        )
    else:
        set_sub_agent_capability(None)
    reset_sub_agent_stats()

    async def _record_sub_agent(in_tokens: int, out_tokens: int, tier: str) -> None:
        await usage_tracker.add_sub_agent(in_tokens, out_tokens, tier)

    set_sub_agent_usage_recorder(_record_sub_agent)
    # Renderer hook for the D41 bidirectional flow: lifecycle events
    # (SubAgentSpawned / Question / Answer / Completed) flow through the
    # same bus the rest of the renderer already consumes.
    set_sub_agent_event_bus(bus)

    # Status bar — the toolbar callable reads from this on every render.
    # We keep the same reference across the session and mutate fields in
    # place; prompt-toolkit doesn't need a redraw kick.
    status = StatusState(
        model_id=model_id,
        session_id=session.session_id,
        profile_name=profile_name,
        profile=active_profile,
        message_history=message_history,
        budget_pct=usage_tracker.status_pct(),
    )

    prompt_session = _make_prompt_session(status)
    _greet(
        model_id=model_id,
        session=session,
        resumed=resumed,
        restored_plan=restored_plan or None,
        loose=not paths.in_project(),
    )

    # Surface the restored plan as a synthesized `PlanReplaced` event so the
    # renderer paints the checklist panel on the first turn — no special
    # startup-time render path. The event sits buffered until the user types.
    if restored_plan:
        await bus.emit(PlanReplaced(steps=plan_capability.store.snapshot()))

    persisted_capabilities = [
        hooks,
        approval,
        plan_capability,
        process_capability,
        clarify_capability,
        a2a_capability,
        skills_capability,
        mcp_capability,
    ]
    user_prompt = HTML("<ansiyellow><b>» </b></ansiyellow>")
    try:
        while True:
            try:
                text = await prompt_session.prompt_async(user_prompt)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return

            text = text.strip()
            if not text:
                continue
            if text.lower() in _EXIT_WORDS:
                console.print("[dim]bye[/dim]")
                return

            if text.startswith("/"):
                ctx = SlashContext(
                    console=console,
                    session=session,
                    profile_name=profile_name,
                    profile=active_profile,
                    model_id=model_id,
                    usage_tracker=usage_tracker,
                    a2a=a2a_capability,
                    skills=skills_capability,
                    mcp=mcp_capability,
                )
                try:
                    result = dispatch(text, ctx)
                except UnknownSlashCommand as exc:
                    console.print(
                        f"[red]unknown slash command:[/red] /{exc.name}  "
                        "[dim](try [bold]/help[/bold])[/dim]"
                    )
                    continue

                if isinstance(result, Exit):
                    console.print("[dim]bye[/dim]")
                    return
                if isinstance(result, SwitchSession):
                    session, message_history = _switch_session(session, result.session)
                    restored, warning = session.load_plan()
                    if warning is not None:
                        console.print(f"[yellow]warning:[/yellow] {warning}")
                    await plan_capability.switch_session(session.plan_file, restored or None)
                    # Rebuild the usage tracker for the new session — fresh
                    # in-memory counters, project baseline recomputed from
                    # usage.jsonl (excluding the new session id).
                    usage_tracker = make_usage_tracker(
                        session_id=session.session_id,
                        bus=bus,
                        usage_file=paths.project_usage_file(),
                        limits=usage_tracker.limits,
                    )
                    # Re-attach the fresh tracker to A2A so any in-flight
                    # or future inbound calls feed the right session's
                    # project_total counter.
                    a2a_capability.usage_tracker = usage_tracker
                    status.session_id = session.session_id
                    status.message_history = message_history
                    status.budget_pct = usage_tracker.status_pct()
                    status.exact_context_tokens = None  # fresh session, no turn yet
                elif isinstance(result, RebuildGru):
                    rebuilt = _rebuild_gru(
                        new_model_id=result.new_model_id,
                        new_profile_name=result.new_profile_name,
                        current_profile=active_profile,
                        current_profile_name=profile_name,
                        capabilities=persisted_capabilities,
                        bus=bus,
                    )
                    if rebuilt is not None:
                        gru, model_id, profile_name, active_profile = rebuilt
                        status.model_id = model_id
                        status.profile_name = profile_name
                        status.profile = active_profile
                        # Keep A2A capability's metadata in sync so a future
                        # /a2a serve spawns its guest with the new model +
                        # profile. The running server (if any) keeps its
                        # already-bound model — switching mid-flight is a
                        # follow-up (likely Phase 4.c).
                        a2a_capability.model = model_id
                        a2a_capability.profile_name = profile_name
                        if active_profile is not None:
                            a2a_capability.retention_days = (
                                active_profile.a2a.context_retention_days
                            )
                            a2a_capability.allow_private_peers = (
                                active_profile.a2a.allow_private_peers
                            )
                            # Mutate profile_peers in place so the outbound
                            # tool closures (which capture the merged-view
                            # getter) see the new peers immediately.
                            # Session peers are NOT touched on /profile —
                            # they're the operator's per-session overrides
                            # and survive a profile switch.
                            a2a_capability.profile_peers.clear()
                            a2a_capability.profile_peers.update(active_profile.a2a.peers)
                elif isinstance(result, RefreshToolsets):
                    # /mcp reload|enable|disable: the MCP capability's catalog
                    # already changed in place; rebuild Gru against the *same*
                    # model so its get_toolset() is re-consulted. No env dance,
                    # no model switch. Pass the active model explicitly so the
                    # --model (no-profile) path doesn't fall back to settings.
                    if model_id == "unknown":
                        console.print(
                            "[yellow]no model bound; cannot rebuild.[/yellow] "
                            "Set one with [bold]/model[/bold] first."
                        )
                    else:
                        try:
                            gru = build_gru(
                                model_override=model_id,
                                extra_capabilities=persisted_capabilities,
                                bus=bus,
                                summarizer_model=_resolve_summarizer_model(profile_name),
                            )
                        except JacConfigError as exc:
                            console.print(f"[red]rebuild failed:[/red] {exc}")
                        else:
                            if result.note:
                                console.print(f"[green]✓[/green] {result.note}")
                elif isinstance(result, StartA2AServer):
                    await _handle_start_a2a(a2a_capability, result)
                elif isinstance(result, StopA2AServer):
                    await _handle_stop_a2a(a2a_capability)
                elif isinstance(result, CompactNow):
                    before = estimate_tokens(message_history)
                    summarizer = _resolve_summarizer_model(profile_name)
                    new_history, dropped, summary_tokens = await force_compact(
                        message_history, summarizer
                    )
                    if dropped == 0:
                        console.print("[dim]nothing to compact — history is already minimal[/dim]")
                    else:
                        message_history = new_history
                        status.message_history = message_history
                        # Last-turn exact count is now stale (pre-compaction);
                        # fall back to the estimate over the shrunken history
                        # until the next real turn refreshes it.
                        status.exact_context_tokens = None
                        after = estimate_tokens(message_history)
                        try:
                            session.save(message_history)
                        except OSError as exc:
                            console.print(f"[yellow]warning:[/yellow] session save failed: {exc}")
                        note = (
                            f"summarized {dropped} message(s) into ~{summary_tokens:,} tokens"
                            if summary_tokens
                            else f"dropped {dropped} message(s) (no summarizer available)"
                        )
                        console.print(
                            f"[green]✓ compacted[/green] — {note}; "
                            f"context ~{before:,} → ~{after:,} tokens"
                        )
                elif isinstance(result, InjectUserText):
                    # Fall through into the normal turn flow with the
                    # synthesized text — budget checks + persistence apply
                    # the same way a real user prompt would.
                    text = result.text
                    if await _refuse_if_over_budget(bus, message_history, text):
                        continue
                    if await _refuse_if_over_token_budget(bus, usage_tracker):
                        continue
                    message_history = await _run_turn(
                        gru, bus, text, message_history, usage_tracker
                    )
                    status.message_history = message_history
                    status.budget_pct = usage_tracker.status_pct()
                    status.exact_context_tokens = usage_tracker.last_input_tokens
                    try:
                        session.save(message_history)
                    except OSError as exc:
                        console.print(f"[yellow]warning:[/yellow] session save failed: {exc}")
                continue

            if await _refuse_if_over_budget(bus, message_history, text):
                continue
            if await _refuse_if_over_token_budget(bus, usage_tracker):
                continue

            message_history = await _run_turn(gru, bus, text, message_history, usage_tracker)
            status.message_history = message_history
            status.budget_pct = usage_tracker.status_pct()
            status.exact_context_tokens = usage_tracker.last_input_tokens
            # Persist after every completed turn so kills mid-turn don't lose prior turns.
            try:
                session.save(message_history)
            except OSError as exc:
                console.print(f"[yellow]warning:[/yellow] session save failed: {exc}")
    finally:
        # Reap any still-running background processes (dev servers, watchers, …).
        # Best-effort: never raise from cleanup, the REPL is already exiting.
        try:
            await process_capability.shutdown()
        except Exception as exc:
            console.print(f"[yellow]warning:[/yellow] process shutdown: {exc}")
        # Reap the A2A server if /a2a stop wasn't called explicitly. Same
        # best-effort posture as the process reaper — peers see the
        # connection drop, but at least the port is freed for next time.
        try:
            await a2a_capability.shutdown()
        except Exception as exc:
            console.print(f"[yellow]warning:[/yellow] a2a shutdown: {exc}")
        # Reset session-scoped policy so a fresh REPL starts in a clean state.
        reset_mode()
        set_session_context_budget(None)


async def _handle_start_a2a(cap, request: StartA2AServer) -> None:
    """REPL-side driver for the ``StartA2AServer`` slash result.

    Lives here (not in the slash handler) so the uvicorn task is
    created in the REPL's event loop — that's what keeps the server
    alive after the slash returns. Mirrors how ``RebuildGru`` is
    handled in :func:`_rebuild_gru`.
    """
    try:
        info = await cap.start_server(host=request.host, port=request.port, unsafe=request.unsafe)
    except RuntimeError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return
    except (JacConfigError, OSError) as exc:
        console.print(f"[red]A2A serve failed:[/red] {exc}")
        return

    print_server_started_banner(info, console)


async def _handle_stop_a2a(cap) -> None:
    """REPL-side driver for ``StopA2AServer``. Best-effort, never raises."""
    try:
        await cap.stop_server(reason="user")
    except Exception as exc:
        console.print(f"[yellow]a2a stop:[/yellow] {exc}")
        return
    console.print("[green]✓ A2A server stopped[/green]")


async def _refuse_if_over_token_budget(bus: EventBus, tracker: UsageTracker) -> bool:
    """Pre-flight: refuse the turn if any token budget is already at hardstop.

    Per D25 + the locked decision for 1.7.f: strict check — only refuse
    when already past the line. The 80% warn already gave the user a
    heads-up. Emits :class:`BudgetHardStop` so other surfaces (status
    bar, future TUI) get the same signal.
    """
    tripped = tracker.is_over_hardstop()
    if tripped is None:
        return False
    kind, used, budget = tripped
    await bus.emit(BudgetHardStop(kind=kind, used=used, budget=budget))
    console.print(
        f"[red]token budget exceeded[/red] — {kind} at "
        f"[bold]{used:,}/{budget:,}[/bold] tokens.\n"
        "[dim]raise it with [bold]/budget extend N[/bold] for this session, "
        "or edit the [bold]budget:[/bold] block in your config.[/dim]"
    )
    return True


async def _refuse_if_over_budget(bus: EventBus, message_history: list, user_text: str) -> bool:
    """Pre-flight: refuse the turn if context is already above the refuse pct.

    Estimates the tokens we'd send (history + the next user prompt) and
    compares against ``settings.compaction.refuse_pct * max_context_tokens``.
    On refuse: emit :class:`CompactionRefused`, surface an actionable
    message, and return ``True``. The caller skips the model call.
    """
    settings = get_settings().compaction
    # Sliding strategy never refuses — it drops oldest turns at send time and
    # flags the overflow in the status bar. Refusing would defeat the point.
    if settings.strategy == "sliding":
        return False
    budget = resolve_context_budget()
    if budget <= 0:
        return False
    projected = estimate_tokens(message_history) + estimate_text_tokens(user_text)
    pct = int((projected / budget) * 100)
    if pct < settings.refuse_pct:
        return False
    await bus.emit(CompactionRefused(usage_pct=pct))
    console.print(
        f"[red]context at {pct}% of {budget:,}-token budget[/red] — refusing the turn.\n"
        "[dim]free up context with [bold]/compact[/bold] (summarize now), "
        "[bold]/clear[/bold] (start fresh), [bold]/context <N>[/bold] (raise the "
        "budget), or switch [bold]compaction.strategy[/bold] to sliding.[/dim]"
    )
    return True


def _resolve_summarizer_model(profile_name: str | None) -> str | None:
    """Return the small-tier model for the named profile, or ``None``.

    Falls back gracefully when:

    - No profile is in play (``--model`` ad-hoc session, ``profile_name`` is ``None``).
    - The profile has no ``small`` tier (e.g. only ``medium`` configured).
    - The profile fails to load for some reason.

    Returning ``None`` makes the history capability drop-only on compaction —
    safe, no crash; we just lose the summary.
    """
    if profile_name is None:
        return None
    try:
        profile = get_profile(profile_name)
    except JacConfigError:
        return None
    if "small" not in profile.tiers or not profile.tiers["small"]:
        return None
    return profile.tiers["small"][0]


def _rebuild_gru(
    *,
    new_model_id: str,
    new_profile_name: str | None,
    current_profile: Profile | None,
    current_profile_name: str | None,
    capabilities: list,
    bus: EventBus,
) -> tuple[Agent, str, str | None, Profile | None] | None:
    """Attempt to rebuild Gru against a new model/profile.

    Returns ``(new_gru, new_model_id, new_profile_name, new_profile)`` on
    success, or ``None`` on failure (env is restored, warning printed; the
    caller keeps the existing Gru).

    The snapshot includes ``JAC_MODEL`` + every env key either profile could
    touch (``env:`` block + ``required_env_keys()``) + the new model's
    provider-required secrets, so a partial apply rolls back cleanly.
    """
    # Resolve the new profile (if any) up front so unknown names fail before
    # we touch env.
    new_profile: Profile | None = None
    if new_profile_name is not None:
        try:
            new_profile = get_profile(new_profile_name)
        except JacConfigError as exc:
            _warn_switch_failed(exc, current_profile_name)
            return None

    keys_to_track: set[str] = {"JAC_MODEL"}
    if current_profile is not None:
        keys_to_track.update(current_profile.env)
        keys_to_track.update(current_profile.required_env_keys())
    if new_profile is not None:
        keys_to_track.update(new_profile.env)
        keys_to_track.update(new_profile.required_env_keys())
    # Ad-hoc model (or in-profile model swap) — capture the new model's secrets too.
    keys_to_track.update(
        get_provider_registry().required_env_for_prefix(provider_prefix(new_model_id))
    )

    snap = snapshot_env(list(keys_to_track))

    try:
        if new_profile_name is None:
            # Ad-hoc /model PROVIDER:ID — no profile change.
            apply_ad_hoc_model_env(new_model_id)
        elif new_profile is not None and new_profile_name != current_profile_name:
            # Profile switch — apply the new profile fully. apply_profile_env
            # sets JAC_MODEL to default_model(); override below if the caller
            # picked a non-default model (won't fire for /profile NAME, which
            # passes default_model() as new_model_id).
            apply_profile_env(new_profile_name, new_profile)
            if new_model_id != new_profile.default_model():
                import os

                os.environ["JAC_MODEL"] = new_model_id
        else:
            # /model picker — same profile, different model. Re-resolve the
            # new model's required env (no-op when the union already covered it).
            apply_ad_hoc_model_env(new_model_id)

        resolved_summarizer = _resolve_summarizer_model(new_profile_name)
        new_gru = build_gru(
            extra_capabilities=capabilities,
            bus=bus,
            summarizer_model=resolved_summarizer,
        )
        set_summarizer_model(resolved_summarizer)
    except JacConfigError as exc:
        restore_env(snap)
        _warn_switch_failed(exc, current_profile_name)
        return None

    target_profile_name = new_profile_name if new_profile_name is not None else current_profile_name
    target_profile = new_profile if new_profile is not None else current_profile

    summary = f"[green]✓[/green] switched to [bold]{new_model_id}[/bold]"
    if new_profile_name is not None and new_profile_name != current_profile_name:
        summary += f"  [dim](profile: {new_profile_name})[/dim]"
    elif new_profile_name is None:
        summary += "  [dim](ad-hoc, no profile)[/dim]"
    console.print(summary)

    return new_gru, new_model_id, target_profile_name, target_profile


def _warn_switch_failed(exc: JacConfigError, fallback_profile: str | None) -> None:
    """Render a yellow panel explaining the failure and what stayed active."""
    from rich.panel import Panel

    fallback = (
        f"staying on profile [bold]{fallback_profile}[/bold]"
        if fallback_profile is not None
        else "staying on the current model"
    )
    console.print(
        Panel.fit(
            f"[yellow]switch failed[/yellow]\n\n{exc}\n\n{fallback}",
            border_style="yellow",
        )
    )


def _switch_session(old: Session, new: Session) -> tuple[Session, list]:
    """Activate ``new`` as the REPL's session and reset message history.

    Returns the new ``(session, message_history)`` pair. The caller is
    expected to have already drained any in-flight turn — the slash dispatch
    is synchronous so there's nothing in flight by definition.
    """
    set_current_session_id(new.session_id)
    is_resumed = bool(new.message_history)
    if is_resumed:
        console.print(
            f"[dim]session:[/dim] {new.session_id} "
            f"[yellow](resumed, {len(new.message_history)} prior messages)[/yellow]",
            highlight=False,
        )
    else:
        console.print(
            f"[dim]session:[/dim] {new.session_id} [green](new)[/green]",
            highlight=False,
        )
    return new, list(new.message_history)


def run_repl(
    model_override: str | None = None,
    *,
    profile_name: str | None = None,
    resume_latest: bool = False,
    resume_id: str | None = None,
) -> None:
    """Synchronous wrapper around the async REPL loop."""
    asyncio.run(
        _repl_loop(
            model_override=model_override,
            profile_name=profile_name,
            resume_latest=resume_latest,
            resume_id=resume_id,
        )
    )
