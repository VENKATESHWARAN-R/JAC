"""Project + user memory — the ``remember`` and ``forget`` tools.

Gru maintains two JAC-owned memory files, mirroring the 2x2 we already
have with AGENTS.md:

|                        | User scope            | Project scope                    |
| ---------------------- | --------------------- | -------------------------------- |
| User-authored (we read) | ``~/.jac/AGENTS.md``  | ``<repo>/AGENTS.md``             |
| JAC-managed (we write)  | ``~/.jac/memory.md``  | ``<repo>/.agents/memory.md``     |

Gru picks ``scope`` explicitly on every call — ``"user"`` for facts that
apply across every project (preferences, cross-cutting conventions) and
``"project"`` for facts that are about *this* repo specifically. There is
no default; the tool forces the model to make the call. ``scope="project"``
outside a git repo is a hard error (we don't silently scribble into a
random CWD).

Design decisions (docs/architecture.md §11 D14):

- **JAC-owned files, not ``AGENTS.md``.** We never mutate user-authored
  context files. ``memory.md`` is a separate file at both scopes.
- **Fixed category enum.** Five categories — convention / fact /
  preference / gotcha / decision — give the file a predictable shape and
  let de-dup work cleanly.
- **HITL on every call.** ``remember`` and ``forget`` both mutate a
  tracked file; the user approves each call through the standard
  approval flow.
- **De-dup with loud feedback.** Exact-normalized matches against the
  target section are rejected and the existing line is reported back to
  Gru, so the model can choose a more specific phrasing if it really
  meant something distinct.
- **Audit trail.** Each entry carries an HTML-comment timestamp and the
  originating session id (when available).
- **Atomic writes.** Write to a sibling tempfile, then rename, so a crash
  mid-write can't corrupt memory.md.
- **Soft size warning.** Once a section crosses ``_SECTION_SIZE_WARN``
  entries, ``remember`` tacks a "consider pruning" hint onto its return
  value — surfaced to Gru and to the user, no automation kicks in.
- **No silent scope fallback.** Project scope outside a git repo raises
  with an actionable error; the model must rephrase as user scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition

from jac.errors import JacConfigError
from jac.tools import jac_function_toolset, jac_tool
from jac.workspace import paths
from jac.workspace.session_ctx import get_current_session_id

MemoryCategory = Literal["convention", "fact", "preference", "gotcha", "decision"]
MemoryScope = Literal["user", "project"]

# Section title shown in memory.md for each category. Order here also defines
# the order sections appear in the bootstrapped template.
_SECTIONS: dict[MemoryCategory, str] = {
    "convention": "Conventions",
    "fact": "Facts",
    "preference": "Preferences",
    "gotcha": "Gotchas",
    "decision": "Decisions",
}

_HEADER = "<!-- jac:memory schema=1 -->"

_TEMPLATES: dict[MemoryScope, str] = {
    "user": (
        f"{_HEADER}\n"
        "# User memory\n"
        "\n"
        "_Maintained by JAC across every project. Edit freely — JAC will "
        "preserve manual edits and append below. Written via the `remember` "
        'tool with `scope="user"` (HITL-gated)._\n'
        + "".join(f"\n## {title}\n" for title in _SECTIONS.values())
    ),
    "project": (
        f"{_HEADER}\n"
        "# Project memory\n"
        "\n"
        "_Maintained by JAC for this repository. Edit freely — JAC will "
        "preserve manual edits and append below. Written via the `remember` "
        'tool with `scope="project"` (HITL-gated)._\n'
        + "".join(f"\n## {title}\n" for title in _SECTIONS.values())
    ),
}

_SECTION_SIZE_WARN = 25  # surfaces a soft "consider pruning" hint past this


# ---------- public surface ----------------------------------------------


@dataclass
class MemoryCapability(AbstractCapability[Any]):
    """Project + user memory toolset. ``remember`` and ``forget`` are HITL-gated."""

    def get_toolset(self) -> Any:
        toolset = jac_function_toolset(remember, forget)
        return toolset.approval_required(_needs_approval)


@jac_tool
def remember(
    reason: str,
    content: str,
    category: MemoryCategory,
    scope: MemoryScope,
) -> str:
    """Persist a durable fact to memory.md (HITL-gated).

    Use this only for **durable** facts that future sessions will benefit
    from knowing — conventions, structural facts, expressed preferences,
    gotchas, or design decisions. Do not use it for ephemeral observations
    or chat turns.

    Categories:
        - ``convention``  — how things are done (e.g. "uses uv, not pip")
        - ``fact``        — structural truths (e.g. "tests live in tests/")
        - ``preference``  — the user's stated preferences
        - ``gotcha``      — non-obvious traps a future session should know
        - ``decision``    — design decisions and their rationale

    Scopes:
        - ``user``    — applies across every project. Stored in
          ``~/.jac/memory.md``. Most ``preference`` entries go here.
        - ``project`` — applies to *this* repo only. Stored in
          ``<repo>/.agents/memory.md``. Most ``convention`` / ``gotcha`` /
          ``decision`` entries go here. Fails fast outside a git repo.

    Args:
        reason: One-sentence justification for this call. Shown in the
            approval prompt and audit trail.
        content: The durable fact, as a single sentence. The user sees this
            verbatim in the approval prompt and in ``memory.md``.
        category: Which section to file the fact under.
        scope: Which memory file — user-level or project-level.

    Returns:
        Confirmation string. If the fact already exists in the target
        section it is **not** re-added and the existing line is reported
        back — try a more specific phrasing if you meant something
        distinct. If the section is large the return also carries a soft
        "consider pruning" hint.
    """
    _validate_category(category)
    content_stripped = _validate_content(content)
    section_title = _SECTIONS[category]
    path = ensure_memory_file(scope)
    text = path.read_text(encoding="utf-8")

    section_body = _extract_section(text, section_title)
    if section_body is None:
        raise ValueError(
            f"section `## {section_title}` not found in {path}. "
            "Either restore the heading manually, or delete the file to have "
            "JAC recreate it from the template."
        )

    duplicate = _find_duplicate(section_body, content_stripped)
    if duplicate is not None:
        return (
            f"already recorded under {section_title} ({scope} scope): "
            f"{duplicate!r}. Skipped to avoid noise — rephrase more "
            "specifically if you meant something distinct."
        )

    bullet = _format_bullet(content_stripped)
    new_text = _insert_into_section(text, section_title, bullet)
    _atomic_write(path, new_text)

    size_after = _count_bullets(_extract_section(new_text, section_title) or "")
    msg = f"stored under {section_title} ({scope} scope): {content_stripped}"
    if size_after > _SECTION_SIZE_WARN:
        msg += (
            f" — note: section now has {size_after} entries; consider "
            "`forget` or manual pruning of stale lines."
        )
    return msg


@jac_tool
def forget(reason: str, content: str, scope: MemoryScope) -> str:
    """Remove a previously-stored memory entry (HITL-gated).

    Exact-normalized match against the bullet text (ignoring the audit
    comment). Errors if no match is found, or if more than one matches —
    add specifics to the ``content`` to disambiguate.

    Args:
        reason: One-sentence justification for this call (e.g. "the
            convention was reversed in the last refactor"). Shown in the
            approval prompt.
        content: The text of the entry to remove. Must match an existing
            bullet exactly after whitespace + case normalization.
        scope: Which memory file to remove from.

    Returns:
        Confirmation string naming the section and the removed line.
    """
    content_stripped = _validate_content(content)
    path = _memory_path_for_scope(scope)
    if not path.is_file():
        raise JacConfigError(
            f"no memory file at {path}; nothing to forget. Use `remember` "
            "first, or check the scope."
        )
    text = path.read_text(encoding="utf-8")

    hits = _find_all_matches(text, content_stripped)
    if not hits:
        raise ValueError(
            f"no entry matching {content!r} in {path}. Use the exact "
            "phrasing of the bullet (case- and whitespace-insensitive)."
        )
    if len(hits) > 1:
        sections = ", ".join(sorted({h[0] for h in hits}))
        raise ValueError(
            f"{len(hits)} entries match {content!r} (in: {sections}). "
            "Use a more specific `content` so the match is unique."
        )

    section_title, line_index, bullet_text = hits[0]
    new_text = _remove_line(text, line_index)
    _atomic_write(path, new_text)
    return f"removed from {section_title} ({scope} scope): {bullet_text}"


def ensure_memory_file(scope: MemoryScope) -> Path:
    """Return the memory.md path for ``scope``, creating from template if missing."""
    path = _memory_path_for_scope(scope)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_TEMPLATES[scope], encoding="utf-8")
    return path


# ---------- internals ---------------------------------------------------


def _validate_category(category: str) -> None:
    if category not in _SECTIONS:
        raise ValueError(
            f"unknown category {category!r}; must be one of {list(get_args(MemoryCategory))}"
        )


def _validate_content(content: str) -> str:
    content_stripped = content.strip()
    if not content_stripped:
        raise ValueError("`content` must not be empty.")
    if "\n" in content_stripped:
        raise ValueError("`content` must be a single line — memory entries are one-sentence facts.")
    return content_stripped


def _memory_path_for_scope(scope: MemoryScope) -> Path:
    if scope == "user":
        return paths.USER_MEMORY_FILE
    if scope == "project":
        if not paths.is_in_project_repo():
            raise JacConfigError(
                'scope="project" requires a git repository; none found at or '
                "above the current directory. Either `cd` into a tracked repo, "
                'or use scope="user" for cross-project facts.'
            )
        return paths.project_memory_file()
    raise ValueError(f"unknown scope {scope!r}; must be one of {list(get_args(MemoryScope))}")


def _needs_approval(ctx: Any, tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    # Every remember/forget call mutates a tracked file — always HITL.
    return True


def _format_bullet(content: str) -> str:
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S")
    session_id = get_current_session_id()
    if session_id:
        meta = f"<!-- jac: {timestamp} session: {session_id} -->"
    else:
        meta = f"<!-- jac: {timestamp} -->"
    return f"- {content} {meta}"


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def _strip_bullet_metadata(bullet_line: str) -> str:
    """Return the prose part of a memory bullet (drop ``- `` prefix + comment)."""
    text = bullet_line.strip()
    if text.startswith("- "):
        text = text[2:]
    idx = text.find("<!--")
    if idx >= 0:
        text = text[:idx]
    return text.strip()


def _count_bullets(section_body: str) -> int:
    return sum(1 for raw in section_body.splitlines() if raw.strip().startswith("- "))


def _extract_section(text: str, section_title: str) -> str | None:
    """Return the body of ``## section_title`` (heading exclusive). ``None`` if absent."""
    lines = text.splitlines()
    start: int | None = None
    end: int = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            if stripped == f"## {section_title}":
                start = i + 1
            continue
        if stripped.startswith("## "):
            end = i
            break
    if start is None:
        return None
    return "\n".join(lines[start:end])


def _find_duplicate(section_body: str, content: str) -> str | None:
    """Return the existing line if ``content`` is already represented; else ``None``.

    Exact-normalized match only. Substring overlap is *not* treated as a
    duplicate — too aggressive (e.g. "uses uv" would shadow "uses uvicorn").
    """
    needle = _normalize(content)
    for raw in section_body.splitlines():
        if not raw.strip().startswith("- "):
            continue
        existing = _strip_bullet_metadata(raw)
        if _normalize(existing) == needle:
            return existing
    return None


def _find_all_matches(text: str, content: str) -> list[tuple[str, int, str]]:
    """Find every bullet matching ``content`` across every section.

    Returns a list of ``(section_title, line_index, bullet_prose)`` triples.
    Used by :func:`forget` to disambiguate hits.
    """
    needle = _normalize(content)
    lines = text.splitlines()
    out: list[tuple[str, int, str]] = []
    current_section: str | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
            continue
        if current_section is None or not stripped.startswith("- "):
            continue
        existing = _strip_bullet_metadata(line)
        if _normalize(existing) == needle:
            out.append((current_section, i, existing))
    return out


def _insert_into_section(text: str, section_title: str, bullet: str) -> str:
    """Insert ``bullet`` at the end of the ``## section_title`` block."""
    lines = text.splitlines()
    start: int | None = None
    end: int = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            if stripped == f"## {section_title}":
                start = i + 1
            continue
        if stripped.startswith("## "):
            end = i
            break
    if start is None:
        raise ValueError(f"section `## {section_title}` not found")

    # Insert after the last non-blank line of the section.
    insert_at = end
    while insert_at > start and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    new_lines = [*lines[:insert_at], bullet, *lines[insert_at:]]
    out = "\n".join(new_lines)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _remove_line(text: str, line_index: int) -> str:
    """Remove the single line at ``line_index`` from ``text``."""
    lines = text.splitlines()
    if not (0 <= line_index < len(lines)):
        raise IndexError(f"line {line_index} out of range")
    new_lines = lines[:line_index] + lines[line_index + 1 :]
    out = "\n".join(new_lines)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _atomic_write(path: Path, content: str) -> None:
    """Write atomically via tempfile + rename in the same directory."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
