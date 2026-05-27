"""``/skill`` — list / use / reload installed skills (Phase D, D21).

Three subcommands:

- ``/skill list`` — table of every loaded skill (name, source, description,
  required tools) so the user can see what's installed and where it came
  from. Mirrors the ``Skills`` block the model sees in its system prompt,
  but with provenance + the full description even when the prompt block
  was truncated under the 2 KB cap.
- ``/skill use NAME`` — inject the skill body as the next user message via
  :class:`InjectUserText`. The REPL runs a real agent turn with the body
  as input, so the model can act on its guidance immediately. Equivalent
  to the model calling ``load_skill`` itself, but routed by the user.
- ``/skill reload`` — re-scan project / user / package directories. Useful
  while authoring a skill so edits show up without restarting Gru.

The handler reads from ``ctx.skills`` (the :class:`SkillsCapability`
instance the REPL wires through :class:`SlashContext`). If skills aren't
wired (tests, headless contexts), the handler prints a friendly hint
and returns ``Handled`` rather than crashing.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from jac.cli.slash.context import SlashContext
from jac.cli.slash.registry import register
from jac.cli.slash.result import Handled, InjectUserText, SlashResult
from jac.workspace import paths

_USAGE = "/skill {list | use NAME | reload}"


@register(
    "skill",
    summary="Manage loaded skills (list / use / reload)",
    usage=_USAGE,
)
def skill_handler(ctx: SlashContext, args: str) -> SlashResult:
    if ctx.skills is None:
        ctx.console.print(
            "[yellow]skills capability is not wired into this session[/yellow] "
            "[dim](this shouldn't happen in the REPL; report as a bug)[/dim]"
        )
        return Handled()

    sub, _, rest = args.partition(" ")
    sub = sub.strip().lower()
    rest = rest.strip()

    if not sub:
        ctx.console.print(f"[dim]usage:[/dim] {_USAGE}")
        return Handled()

    if sub == "list":
        return _handle_list(ctx, rest)
    if sub == "use":
        return _handle_use(ctx, rest)
    if sub == "reload":
        return _handle_reload(ctx, rest)

    ctx.console.print(f"[red]unknown /skill subcommand:[/red] {sub!r}  [dim](try {_USAGE})[/dim]")
    return Handled()


# --- subcommands -------------------------------------------------------


def _handle_list(ctx: SlashContext, rest: str) -> SlashResult:
    if rest:
        ctx.console.print("[dim]/skill list takes no arguments[/dim]")

    assert ctx.skills is not None  # guarded by the parent handler
    skills = ctx.skills.skills
    shadowed = ctx.skills.shadowed
    if not skills and not shadowed:
        ctx.console.print(
            "[dim]no skills installed. Add one under "
            "[bold]~/.jac/skills/<name>/SKILL.md[/bold] or "
            "[bold]<repo>/.agents/skills/<name>/SKILL.md[/bold].[/dim]"
        )
        return Handled()

    if skills:
        table = Table(title="Skills (active)", show_lines=False, header_style="bold")
        table.add_column("name", style="cyan", no_wrap=True)
        table.add_column("source", no_wrap=True)
        table.add_column("description")
        table.add_column("requires", no_wrap=True)

        for name in sorted(skills):
            skill = skills[name]
            requires = ", ".join(skill.frontmatter.tools_required) or "—"
            table.add_row(
                name,
                _source_styled(skill.source),
                skill.description.strip().replace("\n", " "),
                requires,
            )
        ctx.console.print(table)

    # Shadowed entries are skills that parsed cleanly but lost to a
    # higher-priority namesake. Rendering them keeps overrides visible
    # — without this, a user could be staring at the "wrong" code-review
    # body with no clue that their own version was being eclipsed.
    if shadowed:
        shadow_table = Table(
            title="Skills (shadowed — higher-priority namesake wins)",
            show_lines=False,
            header_style="bold yellow",
        )
        shadow_table.add_column("name", style="dim cyan", no_wrap=True)
        shadow_table.add_column("source", no_wrap=True)
        shadow_table.add_column("shadowed by", no_wrap=True)
        shadow_table.add_column("path", overflow="fold")

        for skill in sorted(shadowed, key=lambda s: (s.name, s.source)):
            active = skills.get(skill.name)
            winner = _source_styled(active.source) if active is not None else "?"
            shadow_table.add_row(
                skill.name,
                _source_styled(skill.source),
                winner,
                _display_skill_path(skill.path),
            )
        ctx.console.print(shadow_table)
    return Handled()


def _handle_use(ctx: SlashContext, rest: str) -> SlashResult:
    if not rest:
        ctx.console.print("[dim]usage:[/dim] /skill use NAME")
        return Handled()

    # ``rest`` may carry trailing prose if the user typed ``/skill use foo
    # extra stuff``; accept only the first token as the name and surface a
    # hint that any extras are dropped (rather than silently injecting
    # them into the next turn).
    name, _, extra = rest.partition(" ")
    name = name.strip()
    extra = extra.strip()
    if extra:
        ctx.console.print(
            f"[dim]ignoring extra args after skill name: {extra!r}; "
            "follow up with a normal prompt to add context.[/dim]"
        )

    skills = ctx.skills.skills if ctx.skills else {}
    skill = skills.get(name)
    if skill is None:
        available = ", ".join(sorted(skills)) or "(none installed)"
        ctx.console.print(
            f"[red]unknown skill:[/red] {name!r}  [dim](available: {available})[/dim]"
        )
        return Handled()

    ctx.console.print(
        f"[green]✓[/green] loading skill [bold]{name}[/bold] "
        f"[dim]({skill.source}, {len(skill.body):,} chars) "
        "— Gru will respond next turn[/dim]"
    )
    return InjectUserText(text=skill.body)


def _handle_reload(ctx: SlashContext, rest: str) -> SlashResult:
    if rest:
        ctx.console.print("[dim]/skill reload takes no arguments[/dim]")

    assert ctx.skills is not None  # guarded by the parent handler
    before = set(ctx.skills.skills)
    ctx.skills.reload()
    after = set(ctx.skills.skills)

    added = sorted(after - before)
    removed = sorted(before - after)
    unchanged = sorted(after & before)

    summary = (
        f"[green]✓[/green] reloaded — {len(after)} skill(s) "
        f"[dim](+{len(added)} new, -{len(removed)} removed, "
        f"{len(unchanged)} unchanged)[/dim]"
    )
    ctx.console.print(summary)
    if added:
        ctx.console.print(f"  [green]+ {', '.join(added)}[/green]")
    if removed:
        ctx.console.print(f"  [red]- {', '.join(removed)}[/red]")
    return Handled()


# --- helpers -----------------------------------------------------------


_SOURCE_STYLES: dict[str, str] = {
    "project": "[magenta]project[/magenta]",
    "user": "[blue]user[/blue]",
    "package": "[dim]package[/dim]",
}


def _source_styled(source: str) -> str:
    return _SOURCE_STYLES.get(source, source)


def _display_skill_path(path: Path) -> str:
    """Canonical location string for ``/skill list`` tables.

    Absolute paths fold badly in narrow terminals (Rich splits mid-path,
    so assertions like ``.jac/skills/foo/SKILL.md`` fail even though the
    row is present). Map known skill roots to the same paths we document
    for operators: ``~/.jac/skills/…``, ``.agents/skills/…``, etc.
    """
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(paths.USER_SKILLS_DIR.resolve())
        return f"~/.jac/skills/{rel.as_posix()}"
    except ValueError:
        pass
    if paths.is_in_project_repo():
        try:
            rel = resolved.relative_to(paths.project_skills_dir().resolve())
            return f".agents/skills/{rel.as_posix()}"
        except ValueError:
            pass
    try:
        rel = resolved.relative_to(paths.package_skills_dir().resolve())
        return f"<package>/skills/{rel.as_posix()}"
    except ValueError:
        pass
    return str(path)
