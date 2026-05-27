"""CLI renderer — consumes JAC events and draws to the terminal.

The renderer owns no runtime state and makes no decisions about what the
agent should do. It only observes events and renders them. This separation
is what lets us add a TUI or web surface later without changing the
runtime: drop in a different renderer with the same bus interface.

Approval flow: when an :class:`ApprovalRequest` arrives, the renderer
pauses the status spinner, prompts the user via ``rich``'s Confirm, and
resolves the request's future. The runtime's approval handler awaits that
future before continuing the agent loop.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt

from jac.runtime.events import (
    A2AInboundCall,
    A2AInboundCompleted,
    A2AOutboundCall,
    A2AOutboundCompleted,
    A2AOutboundTokenMinted,
    ApprovalRequest,
    ApprovalResponse,
    BudgetHardStop,
    BudgetWarning,
    ClarifyRequest,
    ClarifyResponse,
    CompactionRefused,
    CompactionTriggered,
    CompactionWarning,
    EventBus,
    JacEvent,
    ModelRequestStarted,
    PlanReplaced,
    PlanStepStatus,
    PlanStepUpdated,
    PlanStepView,
    ProcessExited,
    ProcessStarted,
    RunCompleted,
    RunFailed,
    SubAgentAnswer,
    SubAgentCompleted,
    SubAgentQuestion,
    SubAgentSpawned,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    is_terminal,
)

# Status-line easter eggs — minion-adjacent gibberish, not always "thinking…"
_THINKING_LABELS: tuple[str, ...] = (
    "thinking…",
    "bello…",
    "bee do bee do…",
    "gelato…",
)

_ARG_VALUE_TRUNCATE_AT = 300
_TOOL_ARG_INLINE_TRUNCATE_AT = 60
_FEEDBACK_TRUNCATE_AT = 600  # cap free-text inputs from approval / clarify

_PLAN_GLYPH: dict[PlanStepStatus, str] = {
    "pending": "[dim]○[/dim]",
    "in_progress": "[yellow]◐[/yellow]",
    "completed": "[green]●[/green]",
}


def _thinking_label() -> str:
    return f"[dim]{random.choice(_THINKING_LABELS)}[/dim]"


def _summarize_tool_args(args: dict[str, Any]) -> str:
    """One-line summary of tool args for the persistent scrollback log.

    Skips ``reason`` (already shown separately), takes the first remaining
    arg as the most-meaningful one, and truncates aggressively. Full args
    still appear in the approval panel for gated tools.
    """
    items = [(k, v) for k, v in args.items() if k != "reason"]
    if not items:
        return ""
    key, value = items[0]
    text = str(value).replace("\n", " ")
    if len(text) > _TOOL_ARG_INLINE_TRUNCATE_AT:
        text = text[: _TOOL_ARG_INLINE_TRUNCATE_AT - 1] + "…"
    suffix = f" · +{len(items) - 1} more" if len(items) > 1 else ""
    return f"{key}={text}{suffix}"


def _render_plan(steps: tuple[PlanStepView, ...]) -> Panel:
    if not steps:
        body = "[dim](empty plan)[/dim]"
    else:
        lines: list[str] = []
        for step in steps:
            glyph = _PLAN_GLYPH[step.status]
            text = step.text
            if step.status == "completed":
                text = f"[dim]{text}[/dim]"
            elif step.status == "in_progress":
                text = f"[bold]{text}[/bold]"
            lines.append(f"{glyph} {step.index}. {text}")
        body = "\n".join(lines)
    return Panel(body, title="plan", border_style="yellow", padding=(0, 1))


class CliRenderer:
    """Consume events for one turn; draw to a :class:`Console`.

    Usage::

        renderer = CliRenderer(console)
        await renderer.consume(bus)   # returns when terminal event arrives
        renderer.print_final()         # prints final output or error
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.final_output: str | None = None
        self.error: str | None = None
        self._plan: tuple[PlanStepView, ...] = ()

    async def consume(self, bus: EventBus) -> None:
        """Render events until a terminal event arrives, then return."""
        with self.console.status(_thinking_label(), spinner="dots") as status:
            async for event in bus.stream():
                if isinstance(event, ApprovalRequest):
                    status.stop()
                    response = await self._prompt_approval(event)
                    event.response_future.set_result(response)
                    status.start()
                    continue
                if isinstance(event, ClarifyRequest):
                    status.stop()
                    response = await self._prompt_clarify(event)
                    event.response_future.set_result(response)
                    status.start()
                    continue
                self._handle(event, status)
                if is_terminal(event):
                    return

    def _handle(self, event: JacEvent, status: Any) -> None:
        if isinstance(event, ModelRequestStarted):
            status.update(_thinking_label())
        elif isinstance(event, ToolCallStarted):
            label = event.tool_name
            if event.reason:
                label = f"{label} — {event.reason}"
            status.update(f"[dim]→ {label}[/dim]")
            # Also leave a persistent trace in scrollback. The status line is
            # transient; this line is what the user sees on scroll-back.
            # highlight=False — args often contain paths/numbers we don't want
            # Rich's auto-highlighter to rainbow-paint.
            arg_summary = _summarize_tool_args(event.args)
            tail = f" [dim]{arg_summary}[/dim]" if arg_summary else ""
            self.console.print(
                f"[yellow]→[/yellow] [bold]{event.tool_name}[/bold]{tail}",
                highlight=False,
            )
        elif isinstance(event, ToolCallCompleted):
            status.update(_thinking_label())
        elif isinstance(event, ToolCallFailed):
            # Print errors inline; the agent loop may recover.
            self.console.print(
                f"[red]✗[/red] [bold]{event.tool_name}[/bold] [dim]failed:[/dim] {event.error}",
                highlight=False,
            )
        elif isinstance(event, PlanReplaced):
            self._plan = event.steps
            status.stop()
            self.console.print(_render_plan(self._plan))
            status.start()
        elif isinstance(event, PlanStepUpdated):
            self._plan = tuple(
                PlanStepView(
                    index=s.index,
                    text=s.text,
                    status=event.status if s.index == event.index else s.status,
                )
                for s in self._plan
            )
            status.stop()
            self.console.print(_render_plan(self._plan))
            status.start()
        elif isinstance(event, ProcessStarted):
            label = f" ({event.name})" if event.name else ""
            self.console.print(
                f"[yellow]▶ process[/yellow] {event.task_id}{label}: [dim]{event.command}[/dim]"
            )
        elif isinstance(event, ProcessExited):
            color = (
                "green" if event.exit_code == 0 else ("red" if event.exit_code < 0 else "yellow")
            )
            self.console.print(
                f"[{color}]■ process[/{color}] {event.task_id} "
                f"exited [dim](code={event.exit_code})[/dim]"
            )
        elif isinstance(event, CompactionWarning):
            self.console.print(
                f"[yellow]context at {event.usage_pct}%[/yellow] "
                "[dim]— auto-compact triggers at 70%[/dim]"
            )
        elif isinstance(event, CompactionTriggered):
            self.console.print(
                f"[green]✦ compacted[/green] {event.dropped_count} messages "
                f"[dim](~{event.summary_tokens} summary tokens; "
                f"context now {event.usage_pct}%)[/dim]"
            )
        elif isinstance(event, CompactionRefused):
            # The REPL prints its own actionable message; this lets future
            # surfaces (TUI / status bar in 1.7.b) react to the same signal.
            pass
        elif isinstance(event, BudgetWarning):
            self.console.print(
                f"[yellow]budget {event.kind} at {event.pct}%[/yellow] "
                f"[dim]({event.used:,}/{event.budget:,} tokens; "
                "hard-stop fires at 100% — /budget extend N to raise)[/dim]"
            )
        elif isinstance(event, BudgetHardStop):
            # The REPL prints the actionable message on the refusal path; this
            # event lets other surfaces (status bar) react. We don't print
            # again here to avoid double notices.
            pass
        elif isinstance(event, A2AInboundCall):
            unsafe_tag = " [red](unsafe)[/red]" if event.peer_id == "unsafe" else ""
            self.console.print(
                f"[cyan][a2a in ←][/cyan] [bold]{event.peer_id}[/bold]{unsafe_tag} "
                f"[dim](task {event.task_id[:8]})[/dim]: {event.message_preview}",
                highlight=False,
            )
        elif isinstance(event, A2AInboundCompleted):
            state_color = "green" if event.state == "completed" else "red"
            self.console.print(
                f"[cyan][a2a in ✓][/cyan] [{state_color}]{event.state}[/{state_color}] "
                f"[dim]{event.peer_id} (task {event.task_id[:8]}, "
                f"{event.duration_ms}ms, {event.tokens_used} tok)[/dim]",
                highlight=False,
            )
        elif isinstance(event, A2AOutboundCall):
            # We initiated → arrow points away. `target` is the peer name
            # when called by name, else the raw URL — whichever Gru passed.
            self.console.print(
                f"[cyan][a2a out →][/cyan] [bold]{event.target}[/bold]: {event.message_preview}",
                highlight=False,
            )
        elif isinstance(event, A2AOutboundCompleted):
            state_color = "green" if event.state == "completed" else "red"
            self.console.print(
                f"[cyan][a2a out ✓][/cyan] [{state_color}]{event.state}[/{state_color}] "
                f"[dim]{event.target} ({event.duration_ms}ms)[/dim]",
                highlight=False,
            )
        elif isinstance(event, A2AOutboundTokenMinted):
            # OAuth2 round-tripped to the IDP and got a fresh access token.
            # Surface as a muted single line so the operator sees IDP
            # traffic without it dominating scrollback.
            target = event.peer_name or event.token_url
            self.console.print(
                f"[cyan][a2a token][/cyan] [dim]minted access token for "
                f"[bold]{target}[/bold] (expires in {event.expires_in_s}s)[/dim]",
                highlight=False,
            )
        elif isinstance(event, SubAgentSpawned):
            # Bidirectional worker is running. Sequential spawns get
            # visibility via the ToolCallStarted line above; this panel
            # exists because bidirectional workers may park mid-run and
            # the user needs to see the spawn_id to follow the conversation.
            status.stop()
            self.console.print(
                Panel(
                    f"[bold]objective:[/bold] {event.objective}",
                    title=(
                        f"[blue]▶ sub-agent[/blue] [bold]{event.spawn_id}[/bold] "
                        f"[dim]· tier={event.tier} · {event.model}[/dim]"
                    ),
                    title_align="left",
                    border_style="blue",
                    padding=(0, 1),
                )
            )
            status.start()
        elif isinstance(event, SubAgentQuestion):
            status.stop()
            self.console.print(
                Panel(
                    event.question,
                    title=(
                        f"[yellow]⏸ sub-agent → main[/yellow] "
                        f"[bold]{event.spawn_id}[/bold] "
                        f"[dim]· round-trip {event.round_trip}[/dim]"
                    ),
                    title_align="left",
                    border_style="yellow",
                    padding=(0, 1),
                )
            )
            status.start()
        elif isinstance(event, SubAgentAnswer):
            status.stop()
            self.console.print(
                Panel(
                    event.answer,
                    title=(f"[cyan]↩ main → sub-agent[/cyan] [bold]{event.spawn_id}[/bold]"),
                    title_align="left",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )
            status.start()
        elif isinstance(event, SubAgentCompleted):
            exit_color = "green" if event.exit_status == "ok" else "red"
            self.console.print(
                f"[{exit_color}]✓ sub-agent[/{exit_color}] "
                f"[bold]{event.spawn_id}[/bold] [dim]done · "
                f"turns={event.turns_used} · exit={event.exit_status} · "
                f"asks={event.ask_main_agent_count}[/dim]",
                highlight=False,
            )
        elif isinstance(event, RunCompleted):
            self.final_output = event.output
        elif isinstance(event, RunFailed):
            self.error = event.error

    async def _prompt_clarify(self, event: ClarifyRequest) -> ClarifyResponse:
        """Render the clarify panel and ask the user to pick an option.

        The final menu entry is always "Type your own answer" (D26) — picking
        it opens a free-text prompt and the response is marked ``free_text``.
        """
        body: list[str] = [event.question, ""]
        for i, opt in enumerate(event.options, start=1):
            body.append(f"  [bold yellow]{i}[/bold yellow]. {opt}")
        free_text_index = len(event.options) + 1
        body.append(
            f"  [bold yellow]{free_text_index}[/bold yellow]. [dim]Type your own answer[/dim]"
        )
        self.console.print()
        self.console.print(Panel("\n".join(body), title="clarify", border_style="yellow"))
        choices = [str(i) for i in range(1, free_text_index + 1)]
        try:
            picked = await asyncio.to_thread(
                IntPrompt.ask,
                "Pick one",
                choices=choices,
                show_choices=False,
                console=self.console,
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print("[dim]cancelled[/dim]")
            return ClarifyResponse(selected_index=None, selected_text=None, cancelled=True)
        index = picked if isinstance(picked, int) else int(str(picked))
        if index == free_text_index:
            return await self._collect_clarify_free_text()
        return ClarifyResponse(
            selected_index=index,
            selected_text=event.options[index - 1],
            cancelled=False,
        )

    async def _collect_clarify_free_text(self) -> ClarifyResponse:
        """Collect a free-text answer for the clarify prompt (D26)."""
        try:
            raw = await asyncio.to_thread(
                Prompt.ask,
                "Your answer",
                default="",
                show_default=False,
                console=self.console,
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print("[dim]cancelled[/dim]")
            return ClarifyResponse(selected_index=None, selected_text=None, cancelled=True)
        text = str(raw or "").strip()
        if not text:
            self.console.print("[dim]cancelled[/dim]")
            return ClarifyResponse(selected_index=None, selected_text=None, cancelled=True)
        if len(text) > _FEEDBACK_TRUNCATE_AT:
            text = text[:_FEEDBACK_TRUNCATE_AT]
        return ClarifyResponse(
            selected_index=None,
            selected_text=text,
            cancelled=False,
            free_text=True,
        )

    async def _prompt_approval(self, event: ApprovalRequest) -> ApprovalResponse:
        """Render the approval panel and ask the user.

        Three-way prompt (D26): ``y`` approves, ``n`` denies, ``r`` denies and
        opens a follow-up text input the user can use to redirect the model
        in-band without spending a turn.
        """
        body: list[str] = [f"[bold]{event.tool_name}[/bold]"]
        if event.reason:
            body.append(f"[dim]reason:[/dim] {event.reason}")
        for key, value in event.args.items():
            if key == "reason":
                continue
            value_str = str(value)
            if len(value_str) > _ARG_VALUE_TRUNCATE_AT:
                value_str = value_str[: _ARG_VALUE_TRUNCATE_AT - 1] + "…"
            body.append(f"[dim]{key}:[/dim] {value_str}")

        self.console.print()
        self.console.print(Panel("\n".join(body), title="approval needed", border_style="yellow"))
        try:
            choice = await asyncio.to_thread(
                Prompt.ask,
                "[bold yellow]y[/bold yellow]es / [bold yellow]n[/bold yellow]o / "
                "[bold yellow]r[/bold yellow]edirect with feedback",
                choices=["y", "n", "r"],
                default="n",
                show_choices=False,
                console=self.console,
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print("[dim]denied[/dim]")
            return ApprovalResponse(approved=False)
        choice = str(choice or "n").lower()
        if choice == "y":
            return ApprovalResponse(approved=True)
        if choice == "r":
            return await self._collect_approval_feedback()
        return ApprovalResponse(approved=False)

    async def _collect_approval_feedback(self) -> ApprovalResponse:
        """Collect a redirection on the approval deny path (D26).

        Empty input degrades to a plain deny — Ctrl-C does the same. The
        text is capped at :data:`_FEEDBACK_TRUNCATE_AT` chars to keep the
        tool result bounded.
        """
        try:
            raw = await asyncio.to_thread(
                Prompt.ask,
                "Tell Gru what to do instead",
                default="",
                show_default=False,
                console=self.console,
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print("[dim]denied (no feedback)[/dim]")
            return ApprovalResponse(approved=False)
        feedback = str(raw or "").strip()
        if not feedback:
            return ApprovalResponse(approved=False)
        if len(feedback) > _FEEDBACK_TRUNCATE_AT:
            feedback = feedback[:_FEEDBACK_TRUNCATE_AT]
        return ApprovalResponse(approved=False, feedback=feedback)

    def print_final(self) -> None:
        """Render the final turn output (or error) after :meth:`consume` returns."""
        if self.final_output is not None:
            self.console.print()
            self.console.print("[bold yellow]✦ Gru[/bold yellow]")
            self.console.print(Markdown(self.final_output))
            self.console.print()
        if self.error is not None:
            self.console.print(f"[red]error:[/red] {self.error}")
            self.console.print()
