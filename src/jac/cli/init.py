"""``jac init`` — interactive onboarding / add-a-profile wizard.

First run: pick a secrets backend, then add the first profile.
Subsequent runs: just add another profile (or update an existing one).

Honest design notes:

- Profile names are validated against ``[a-z0-9-]+`` (no spaces, shell-safe).
- When a known env var is already exported, we offer to import it into the
  chosen backend with an *explicit* prompt — never silent. The user might be
  on a machine with their employer's API key in env and want to keep their
  personal key separate.
"""

from __future__ import annotations

import os
from typing import Any, cast

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from jac.errors import JacConfigError
from jac.profiles import (
    Profile,
    add_or_update_profile,
    list_profiles,
    validate_profile_name,
)
from jac.secrets import SecretBackendName, get_backend
from jac.workspace import paths
from jac.workspace.bootstrap import ensure_user_workspace

console = Console()


PROVIDER_CHOICES: dict[str, dict[str, str]] = {
    "anthropic": {"suggested_model": "claude-sonnet-4-5", "format": "anthropic:{model}"},
    "openai": {"suggested_model": "gpt-5", "format": "openai:{model}"},
    "google": {"suggested_model": "gemini-2.5-flash", "format": "google-gla:{model}"},
    "ollama": {"suggested_model": "gemma3:e2b", "format": "ollama:{model}"},
    "openrouter": {
        "suggested_model": "anthropic/claude-sonnet-4-5",
        "format": "openrouter:{model}",
    },
    "mistral": {"suggested_model": "mistral-large-latest", "format": "mistral:{model}"},
    "pydantic-ai-gateway": {"suggested_model": "gpt-4o", "format": "gateway/{model}"},
}


def run_init() -> None:
    """Run the wizard. Idempotent: re-runs add or update profiles."""
    ensure_user_workspace()
    existing = list_profiles()

    _print_intro(existing)
    backend_name = _pick_or_load_backend(existing)
    profile_name, profile = _build_profile_interactive(existing)
    _maybe_store_secrets(profile_name, profile, backend_name)

    set_as_default = (not existing) or Confirm.ask(
        f"\nSet [bold]{profile_name}[/bold] as the default profile?",
        default=not existing,
    )
    add_or_update_profile(profile_name, profile, set_default=set_as_default)
    _print_done(profile_name, set_as_default)


# ---------- steps ----------


def _print_intro(existing: dict[str, Profile]) -> None:
    if existing:
        console.print(
            Panel.fit(
                "[bold cyan]JAC[/bold cyan] — add or update a profile\n"
                f"[dim]existing:[/dim] {', '.join(existing)}",
                border_style="cyan",
            )
        )
    else:
        console.print(
            Panel.fit(
                "[bold cyan]JAC setup wizard[/bold cyan]\n\n"
                "Let's set up your first profile.\n"
                f"Config will be written to [bold]{paths.USER_CONFIG_FILE}[/bold].",
                border_style="cyan",
            )
        )


def _pick_or_load_backend(existing: dict[str, Profile]) -> SecretBackendName:
    """First run: ask which secrets backend to use. Subsequent runs: keep current."""
    from jac.config import get_settings, reset_settings_cache

    if existing:
        # Backend already chosen; just report.
        current = get_settings().secrets.backend
        console.print(f"[dim]secrets backend:[/dim] [bold]{current}[/bold]")
        return current

    backend_name = cast(
        SecretBackendName,
        Prompt.ask(
            "\n[bold]Where should JAC store API keys?[/bold]\n"
            "  [bold]keyring[/bold]  — OS keychain (recommended)\n"
            "  [bold]dotenv[/bold]   — ~/.jac/.env, plaintext, chmod 600\n"
            "  [bold]env-only[/bold] — JAC won't store; you'll manage via shell\n"
            "choice",
            choices=["keyring", "dotenv", "env-only"],
            default="keyring",
        ),
    )
    _write_backend_choice(backend_name)
    reset_settings_cache()
    return backend_name


def _build_profile_interactive(existing: dict[str, Profile]) -> tuple[str, Profile]:
    """Collect provider/model/name/env from the user; return ``(name, profile)``."""
    provider = Prompt.ask(
        "\n[bold]Provider[/bold]",
        choices=list(PROVIDER_CHOICES),
        default="anthropic",
    )
    pc = PROVIDER_CHOICES[provider]
    model_name = Prompt.ask(f"[bold]{provider}[/bold] model", default=pc["suggested_model"])
    model_id = pc["format"].format(model=model_name)

    # Profile name — validated, with friendly retry.
    suggested = (
        provider
        if provider not in existing
        else f"{provider}-{model_name.split('/')[-1].split(':')[0]}"
    )
    while True:
        candidate = Prompt.ask("[bold]Profile name[/bold]", default=suggested).strip().lower()
        try:
            validate_profile_name(candidate)
        except JacConfigError as exc:
            console.print(f"  [red]{exc}[/red]")
            continue
        if candidate in existing and not Confirm.ask(
            f"  [yellow]profile {candidate!r} exists — overwrite?[/yellow]",
            default=False,
        ):
            continue
        profile_name = candidate
        break

    # Non-secret env (Ollama base URL is the canonical example).
    profile_env: dict[str, str] = {}
    if provider == "ollama":
        existing_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        url = Prompt.ask("[bold]Ollama base URL[/bold]", default=existing_url)
        profile_env["OLLAMA_BASE_URL"] = url

    return profile_name, Profile(model=model_id, env=profile_env)


def _maybe_store_secrets(
    profile_name: str, profile: Profile, backend_name: SecretBackendName
) -> None:
    """For each required env var, optionally store it in the chosen backend."""
    required = profile.required_env_keys()
    if not required:
        return

    console.print()
    backend = get_backend(backend_name)

    for key in required:
        # Already stored?
        try:
            already = backend.get(key)
        except Exception:
            already = None
        if already:
            console.print(f"  [green]✓[/green] {key} already in [bold]{backend_name}[/bold]")
            continue

        env_value = os.environ.get(key)
        if env_value:
            if backend_name == "env-only":
                console.print(f"  [green]✓[/green] {key} present in environment")
                continue
            # Explicit prompt — never silent. The user might be on a machine
            # with employer credentials in env and want to keep them out of
            # their personal JAC store.
            if Confirm.ask(
                f"  [yellow]I see {key} in your environment.[/yellow] "
                f"Import into [bold]{backend_name}[/bold]?",
                default=True,
            ):
                backend.set(key, env_value)
                console.print(f"    [green]✓ imported {key}[/green]")
            else:
                console.print(f"    [dim]skipped — JAC will keep reading {key} from env[/dim]")
            continue

        # Not in env or backend — prompt or warn depending on backend.
        if backend_name == "env-only":
            console.print(
                f"  [yellow]![/yellow] {key} not set. "
                f"Export it in your shell before running [bold]jac[/bold]."
            )
            continue

        value = Prompt.ask(f"  Enter [bold]{key}[/bold]", password=True)
        if not value:
            console.print(f"    [dim]skipped {key}[/dim]")
            continue
        backend.set(key, value)
        console.print(f"    [green]✓ stored {key} in {backend_name}[/green]")


def _write_backend_choice(name: SecretBackendName) -> None:
    """Persist the chosen secrets backend in ~/.jac/config.yaml."""
    raw: dict[str, Any] = {}
    if paths.USER_CONFIG_FILE.is_file():
        text = paths.USER_CONFIG_FILE.read_text(encoding="utf-8")
        if text.strip():
            loaded = yaml.safe_load(text)
            if isinstance(loaded, dict):
                raw = loaded
    raw.setdefault("secrets", {})
    raw["secrets"]["backend"] = name
    body = yaml.safe_dump(raw, default_flow_style=False, sort_keys=False)
    header = (
        "# JAC user-level configuration.\n"
        '# See CLAUDE.md "Configuration & workspace" for the layering rules.\n\n'
    )
    paths.USER_CONFIG_FILE.write_text(header + body, encoding="utf-8")


def _print_done(profile_name: str, set_as_default: bool) -> None:
    lines = [
        "[bold green]Done![/bold green]",
        "",
        f"profile [bold]{profile_name}[/bold] saved to {paths.USER_CONFIG_FILE}.",
    ]
    if set_as_default:
        lines.append("set as default — [bold]jac[/bold] will use it.")
    else:
        lines.append(f"use it explicitly: [bold]jac --profile {profile_name}[/bold]")
    lines.append("")
    lines.append("Run [bold]jac init[/bold] again to add another profile.")
    lines.append("Run [bold]jac keys[/bold] to inspect stored credentials.")
    console.print(Panel.fit("\n".join(lines), border_style="green"))
