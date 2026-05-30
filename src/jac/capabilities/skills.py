"""Community-format skill loader (Phase D, D21).

Skills are **loadable prompts / playbooks** that Gru can read on demand,
not a runtime mode and not a new tool surface. They live as
``<dir>/<name>/SKILL.md`` files with a YAML frontmatter block + a
free-form markdown body. The capability does three things:

1. **Discover.** Walk three locations — project, user, package — and
   parse every ``SKILL.md`` it finds. Project shadows user shadows
   package on name collision so a repo can override a shipped recipe.
2. **Advertise.** Inject the loaded skills' ``name`` + ``description``
   into Gru's system prompt via :meth:`get_instructions`, so the model
   knows what's available without having to discover it. A 2 KB cap
   prevents skills from crowding out the rest of the prompt — past the
   cap we fall back to name-only listings plus a "/skill list" hint.
3. **Serve.** Expose a single :func:`load_skill` tool the model calls to
   pull a skill's body into the next turn. The body comes back as the
   tool result — the LLM sees it as a tool message, exactly like any
   other tool return.

Design references:

- ``docs/design/cost-efficient-orchestration.md`` §6 (Phase D spec).
- ``docs/architecture.md`` §11 D21 (community skill format decision).
- The ``reason: str`` discipline (architecture §6a) — :func:`load_skill`
  carries it like every other JAC tool.

The capability deliberately does **not** gate by ``tools_required``. If a
skill declares a required tool that isn't loaded, ``/skill list`` shows a
note next to the entry, but the model can still load the skill body —
the body is advice, not execution. Hard gating is an anti-pattern when
skills are prose.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai.capabilities import AbstractCapability

from jac.tools import jac_function_toolset, jac_tool
from jac.workspace import paths

logger = logging.getLogger(__name__)

# --- constants ---------------------------------------------------------

SkillSource = Literal["project", "user", "package"]
"""Where a loaded skill came from. Lower-priority sources are shadowed
on name collision; see :func:`load_all_skills` for the resolution order."""

_INSTRUCTIONS_CAP_BYTES = 2048
"""Soft ceiling on the skill block injected into the system prompt.

Anything above this falls back to a name-only listing — the full
descriptions are still available via ``/skill list``. Keeps the prefix
small enough that prompt caching stays effective when users install many
skills."""

_DESCRIPTION_SOFT_CAP_CHARS = 200
"""Per-skill description length above which the loader emits a warning.

Not a hard truncation — long descriptions just eat into the 2 KB total
faster. A warning is enough nudge during skill authoring."""

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<frontmatter>.*?)\n---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)
"""Match the leading ``---`` ... ``---`` YAML block + the rest as body."""

_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
"""Skill names are lowercase alphanumeric + hyphens. Same convention as
slash command names so they read consistently."""


# --- models ------------------------------------------------------------


class SkillFrontmatter(BaseModel):
    """Validated YAML frontmatter for a SKILL.md file.

    ``tools_required`` is **informational** — the loader never hides a
    skill based on missing tools (skills are advice, not execution).
    It surfaces in ``/skill list`` as a note so the user can spot
    skills that reference unavailable tools.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "Slug used to load the skill (lowercase alphanumeric + hyphens). "
            "Must match the containing folder name."
        ),
    )
    description: str = Field(
        description=(
            "One-line summary of when to use this skill. Shown in the "
            "system prompt so the model can decide whether to load the body."
        ),
    )
    tools_required: list[str] = Field(
        default_factory=list,
        description=(
            "Informational only — names of tools the skill mentions. The "
            "loader does not gate on this; ``/skill list`` annotates "
            "entries whose required tools aren't currently available."
        ),
    )


@dataclass(frozen=True)
class LoadedSkill:
    """A parsed SKILL.md ready for advertisement + on-demand loading."""

    frontmatter: SkillFrontmatter
    body: str
    source: SkillSource
    path: Path

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description


@dataclass(frozen=True)
class SkillCatalog:
    """Outcome of a discovery pass over the three skill locations.

    ``active`` is the dict the rest of JAC uses — one entry per name,
    with the highest-priority source winning on collisions. ``shadowed``
    is every parseable skill that *would* have loaded if not for a
    higher-priority namesake. Surfacing the shadowed list lets the user
    see in ``/skill list`` exactly what got overridden, instead of the
    override being silent.
    """

    active: dict[str, LoadedSkill]
    shadowed: list[LoadedSkill]


# --- loader ------------------------------------------------------------


def _candidate_dirs() -> list[tuple[Path, SkillSource]]:
    """The three skill locations in resolution order — highest priority first.

    Project (``<repo>/.agents/skills``) shadows user (``~/.jac/skills``)
    shadows package (``<package>/data/skills``). We probe project only when
    we're actually inside a project (``.git`` or ``.agents/``), mirroring
    the rest of JAC's "fail loud outside a project" stance for
    project-scoped state.
    """
    dirs: list[tuple[Path, SkillSource]] = []
    if paths.in_project():
        dirs.append((paths.project_skills_dir(), "project"))
    dirs.append((paths.USER_SKILLS_DIR, "user"))
    dirs.append((paths.package_skills_dir(), "package"))
    return dirs


def _parse_skill_file(path: Path, source: SkillSource) -> LoadedSkill | None:
    """Parse a ``SKILL.md`` into a :class:`LoadedSkill`, or ``None`` on error.

    Errors are logged at WARNING and skipped — a single malformed skill
    must not break Gru's startup. The user sees the warning in the JAC
    log; ``/skill list`` simply omits the broken entry.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("skipping unreadable skill %s: %s", path, exc)
        return None

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        logger.warning(
            "skipping skill %s: missing YAML frontmatter (expected leading `---` block)",
            path,
        )
        return None

    try:
        data = yaml.safe_load(match.group("frontmatter")) or {}
    except yaml.YAMLError as exc:
        logger.warning("skipping skill %s: invalid YAML frontmatter: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning(
            "skipping skill %s: frontmatter must be a mapping, got %s",
            path,
            type(data).__name__,
        )
        return None

    try:
        frontmatter = SkillFrontmatter.model_validate(data)
    except ValidationError as exc:
        logger.warning("skipping skill %s: frontmatter failed validation: %s", path, exc)
        return None

    if not _NAME_RE.match(frontmatter.name):
        logger.warning(
            "skipping skill %s: name %r must match %s",
            path,
            frontmatter.name,
            _NAME_RE.pattern,
        )
        return None

    if frontmatter.name != path.parent.name:
        logger.warning(
            "skipping skill %s: frontmatter name %r doesn't match folder %r",
            path,
            frontmatter.name,
            path.parent.name,
        )
        return None

    body = match.group("body").strip()
    if not body:
        logger.warning("skipping skill %s: body is empty", path)
        return None

    if len(frontmatter.description) > _DESCRIPTION_SOFT_CAP_CHARS:
        logger.warning(
            "skill %s description is %d chars (soft cap %d) — consider shortening "
            "so it doesn't crowd the 2 KB system-prompt budget",
            frontmatter.name,
            len(frontmatter.description),
            _DESCRIPTION_SOFT_CAP_CHARS,
        )

    return LoadedSkill(frontmatter=frontmatter, body=body, source=source, path=path)


def _discover_in_dir(directory: Path, source: SkillSource) -> Iterable[LoadedSkill]:
    """Yield every parseable ``SKILL.md`` inside ``directory``."""
    if not directory.is_dir():
        return
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        parsed = _parse_skill_file(skill_file, source)
        if parsed is not None:
            yield parsed


def load_all_skills() -> SkillCatalog:
    """Discover and parse every skill across project / user / package.

    **Every source contributes** — a skill named ``alpha`` in the user
    dir and a skill named ``beta`` in the package dir both end up in
    ``active``. The only time a skill is *not* in ``active`` is when a
    higher-priority source has a namesake — project > user > package.
    Those overridden entries land in ``shadowed`` so ``/skill list``
    can surface them rather than dropping them silently.

    Skills with invalid frontmatter are logged and skipped, not raised.
    Boot-time loader errors must never crash Gru — a missing or broken
    skill is a degraded surface, not a fatal config error.

    Returns:
        :class:`SkillCatalog` with the visible dict + the shadowed list.
    """
    active: dict[str, LoadedSkill] = {}
    shadowed: list[LoadedSkill] = []
    # Walk in priority order; first writer wins, later sources can't
    # overwrite. This is the inverse of "last writer wins" — explicit so
    # the shadowing intent is obvious in the loop.
    for directory, source in _candidate_dirs():
        for skill in _discover_in_dir(directory, source):
            if skill.name in active:
                # Quiet at WARNING level — shadowing is design, not an
                # error. DEBUG log + the shadowed list (via /skill list)
                # are the two surfaces that expose it.
                logger.debug(
                    "skill %r at %s shadowed by %s",
                    skill.name,
                    skill.path,
                    active[skill.name].path,
                )
                shadowed.append(skill)
                continue
            active[skill.name] = skill
    return SkillCatalog(active=active, shadowed=shadowed)


# --- capability --------------------------------------------------------


@dataclass
class SkillsCapability(AbstractCapability[Any]):
    """Publish skill name+description to Gru's prompt and expose ``load_skill``.

    Skills are discovered eagerly at construction; call :meth:`reload`
    after editing files on disk (or use the ``/skill reload`` slash,
    which calls ``reload`` for you). The 2 KB cap on the injected
    description block keeps the system-prompt prefix stable and cache-
    friendly even when users install many skills.

    Attributes:
        skills: name → :class:`LoadedSkill` for every active (un-
            shadowed) skill across the three sources.
        shadowed: every skill that *would* have loaded but lost to a
            higher-priority namesake. Surfaced via ``/skill list`` so
            overrides aren't invisible.
    """

    skills: dict[str, LoadedSkill] = field(default_factory=dict)
    shadowed: list[LoadedSkill] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Empty `skills` is the signal that we should discover from disk.
        # Tests that want a hand-crafted catalog pass `skills={"a": ...}`
        # explicitly; we leave those alone.
        if not self.skills:
            catalog = load_all_skills()
            self.skills = catalog.active
            self.shadowed = catalog.shadowed

    def reload(self) -> None:
        """Rediscover skills from disk. Used by ``/skill reload``."""
        catalog = load_all_skills()
        self.skills = catalog.active
        self.shadowed = catalog.shadowed

    def load_skill(self, name: str) -> str:
        """Return the markdown body of skill ``name`` (the ``load_skill`` tool
        core). Closure-free entry point so callers and tests can resolve a
        skill body without going through the toolset.

        Raises:
            ValueError: if no skill is loaded, or ``name`` is unknown — the
                message lists the available names so the model can self-correct.
        """
        if not self.skills:
            raise ValueError(
                "no skills are loaded — install one under "
                "~/.jac/skills/<name>/SKILL.md or "
                "<repo>/.agents/skills/<name>/SKILL.md, or use a shipped "
                "reference skill."
            )
        skill = self.skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self.skills)) or "(none)"
            raise ValueError(
                f"unknown skill {name!r}; available: {available}. "
                "Use the exact name from the Skills block of your system prompt."
            )
        return skill.body

    def get_instructions(self) -> Any:
        # Re-render on every request so ``/skill reload`` shows up without
        # rebuilding Gru. The render is cheap (a sort + a few string
        # joins) and the *output* is stable as long as the on-disk skills
        # don't change — which is the only thing that affects prompt
        # caching anyway. Cache-friendliness lives in the stable output,
        # not in re-using a Python string object.
        def _instructions(_ctx: Any) -> str:
            return _render_skills_block(self.skills)

        return _instructions

    def get_toolset(self) -> Any:
        # `load_skill` reads a parsed in-memory body; no filesystem touch
        # at call time, no approval needed. Bare toolset. Built as a closure
        # over `self` (mirrors ClarifyCapability) so two SkillsCapability
        # instances don't clobber a shared module global.
        return jac_function_toolset(*self._build_tools())

    def _build_tools(self) -> list[Any]:
        capability = self

        @jac_tool
        def load_skill(reason: str, name: str) -> str:
            """Pull a loaded skill's body into the current turn.

            Use this when the user's request matches a skill's purpose
            (visible in the "Skills" block of your system prompt). The body
            is delivered to you as this tool's result — read it, then act on
            its guidance.

            Skills are advisory text, not executable tools. Loading one
            doesn't grant access to anything; it just inserts a playbook you
            can follow. If the skill names a tool you don't have, follow
            whatever fallback the body suggests — don't refuse blindly.

            Args:
                reason: One-sentence justification for the load (per JAC's
                    tool discipline). Becomes part of the audit trail.
                name: Skill identifier as advertised in the "Skills"
                    system-prompt block (e.g. ``"code-review"``).

            Returns:
                The full markdown body of the skill, ready to act on.

            Raises:
                ValueError: if ``name`` doesn't match any loaded skill.
                    Lists the available names so the model can self-correct.
            """
            return capability.load_skill(name)

        return [load_skill]


def make_skills_capability() -> SkillsCapability:
    """Convenience constructor mirroring other capabilities' factories."""
    return SkillsCapability()


# --- rendering ---------------------------------------------------------


def _render_skills_block(skills: dict[str, LoadedSkill]) -> str:
    """Render the system-prompt block that advertises available skills.

    Two-pass strategy: build the full block first; if it exceeds the 2 KB
    cap, re-render as a name-only listing with a note pointing the user
    at ``/skill list`` for the full descriptions. We don't try to
    cherry-pick which descriptions fit — name-only is predictable and
    avoids surprising "some skills disappeared" UX.
    """
    if not skills:
        return ""

    header = (
        "\n\n---\n\n# Skills\n\n"
        "Loaded skills you can pull into context with `load_skill(reason, name)` "
        "when the user's request matches:\n\n"
    )

    full_lines: list[str] = []
    for name in sorted(skills):
        skill = skills[name]
        desc = skill.description.strip().replace("\n", " ")
        full_lines.append(f"- **{name}** — {desc}")
    full_block = header + "\n".join(full_lines) + "\n"

    if len(full_block.encode("utf-8")) <= _INSTRUCTIONS_CAP_BYTES:
        return full_block

    # Fallback: name-only listing. Predictable and small.
    truncated_lines = [f"- `{name}`" for name in sorted(skills)]
    return (
        header
        + "\n".join(truncated_lines)
        + "\n\n_(descriptions truncated — run `/skill list` for the full table)._\n"
    )
