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
USER_SKILLS_DIR: Path = USER_WORKSPACE / "skills"
USER_HISTORY_FILE: Path = USER_WORKSPACE / "history"
USER_MCP_FILE: Path = USER_WORKSPACE / "mcp.json"
"""User-level MCP server catalog (Phase F, D28). Standard ``mcpServers`` JSON
shape (Claude Desktop / Cursor / MCP spec) so existing configs paste in
verbatim. JAC per-server knobs live in an optional sibling ``jac`` block —
see :mod:`jac.capabilities.mcp`."""

# --- Project workspace --------------------------------------------

PROJECT_WORKSPACE_DIRNAME = ".agents"
PROJECT_CONTEXT_FILENAME = "AGENTS.md"


def _is_project_marker(directory: Path) -> bool:
    """``True`` if ``directory`` looks like a JAC project root.

    Two markers, either suffices:

    - ``.git`` (file or dir — git worktrees use a file) — an obvious project.
    - ``.agents/`` directory — the explicit JAC opt-in. This is how a
      *non-git* folder declares itself a project: create ``.agents/`` (e.g.
      via ``jac init``) and JAC treats it as one.
    """
    return (directory / ".git").exists() or (directory / PROJECT_WORKSPACE_DIRNAME).is_dir()


@cache
def project_root(start: Path | None = None) -> Path | None:
    """Nearest ancestor that is a JAC project (``.git`` or ``.agents``).

    Returns ``None`` when neither marker is found at or above ``start``
    (default: CWD) — i.e. JAC is running "loose" in an unrelated folder.
    Callers that store project-scoped state consult this (via
    :func:`project_state_root`) so we never scatter ``.agents/`` into
    directories the user never opted in.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if _is_project_marker(candidate):
            return candidate
    return None


@cache
def find_project_root(start: Path | None = None) -> Path:
    """Working root for tool execution and relative-path resolution.

    The :func:`project_root` when one exists; otherwise the resolved
    ``start`` (default: CWD). The fallback matters for *tools* — ``run_shell``,
    ``grep``, and relative ``read_file`` paths must operate where the user
    actually is, even with no project. It does **not** govern where JAC
    writes its own state; that's :func:`project_state_root`.
    """
    return project_root(start) or (start or Path.cwd()).resolve()


def in_project(start: Path | None = None) -> bool:
    """``True`` iff a JAC project (``.git`` or ``.agents``) exists at/above ``start``.

    Used by scope-aware code paths (e.g. project-memory writes, project
    skill/prompt overlays) that must refuse to run outside a project rather
    than silently anchoring to whatever CWD happens to be.
    """
    return project_root(start) is not None


def project_workspace() -> Path:
    """``<project_root>/.agents`` — the overlay-resource root (config, memory,
    prompts, skills). In loose mode :func:`find_project_root` falls back to
    CWD, so these resolve under ``<cwd>/.agents`` and are simply skipped if
    absent (overlay readers tolerate missing files; project-memory *writes*
    refuse via :func:`in_project`). State writers must use
    :func:`project_state_root`, not this."""
    return find_project_root() / PROJECT_WORKSPACE_DIRNAME


def project_state_root() -> Path:
    """Directory under which JAC writes session-scoped state.

    Sessions, ``usage.jsonl``, the tool-result cache, and A2A state live
    here. In a project it's ``<project_root>/.agents`` (same as
    :func:`project_workspace`). When **loose** (no ``.git`` / ``.agents``)
    it's the user workspace ``~/.jac`` — so a quick ``jac`` in an unrelated
    folder persists globally instead of dropping ``.agents/`` next to
    whatever files happen to be there.
    """
    root = project_root()
    return root / PROJECT_WORKSPACE_DIRNAME if root is not None else USER_WORKSPACE


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
    """``<state_root>/usage.jsonl`` — per-turn token usage log (D25).

    One JSONL line is appended per completed agent turn:
    ``{session_id, ts, input_tokens, output_tokens}``. ``project_total_tokens``
    budgets sum across this file on startup; the running session's
    contributions accumulate live in :class:`jac.runtime.usage.UsageTracker`.
    Anchored to :func:`project_state_root` so loose runs log under ``~/.jac``.
    """
    return project_state_root() / "usage.jsonl"


def project_a2a_dir() -> Path:
    """``<project_root>/.agents/a2a/`` — A2A subsystem state (D24).

    Holds persisted task contexts, the inbound call audit log, and any
    other per-project A2A artifacts. Created lazily on first server start.
    Anchored to :func:`project_state_root` (loose runs serve under ``~/.jac``).
    """
    return project_state_root() / "a2a"


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


def project_skills_dir() -> Path:
    return project_workspace() / "skills"


def project_mcp_file() -> Path:
    """``<project_root>/.agents/mcp.json`` — project MCP server catalog (Phase F).

    Same standard ``mcpServers`` JSON shape as :data:`USER_MCP_FILE`. The
    MCP loader merges this over the user file **per server name** (project
    wins), mirroring the skill / prompt overlay precedence.
    """
    return project_workspace() / "mcp.json"


def mcp_log_dir() -> Path:
    """``<state_root>/cache/mcp/logs/`` — per-server stdio stderr logs.

    MCP stdio subprocesses get their stderr redirected here
    (``<name>.log``) instead of inheriting JAC's terminal. This keeps the
    REPL clean **and** prevents a misbehaving server (e.g. a Node-based one)
    from holding the controlling TTY and flipping it into raw mode mid-prompt.
    Anchored to :func:`project_state_root` so loose runs keep logs under
    ``~/.jac``.
    """
    return project_state_root() / "cache" / "mcp" / "logs"


def project_sessions_dir() -> Path:
    """``<state_root>/sessions`` — message-history persistence.

    Anchored to :func:`project_state_root` so loose runs persist under
    ``~/.jac/sessions`` (one global pool) instead of writing ``.agents/``
    into an unrelated folder.
    """
    return project_state_root() / "sessions"


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


def package_skills_dir() -> Path:
    """Shipped reference skills under ``jac/data/skills/``.

    Mirrors :func:`package_prompts_dir` for skills (Phase D). Each shipped
    skill lives at ``data/skills/<name>/SKILL.md`` with YAML frontmatter +
    markdown body, per the Anthropic community skill format. The user/
    project skill loader treats this directory as the lowest-priority
    source — user and project skills shadow shipped ones on name collision.
    """
    return package_data_dir() / "skills"


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
