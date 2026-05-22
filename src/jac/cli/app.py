"""JAC CLI entry point.

Multi-command Typer app:

- ``jac``                — start interactive REPL with the default profile.
- ``jac --profile NAME`` — use a specific profile for this invocation.
- ``jac --resume``       — resume the latest session in this project.
- ``jac --session ID``   — resume a specific session by id.
- ``jac init``           — interactive wizard: secrets backend, profile, keys.
- ``jac profiles ...``   — list / use / remove profiles.
- ``jac keys ...``       — inspect / set / unset stored credentials.
- ``jac sessions``       — list project sessions.

The root callback runs the silent workspace bootstrap on every invocation
so ``~/.jac/`` always exists. Profile activation happens **only on the REPL
path** — subcommands manage their own state and shouldn't fail-first on
missing credentials.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from jac.capabilities.observability import setup_observability
from jac.cli.keys_cmd import app as keys_app
from jac.cli.profiles_cmd import app as profiles_app
from jac.errors import JacConfigError
from jac.secrets import apply_ad_hoc_model_env, apply_profile_env
from jac.workspace.bootstrap import ensure_user_workspace

app = typer.Typer(
    name="jac",
    help="JAC — Just Another Companion/CLI, An agentic harness built on Pydantic AI.",
    no_args_is_help=False,
    add_completion=False,
)
app.add_typer(profiles_app, name="profiles")
app.add_typer(keys_app, name="keys")

console = Console()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Raw model id, bypasses profile (e.g. 'anthropic:claude-opus-4-6').",
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            "-p",
            help="Profile to activate for this session (see `jac profiles`).",
        ),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume",
            "-r",
            help="Resume the latest session in this project.",
        ),
    ] = False,
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            "-s",
            help="Resume a specific session by id (see `jac sessions`).",
        ),
    ] = None,
) -> None:
    """JAC — start an interactive session. Use `jac init` for first-time setup."""
    just_created = ensure_user_workspace()
    setup_observability()

    if just_created:
        console.print(
            "[dim]first run — created skeleton at ~/.jac/. "
            "Run [bold]jac init[/bold] for guided setup.[/dim]"
        )

    if ctx.invoked_subcommand is not None:
        # Subcommand handles itself. No profile activation here — subcommands
        # like `keys` or `profiles` don't need a usable model.
        return

    # REPL path. Activate the right env BEFORE invoking the REPL so build_gru
    # sees JAC_MODEL and the resolved secrets in os.environ.
    try:
        active_profile_name = _activate_for_repl(model_override=model, profile_name=profile)
    except JacConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from None

    from jac.cli.repl import run_repl

    run_repl(
        model_override=model,
        profile_name=active_profile_name,
        resume_latest=resume,
        resume_id=session,
    )


@app.command("init")
def init_command() -> None:
    """Run the interactive setup wizard (provider + model + secrets + profile)."""
    from jac.cli.init import run_init

    run_init()


@app.command("sessions")
def sessions_command() -> None:
    """List sessions for this project, oldest → newest."""
    from jac.cli.session_view import render_session_listing

    render_session_listing(console, in_repl=False)


# ---------- internal ----------


def _activate_for_repl(*, model_override: str | None, profile_name: str | None) -> str | None:
    """Resolve and apply the profile that this REPL turn will use.

    Sets ``JAC_MODEL`` + any required env vars in ``os.environ`` so the rest
    of the runtime (which reads ``settings.model`` and lets pydantic-ai
    construct providers from env) just works.

    Returns the active profile name so the REPL can surface it (status bar,
    ``/profile`` slash) — ``None`` when ``--model`` bypasses the profile path.

    - If ``model_override`` is given, JAC still resolves the model's required
      credentials best-effort (so ``--model anthropic:...`` with the key in
      keyring works without an explicit ``--profile``).
    - Otherwise the active profile is the CLI ``--profile`` flag, falling
      back to ``default_profile``.
    """
    if model_override is not None:
        # Best-effort credential resolution for the ad-hoc model.
        apply_ad_hoc_model_env(model_override)
        return None

    from jac.profiles import get_profile, resolve_active_profile_name

    active_name = resolve_active_profile_name(profile_name)
    active = get_profile(active_name)
    apply_profile_env(active_name, active)
    return active_name


def main() -> None:
    """Console-script entry point. See ``pyproject.toml`` ``[project.scripts]``."""
    app()


if __name__ == "__main__":
    main()
