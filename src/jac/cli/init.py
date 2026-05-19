"""``jac init`` — interactive onboarding wizard.

Walks the user through provider selection, model choice, and writes
``~/.jac/config.yaml``. Also ensures the user workspace skeleton exists.

Intentionally minimal in Phase 0.5: provider + model + a courtesy API-key
presence check. Tier-based routing, project-workspace setup, and richer
preferences are later additions (see ``PROGRESS.md`` v2).
"""

from __future__ import annotations

import os

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from jac.workspace import paths
from jac.workspace.bootstrap import ensure_user_workspace

console = Console()


PROVIDERS: dict[str, dict[str, str | None]] = {
    "anthropic": {
        "env_key": "ANTHROPIC_API_KEY",
        "suggested_model": "claude-sonnet-4-5",
        "format": "anthropic:{model}",
    },
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "suggested_model": "gpt-5",
        "format": "openai:{model}",
    },
    "google": {
        "env_key": "GEMINI_API_KEY",
        "suggested_model": "gemini-2.5-flash",
        "format": "google-gla:{model}",
    },
    "ollama": {
        "env_key": None,
        "suggested_model": "gemma3:e2b",
        "format": "ollama:{model}",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "suggested_model": "anthropic/claude-sonnet-4-5",
        "format": "openrouter:{model}",
    },
    "mistral": {
        "env_key": "MISTRAL_API_KEY",
        "suggested_model": "mistral-large-latest",
        "format": "mistral:{model}",
    },
    "pydantic-ai-gateway": {
        "env_key": "PYDANTIC_AI_GATEWAY_API_KEY",
        "suggested_model": "gpt-4o",
        "format": "gateway/{model}",
    },
}


def run_init() -> None:
    """Run the interactive setup wizard."""
    console.print(
        Panel.fit(
            "[bold cyan]JAC setup wizard[/bold cyan]\n\n"
            "I'll walk you through choosing a provider and model, then write\n"
            f"your config to [bold]{paths.USER_CONFIG_FILE}[/bold].",
            border_style="cyan",
        )
    )

    # Step 1: ensure workspace skeleton
    if ensure_user_workspace():
        console.print(f"[green]✓[/green] created skeleton at [bold]{paths.USER_WORKSPACE}[/bold]")
    else:
        console.print(f"[dim]workspace already exists at {paths.USER_WORKSPACE}[/dim]")

    # Step 2: confirm overwrite if a non-template config already exists
    if _config_has_user_content():
        if not Confirm.ask(
            f"\n[yellow]warning:[/yellow] {paths.USER_CONFIG_FILE} already has content. Overwrite?",
            default=False,
        ):
            console.print("[dim]aborted[/dim]")
            return

    # Step 3: provider
    provider = Prompt.ask(
        "\nWhich [bold]provider[/bold]?",
        choices=list(PROVIDERS.keys()),
        default="anthropic",
    )
    p = PROVIDERS[provider]

    # Step 4: model
    suggested = p["suggested_model"]
    assert suggested is not None  # all entries have one
    model_name = Prompt.ask(
        f"Which {provider} [bold]model[/bold]?",
        default=suggested,
    )
    fmt = p["format"]
    assert fmt is not None
    model_id = fmt.format(model=model_name)

    # Step 5: API-key presence check (informational only — don't block)
    env_key = p["env_key"]
    if env_key:
        if os.environ.get(env_key):
            console.print(f"[green]✓[/green] {env_key} found in environment")
        else:
            console.print(
                f"[yellow]![/yellow] {env_key} is [bold]not[/bold] set. "
                f"Add it to your shell or [bold].env[/bold] file before running [bold]jac[/bold]."
            )
    elif provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL")
        if base_url:
            console.print(f"[green]✓[/green] OLLAMA_BASE_URL = {base_url}")
        else:
            console.print(
                "[yellow]![/yellow] OLLAMA_BASE_URL is not set. "
                "JAC will try the default ([bold]http://localhost:11434/v1[/bold]). "
                "Set the env var if your Ollama lives elsewhere."
            )
            os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434/v1"

    # Step 6: preview + confirm + write
    config_data = {"model": model_id}
    yaml_body = yaml.safe_dump(config_data, default_flow_style=False, sort_keys=False)
    payload = (
        "# JAC user-level configuration. Written by `jac init`.\n"
        '# See CLAUDE.md "Configuration & workspace" for the layering rules.\n\n'
        f"{yaml_body}"
    )

    console.print("\n[bold]Will write:[/bold]")
    console.print(Panel(payload.rstrip(), border_style="dim"))

    if not Confirm.ask(f"Write to {paths.USER_CONFIG_FILE}?", default=True):
        console.print("[dim]aborted (no file written)[/dim]")
        return

    paths.USER_CONFIG_FILE.write_text(payload, encoding="utf-8")
    console.print(f"[green]✓[/green] wrote [bold]{paths.USER_CONFIG_FILE}[/bold]")

    # Step 7: next steps
    next_steps_lines = [
        "[bold green]Done![/bold green]",
        "",
        "Next steps:",
    ]
    if env_key:
        next_steps_lines.append(f"  1. Make sure [bold]{env_key}[/bold] is set in your environment")
        next_steps_lines.append("  2. Run [bold]jac[/bold] to start a session")
    else:
        next_steps_lines.append("  1. Run [bold]jac[/bold] to start a session")
    next_steps_lines.append(
        "  3. Optional: add an [bold]AGENTS.md[/bold] at your repo root for project context"
    )

    console.print(Panel.fit("\n".join(next_steps_lines), border_style="green"))


def _config_has_user_content() -> bool:
    """True if ``~/.jac/config.yaml`` has anything beyond the template comment header."""
    if not paths.USER_CONFIG_FILE.exists():
        return False
    for line in paths.USER_CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False
