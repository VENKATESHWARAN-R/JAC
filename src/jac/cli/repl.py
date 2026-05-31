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
from prompt_toolkit.styles import Style
from rich.console import Console

from jac import __version__
from jac.capabilities.history import estimate_tokens, force_compact
from jac.cli.renderer import CliRenderer
from jac.cli.slash import (
    CompactNow,
    Exit,
    InjectUserText,
    SlashContext,
    SwitchSession,
    UnknownSlashCommand,
    command_names,
    dispatch,
)
from jac.cli.statusbar import StatusState, format_toolbar
from jac.config import resolve_context_budget, set_session_context_budget
from jac.errors import JacConfigError
from jac.runtime.bootstrap import build_session_runtime, resolve_summarizer_model
from jac.runtime.control import SessionController
from jac.runtime.driver import SessionDriver
from jac.runtime.events import (
    EventBus,
    PlanReplaced,
)
from jac.runtime.modes import reset_mode
from jac.runtime.session import Session
from jac.runtime.usage import make_usage_tracker
from jac.workspace import paths
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


async def _run_turn(driver: SessionDriver, bus: EventBus, text: str, message_history: list) -> list:
    """CLI wrapper around :meth:`SessionDriver.run_turn`.

    The surface-agnostic turn logic (running the agent, recording usage,
    recovering a failed history) lives in the driver (R5). This wrapper owns
    only the CLI-specific orchestration: spin up a ``CliRenderer``, run the
    driver concurrently, consume the bus until the terminal event, then print
    the final frame. A browser/SDK surface writes its own equivalent of this
    function around the same ``driver.run_turn`` call.
    """
    renderer = CliRenderer(console)
    agent_task = asyncio.create_task(driver.run_turn(text, message_history))
    await renderer.consume(bus)
    result = await agent_task
    renderer.print_final()
    return result.message_history


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

    # Restore the in-session plan checklist if one was persisted (D27).
    # Malformed files are non-fatal: we warn yellow and continue empty.
    restored_plan, plan_warning = session.load_plan()
    if plan_warning is not None:
        console.print(f"[yellow]warning:[/yellow] {plan_warning}")

    # Build the full session engine (bus + hooks + approval + capabilities +
    # Gru + driver + usage tracker + sub-agent wiring). Surface-agnostic — the
    # web chat surface calls the same builder; the REPL keeps only its renderer
    # half (status bar, prompt, CliRenderer) below.
    try:
        rt = build_session_runtime(
            session,
            model_override=model_override,
            profile_name=profile_name,
            restored_plan=restored_plan,
        )
    except JacConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        return

    # hooks / approval / clarify / persisted_capabilities live on `rt`; the REPL
    # only names the handles it references below (status, slash, finally). Gru
    # itself is reached via `driver.gru` — the control plane swaps it in place
    # on `rt.driver` during a model/profile/toolset change, so the REPL never
    # holds its own `gru` reference.
    bus = rt.bus
    driver = rt.driver
    usage_tracker = rt.usage_tracker
    plan_capability = rt.plan_capability
    process_capability = rt.process_capability
    a2a_capability = rt.a2a_capability
    skills_capability = rt.skills_capability
    mcp_capability = rt.mcp_capability
    active_profile = rt.active_profile
    model_id = rt.model_id
    message_history: list = list(session.message_history)

    # Surface-agnostic control plane: every runtime mutation a slash command
    # triggers (switch model/profile, toggle/reload MCP, reload skills) goes
    # through this, mutating `rt` in place. The web surface drives the same
    # verbs. After each dispatch the REPL re-syncs its display locals from `rt`.
    controller = SessionController(rt)

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
                    controller=controller,
                )
                try:
                    result = dispatch(text, ctx)
                except UnknownSlashCommand as exc:
                    console.print(
                        f"[red]unknown slash command:[/red] /{exc.name}  "
                        "[dim](try [bold]/help[/bold])[/dim]"
                    )
                    continue

                # A control-plane verb (via ctx.controller) may have switched
                # model/profile or rebuilt Gru in place on `rt`. Re-sync the
                # surface's view so the status bar + next SlashContext reflect
                # it. No-op when the command changed nothing. The A2A metadata
                # sync now lives in the controller, not here.
                model_id = rt.model_id
                profile_name = rt.profile_name
                active_profile = rt.active_profile
                status.model_id = model_id
                status.profile_name = profile_name
                status.profile = active_profile

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
                    driver.usage_tracker = usage_tracker
                    status.session_id = session.session_id
                    status.message_history = message_history
                    status.budget_pct = usage_tracker.status_pct()
                    status.exact_context_tokens = None  # fresh session, no turn yet
                elif isinstance(result, CompactNow):
                    before = estimate_tokens(message_history)
                    summarizer = resolve_summarizer_model(profile_name)
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
                    if await _refuse_if_over_budget(driver, message_history, text):
                        continue
                    if await _refuse_if_over_token_budget(driver):
                        continue
                    message_history = await _run_turn(driver, bus, text, message_history)
                    status.message_history = message_history
                    status.budget_pct = usage_tracker.status_pct()
                    status.exact_context_tokens = usage_tracker.last_input_tokens
                    try:
                        session.save(message_history)
                    except OSError as exc:
                        console.print(f"[yellow]warning:[/yellow] session save failed: {exc}")
                continue

            if await _refuse_if_over_budget(driver, message_history, text):
                continue
            if await _refuse_if_over_token_budget(driver):
                continue

            message_history = await _run_turn(driver, bus, text, message_history)
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
        # Defensive: the REPL no longer starts an inbound A2A server (that's
        # `jac a2a serve` only), so this is normally a no-op — kept as a
        # best-effort reaper in case the capability ever holds a live task.
        try:
            await a2a_capability.shutdown()
        except Exception as exc:
            console.print(f"[yellow]warning:[/yellow] a2a shutdown: {exc}")
        # Reset session-scoped policy so a fresh REPL starts in a clean state.
        reset_mode()
        set_session_context_budget(None)


async def _refuse_if_over_token_budget(driver: SessionDriver) -> bool:
    """CLI wrapper: run the driver's token-budget guard, print on refusal.

    The check + the :class:`BudgetHardStop` emission (with ``suggested_action``
    for other surfaces) live in :meth:`SessionDriver.check_token_budget`; this
    renders the styled CLI message and returns whether the turn was refused.
    """
    event = await driver.check_token_budget()
    if event is None:
        return False
    console.print(
        f"[red]token budget exceeded[/red] — {event.kind} at "
        f"[bold]{event.used:,}/{event.budget:,}[/bold] tokens.\n"
        "[dim]raise it with [bold]/budget extend N[/bold] for this session, "
        "or edit the [bold]budget:[/bold] block in your config.[/dim]"
    )
    return True


async def _refuse_if_over_budget(
    driver: SessionDriver, message_history: list, user_text: str
) -> bool:
    """CLI wrapper: run the driver's context-budget guard, print on refusal.

    The estimate + the :class:`CompactionRefused` emission live in
    :meth:`SessionDriver.check_context_budget`; this renders the styled CLI
    message and returns whether the turn was refused.
    """
    event = await driver.check_context_budget(message_history, user_text)
    if event is None:
        return False
    budget = resolve_context_budget()
    console.print(
        f"[red]context at {event.usage_pct}% of {budget:,}-token budget[/red] — "
        "refusing the turn.\n"
        "[dim]free up context with [bold]/compact[/bold] (summarize now), "
        "[bold]/clear[/bold] (start fresh), [bold]/context <N>[/bold] (raise the "
        "budget), or switch [bold]compaction.strategy[/bold] to sliding.[/dim]"
    )
    return True


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
