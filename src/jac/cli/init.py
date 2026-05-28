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
from jac.profiles import Profile, validate_profile_name
from jac.profiles_crud import add_or_update_profile, list_profiles
from jac.profiles_io import detect_old_profiles, migrate_old_profiles
from jac.providers.registry import get_provider_registry
from jac.secrets import SecretBackendName, get_backend
from jac.workspace import paths
from jac.workspace.bootstrap import ensure_user_workspace

console = Console()


def run_init() -> None:
    """Run the wizard. Idempotent: re-runs add or update profiles."""
    ensure_user_workspace()
    _run_pending_migrations()
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


def _run_pending_migrations() -> None:
    """Run any config-shape migrations the workspace still needs.

    Add a new migration here when (and only when) a release changes the
    **shape** of user config — field renames, removals, or new required
    fields. Default-value flips do NOT need a migration: pydantic-settings
    merges YAML sources field-level, so any key absent from the user's
    ``~/.jac/config.yaml`` falls through to ``defaults.yaml`` and picks
    up the new value automatically on upgrade. See CLAUDE.md "Changing
    config schema" for the full rule.

    Each migration must be idempotent (safe to run on every ``jac init``)
    and should surface a clear panel explaining what's about to change
    before touching files.
    """
    _migrate_pre_d22_profiles()
    # Add future migrations below, oldest → newest.


def _migrate_pre_d22_profiles() -> None:
    """Detect pre-D22 ``model:`` profiles and offer to auto-rewrite as ``tiers:``."""
    old = detect_old_profiles()
    if not old:
        return

    listed = ", ".join(f"[bold]{n}[/bold]" for n in old)
    console.print(
        Panel.fit(
            "[bold yellow]Old profile schema detected[/bold yellow]\n\n"
            f"Profile(s) {listed} use the pre-D22 [bold]model:[/bold] field.\n"
            "JAC's new schema groups models into [bold]tiers[/bold] "
            "(small / medium / large) — see CLAUDE.md.\n\n"
            "Auto-migration wraps each [bold]model: X[/bold] as "
            "[bold]tiers: {medium: [X]}, active_tier: medium[/bold].\n"
            "Your env: and requires_env: fields are preserved.",
            border_style="yellow",
        )
    )
    if not Confirm.ask("Migrate now?", default=True):
        raise JacConfigError(
            "old-schema profiles not migrated. Re-run `jac init` or hand-edit "
            f"{paths.USER_CONFIG_FILE} to add tiers/active_tier."
        )

    migrated = migrate_old_profiles()
    console.print(f"[green]✓[/green] migrated {len(migrated)} profile(s): {', '.join(migrated)}\n")


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

    registry = get_provider_registry()
    if existing:
        current = get_settings().secrets.backend
        console.print(f"[dim]secrets backend:[/dim] [bold]{current}[/bold]")
        return current

    default_backend = cast(
        SecretBackendName,
        registry.init.default_secrets_backend,
    )
    backend_name = cast(
        SecretBackendName,
        Prompt.ask(
            "\n[bold]Where should JAC store API keys?[/bold]\n"
            "  [bold]keyring[/bold]  — OS keychain (recommended)\n"
            "  [bold]dotenv[/bold]   — ~/.jac/.env, plaintext, chmod 600\n"
            "  [bold]env-only[/bold] — JAC won't store; you'll manage via shell\n"
            "choice",
            choices=["keyring", "dotenv", "env-only"],
            default=default_backend,
        ),
    )
    _write_backend_choice(backend_name)
    reset_settings_cache()
    return backend_name


def _build_profile_interactive(existing: dict[str, Profile]) -> tuple[str, Profile]:
    """Collect provider/model/name/env from the user; return ``(name, profile)``."""
    registry = get_provider_registry()
    wizard_entries = registry.wizard_providers()
    if not wizard_entries:
        raise JacConfigError(
            "no providers with wizard metadata in the catalog. "
            f"Check {paths.package_providers_file()}."
        )

    provider_ids = [entry[0] for entry in wizard_entries]
    default_provider = registry.init.default_wizard_provider
    if default_provider not in provider_ids:
        default_provider = provider_ids[0]

    provider = Prompt.ask(
        "\n[bold]Provider[/bold]",
        choices=provider_ids,
        default=default_provider,
    )
    _provider_id, spec, wizard = next(e for e in wizard_entries if e[0] == provider)

    model_name = Prompt.ask(
        f"[bold]{wizard.label}[/bold] model "
        "[dim](this becomes your [bold]medium[/bold] tier)[/dim]",
        default=wizard.suggested_model,
    )
    medium_model = wizard.model_format.format(model=model_name)

    tiers: dict[str, list[str]] = {"medium": [medium_model]}
    if Confirm.ask(
        "\n[dim]Optional: add [bold]small[/bold] and [bold]large[/bold] tiers now? "
        "(Used by `/model TIER` and future cost-aware compaction.)[/dim]",
        default=False,
    ):
        small = Prompt.ask(
            "[bold]Small[/bold] tier model "
            "[dim](full id, e.g. anthropic:claude-haiku-4-5 — blank to skip)[/dim]",
            default="",
        ).strip()
        if small:
            tiers["small"] = [small]
        large = Prompt.ask(
            "[bold]Large[/bold] tier model "
            "[dim](full id, e.g. anthropic:claude-opus-4-7 — blank to skip)[/dim]",
            default="",
        ).strip()
        if large:
            tiers["large"] = [large]

    suggested = (
        provider
        if provider not in existing
        else f"{provider}-{model_name.split('/')[-1].split(':')[0]}"
    )
    while True:
        candidate = Prompt.ask("\n[bold]Profile name[/bold]", default=suggested).strip().lower()
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

    profile_env: dict[str, str] = {}
    for env_key in spec.profile_env_keys:
        default_val = registry.init.env_defaults.get(
            env_key,
            os.environ.get(env_key, ""),
        )
        if provider == "ollama" and env_key == "OLLAMA_BASE_URL" and not default_val:
            default_val = "http://localhost:11434/v1"
        label = env_key.replace("_", " ").title()
        value = Prompt.ask(f"[bold]{label}[/bold]", default=default_val or "")
        if value:
            profile_env[env_key] = value

    return profile_name, Profile(tiers=tiers, active_tier="medium", env=profile_env)


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
