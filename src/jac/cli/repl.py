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

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from pydantic_ai import Agent, AgentRunResult
from rich.console import Console

from jac import __version__
from jac.capabilities.a2a import make_a2a_capability
from jac.capabilities.approval import make_approval_handler
from jac.capabilities.clarify import make_clarify_capability
from jac.capabilities.history import estimate_text_tokens, estimate_tokens
from jac.capabilities.hooks import make_hooks
from jac.capabilities.plan import make_plan_capability
from jac.capabilities.process import make_process_capability
from jac.cli.renderer import CliRenderer
from jac.cli.slash import (
    Exit,
    RebuildGru,
    SlashContext,
    StartA2AServer,
    StopA2AServer,
    SwitchSession,
    UnknownSlashCommand,
    command_names,
    dispatch,
)
from jac.cli.statusbar import StatusState, format_toolbar
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles import Profile, get_profile
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.runtime.bus import EventBus
from jac.runtime.events import (
    BudgetHardStop,
    CompactionRefused,
    PlanReplaced,
    RunCompleted,
    RunFailed,
)
from jac.runtime.gru import build_gru
from jac.runtime.session import Session
from jac.runtime.session_ctx import set_current_session_id
from jac.runtime.usage import BudgetLimits, UsageTracker, make_usage_tracker
from jac.secrets import (
    apply_ad_hoc_model_env,
    apply_profile_env,
    resolve_optional_keys,
    restore_env,
    snapshot_env,
)
from jac.workspace import paths

_EXIT_WORDS = {"exit", "quit", ":q", ":quit"}

# Compact 5-line block-letter "JAC". Kept hard-coded so we don't take a
# runtime dep on figlet; backslashes are literal — raw triple-quoted string.
_BANNER = r"""     _    _    ____
    | |  / \  / ___|
 _  | | / _ \| |
| |_| |/ ___ \ |___
 \___//_/   \_\____|"""

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


def _make_prompt_session(status: StatusState) -> PromptSession[str]:
    paths.USER_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(paths.USER_HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=_SlashOnlyCompleter(list(command_names())),
        bottom_toolbar=lambda: format_toolbar(status),
        multiline=False,
    )


def _greet(
    *,
    model_id: str,
    session: Session,
    resumed: bool,
    restored_plan: list[dict[str, str]] | None = None,
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
    """
    renderer = CliRenderer(console)

    async def run_agent_and_signal() -> AgentRunResult[str]:
        try:
            result = await gru.run(text, message_history=message_history)
        except Exception as exc:
            await bus.emit(RunFailed(error=str(exc)))
            raise
        await bus.emit(RunCompleted(output=str(result.output)))
        return result

    agent_task = asyncio.create_task(run_agent_and_signal())
    await renderer.consume(bus)

    try:
        result = await agent_task
    except Exception:
        # The renderer already captured the error from the bus event.
        renderer.print_final()
        return message_history

    renderer.print_final()
    if usage_tracker is not None:
        usage = result.usage
        await usage_tracker.record(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    return result.all_messages()


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
        gru = build_gru(
            model_override=model_override,
            extra_capabilities=[
                hooks,
                approval,
                plan_capability,
                process_capability,
                clarify_capability,
                a2a_capability,
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
    # Pull retention from the active profile (falls back to schema default).
    if active_profile is not None:
        a2a_capability.retention_days = active_profile.a2a.context_retention_days
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
                    status.session_id = session.session_id
                    status.message_history = message_history
                    status.budget_pct = usage_tracker.status_pct()
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
                elif isinstance(result, StartA2AServer):
                    await _handle_start_a2a(a2a_capability, result)
                elif isinstance(result, StopA2AServer):
                    await _handle_stop_a2a(a2a_capability)
                continue

            if await _refuse_if_over_budget(bus, message_history, text):
                continue
            if await _refuse_if_over_token_budget(bus, usage_tracker):
                continue

            message_history = await _run_turn(gru, bus, text, message_history, usage_tracker)
            status.message_history = message_history
            status.budget_pct = usage_tracker.status_pct()
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

    console.print(
        f"[green]✓ A2A server started:[/green] [bold]{info.url}[/bold]  "
        f"[dim](bind {info.bind_host}:{info.port})[/dim]"
    )
    if info.unsafe:
        console.print(
            "[red]auth: disabled (--unsafe)[/red] "
            "[dim]— card omits securitySchemes; any caller accepted[/dim]"
        )
    else:
        console.print("[dim]auth: bearer token (save it; /a2a token re-prints):[/dim]")
        console.print(f"  [bold]{info.token}[/bold]")
    console.print(f"[dim]agent card: {info.url}/.well-known/agent-card.json[/dim]")


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
    budget = settings.max_context_tokens
    if budget <= 0:
        return False
    projected = estimate_tokens(message_history) + estimate_text_tokens(user_text)
    pct = int((projected / budget) * 100)
    if pct < settings.refuse_pct:
        return False
    await bus.emit(CompactionRefused(usage_pct=pct))
    console.print(
        f"[red]context at {pct}% of {budget:,}-token budget[/red] — refusing the turn.\n"
        "[dim]free up context with [bold]/clear[/bold] (start fresh in place) or "
        "raise [bold]compaction.max_context_tokens[/bold] in your config.[/dim]"
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

        new_gru = build_gru(
            extra_capabilities=capabilities,
            bus=bus,
            summarizer_model=_resolve_summarizer_model(new_profile_name),
        )
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
