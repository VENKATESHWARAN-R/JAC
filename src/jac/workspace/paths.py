"""Workspace path resolution.

**All** filesystem paths used by JAC are derived here. No path string should
appear hardcoded anywhere else in the codebase — see CLAUDE.md
"Fail-first, no hardcoding".

Layout:

- User workspace: ``~/.jac/`` (JAC-private)
- Project workspace: ``<project_root>/.agents/`` (community-neutral)
- Project context: ``<project_root>/AGENTS.md`` (auto-loaded)
- User context: ``~/.jac/AGENTS.md`` (auto-loaded)
- Package data: shipped under ``jac/data/`` in the installed package (defaults + provider catalog)
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from pathlib import Path

# --- User workspace -----------------------------------------------

USER_WORKSPACE: Path = Path.home() / ".jac"
USER_CONFIG_FILE: Path = USER_WORKSPACE / "config.yaml"
USER_PROVIDERS_FILE: Path = USER_WORKSPACE / "providers.yaml"
USER_PROVIDERS_EXAMPLE_FILE: Path = USER_WORKSPACE / "providers.yaml.example"
USER_CONTEXT_FILE: Path = USER_WORKSPACE / "AGENTS.md"
USER_MEMORY_FILE: Path = USER_WORKSPACE / "memory.md"
USER_PROMPTS_DIR: Path = USER_WORKSPACE / "prompts"
USER_MINIONS_DIR: Path = USER_WORKSPACE / "minions" / "templates"
USER_SKILLS_DIR: Path = USER_WORKSPACE / "skills"
USER_HISTORY_FILE: Path = USER_WORKSPACE / "history"

# --- Project workspace --------------------------------------------

PROJECT_WORKSPACE_DIRNAME = ".agents"
PROJECT_CONTEXT_FILENAME = "AGENTS.md"


@cache
def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: CWD) looking for a ``.git`` directory.

    Returns the directory containing ``.git`` as the project root. Falls back
    to ``start`` (resolved) if no ``.git`` is found in any ancestor.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return here


def is_in_project_repo(start: Path | None = None) -> bool:
    """``True`` iff a ``.git`` directory exists at or above ``start`` (default: CWD).

    Used by scope-aware code paths (e.g. project-memory writes) that need to
    refuse to run outside a tracked repo, rather than silently anchoring to
    whatever CWD happens to be.
    """
    here = (start or Path.cwd()).resolve()
    return any((candidate / ".git").exists() for candidate in (here, *here.parents))


def project_workspace() -> Path:
    return find_project_root() / PROJECT_WORKSPACE_DIRNAME


def project_config_file() -> Path:
    return project_workspace() / "config.yaml"


def project_context_file() -> Path:
    """``<project_root>/AGENTS.md`` — at repo root, NOT inside ``.agents/``."""
    return find_project_root() / PROJECT_CONTEXT_FILENAME


def project_memory_file() -> Path:
    """``<project_root>/.agents/memory.md`` — JAC-managed project memory.

    Distinct from ``AGENTS.md``: this file is written by Gru via the
    ``remember`` tool, with HITL approval. We never mutate ``AGENTS.md``.
    """
    return project_workspace() / "memory.md"


def project_usage_file() -> Path:
    """``<project_root>/.agents/usage.jsonl`` — per-turn token usage log (D25).

    One JSONL line is appended per completed agent turn:
    ``{session_id, ts, input_tokens, output_tokens}``. ``project_total_tokens``
    budgets sum across this file on startup; the running session's
    contributions accumulate live in :class:`jac.runtime.usage.UsageTracker`.
    """
    return project_workspace() / "usage.jsonl"


def project_a2a_dir() -> Path:
    """``<project_root>/.agents/a2a/`` — A2A subsystem state (D24).

    Holds persisted task contexts, the inbound call audit log, and any
    other per-project A2A artifacts. Created lazily on first server start.
    """
    return project_workspace() / "a2a"


def project_a2a_contexts_dir() -> Path:
    """``<project_root>/.agents/a2a/contexts/`` — one JSON file per A2A ``context_id``.

    Each file mirrors fasta2a's ``Storage.update_context`` payload (the
    pydantic-ai ``message_history`` for that conversation thread). Default
    retention is 3 days; see ``a2a.context_retention_days`` in profile config.
    """
    return project_a2a_dir() / "contexts"


def project_a2a_inbound_log() -> Path:
    """``<project_root>/.agents/a2a/inbound.jsonl`` — audit log of inbound A2A calls (D24).

    One JSONL line per call: ``{ts, peer_id, context_id, task_id, state,
    duration_ms, tokens_used, message_preview}``. Never rotated by us — the
    user owns retention via ``a2a.context_retention_days`` (which prunes
    *context* files; the audit log stays as a long-term ledger).
    """
    return project_a2a_dir() / "inbound.jsonl"


def project_a2a_inbound_files_dir() -> Path:
    """``<project_root>/.agents/a2a/inbound-files/`` — files received from A2A peers (Phase 4.d.3).

    When ``a2a_call`` receives a task whose ``artifacts`` or ``history``
    contain ``FilePart`` entries with inline bytes, JAC decodes the
    bytes and writes them here under ``<task_id>/<filename>``. The
    returned task dict carries the saved paths in ``_jac_saved_files``
    so the calling Gru can read / display them without ever pulling
    the binary into its context window.

    Lazy-created on first save. Not pruned by the retention loop today
    (that one targets the contexts dir only); operator owns cleanup
    until a use case demands automation.
    """
    return project_a2a_dir() / "inbound-files"


def project_a2a_guest_uploads_dir() -> Path:
    """``<project_root>/.agents/a2a/guest-uploads/`` — files received by the guest server (Phase 4.d.4).

    When a peer POSTs a ``message/send`` carrying ``FilePart`` entries
    with inline bytes, the auditing worker decodes and saves them under
    ``<context_id>/<filename>`` so the guest Gru's path-based tools
    (``read_file``, ``grep``, ``glob``) can operate on them.

    Per-context (not per-task) so a multi-turn conversation re-uses
    the same file state. Sibling of :func:`project_a2a_contexts_dir`
    rather than nested inside it so JSON storage and file uploads
    stay structurally separate.

    Cleanup follows the same retention window as contexts. Lazy-
    created on first save.
    """
    return project_a2a_dir() / "guest-uploads"


def project_prompts_dir() -> Path:
    return project_workspace() / "prompts"


def project_minions_dir() -> Path:
    return project_workspace() / "minions" / "templates"


def project_skills_dir() -> Path:
    return project_workspace() / "skills"


def project_sessions_dir() -> Path:
    return project_workspace() / "sessions"


def resolve_under_project(path: str | Path) -> Path:
    """Resolve ``path`` to an absolute path.

    Absolute paths are returned as-is. Relative paths are anchored to the
    project root (``<repo>/``), so an agent can say ``"src/foo.py"`` without
    needing to know the absolute path or even the current working directory.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    return find_project_root() / p


# --- Package data (shipped defaults) ------------------------------


@cache
def package_root() -> Path:
    """Filesystem path to the installed JAC package directory."""
    return Path(str(files("jac")))


def package_data_dir() -> Path:
    """Shipped YAML defaults and provider catalog (not Python modules)."""
    return package_root() / "data"


def package_defaults_file() -> Path:
    return package_data_dir() / "defaults.yaml"


def package_providers_file() -> Path:
    return package_data_dir() / "providers.yaml"


def package_prompts_dir() -> Path:
    return package_root() / "prompts"


def package_minions_dir() -> Path:
    return package_root() / "minions" / "templates"


# --- Layered prompt loader ----------------------------------------


def load_prompt(name: str) -> str:
    """Return the prompt body for ``name`` (no extension), first hit wins.

    Resolution order: project ``.agents/prompts/`` → user ``~/.jac/prompts/``
    → shipped package defaults. Raises ``FileNotFoundError`` if the package
    default is missing (a packaging bug — every name should ship a default).
    """
    candidates = [
        project_prompts_dir() / f"{name}.md",
        USER_PROMPTS_DIR / f"{name}.md",
        package_prompts_dir() / f"{name}.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"Prompt '{name}.md' not found in project, user, or package locations. "
        "This is a packaging bug — the shipped default appears to be missing."
    )
