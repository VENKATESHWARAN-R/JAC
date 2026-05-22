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
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from pydantic_ai import Agent, AgentRunResult
from rich.console import Console

from jac import __version__
from jac.capabilities.approval import make_approval_handler
from jac.capabilities.clarify import make_clarify_capability
from jac.capabilities.hooks import make_hooks
from jac.capabilities.plan import make_plan_capability
from jac.capabilities.process import make_process_capability
from jac.cli.renderer import CliRenderer
from jac.cli.slash import (
    Exit,
    RebuildGru,
    SlashContext,
    SwitchSession,
    UnknownSlashCommand,
    command_names,
    dispatch,
)
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles import Profile, get_profile
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.runtime.bus import EventBus
from jac.runtime.events import RunCompleted, RunFailed
from jac.runtime.gru import build_gru
from jac.runtime.session import Session
from jac.runtime.session_ctx import set_current_session_id
from jac.secrets import (
    apply_ad_hoc_model_env,
    apply_profile_env,
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


def _make_prompt_session() -> PromptSession[str]:
    paths.USER_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # WordCompleter offers slash-command names when the user types `/`.
    # Non-slash input falls through unchanged — no completer matches.
    completer = WordCompleter(
        [f"/{name}" for name in command_names()],
        ignore_case=False,
        match_middle=False,
    )
    return PromptSession(
        history=FileHistory(str(paths.USER_HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        multiline=False,
    )


def _greet(*, model_id: str, session: Session, resumed: bool) -> None:
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
    console.print("[dim]type 'exit' or Ctrl-D to quit[/dim]", highlight=False)


async def _run_turn(gru: Agent, bus: EventBus, text: str, message_history: list) -> list:
    """Run one agent turn through the bus. Returns the updated message history."""
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

    # Build Gru (default tools + caller-supplied hooks/approval/extra caps).
    try:
        bus = EventBus()
        hooks = make_hooks(bus)
        approval = make_approval_handler(bus)
        plan_capability = make_plan_capability(bus)
        process_capability = make_process_capability(bus)
        clarify_capability = make_clarify_capability(bus)
        gru = build_gru(
            model_override=model_override,
            extra_capabilities=[
                hooks,
                approval,
                plan_capability,
                process_capability,
                clarify_capability,
            ],
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

    model_id = model_override or get_settings().model or "unknown"
    prompt_session = _make_prompt_session()
    _greet(model_id=model_id, session=session, resumed=resumed)

    persisted_capabilities = [
        hooks,
        approval,
        plan_capability,
        process_capability,
        clarify_capability,
    ]

    message_history: list = list(session.message_history)
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
                elif isinstance(result, RebuildGru):
                    rebuilt = _rebuild_gru(
                        new_model_id=result.new_model_id,
                        new_profile_name=result.new_profile_name,
                        current_profile=active_profile,
                        current_profile_name=profile_name,
                        capabilities=persisted_capabilities,
                    )
                    if rebuilt is not None:
                        gru, model_id, profile_name, active_profile = rebuilt
                continue

            message_history = await _run_turn(gru, bus, text, message_history)
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


def _rebuild_gru(
    *,
    new_model_id: str,
    new_profile_name: str | None,
    current_profile: Profile | None,
    current_profile_name: str | None,
    capabilities: list,
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

        new_gru = build_gru(extra_capabilities=capabilities)
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
