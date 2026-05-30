"""Tests for the community-format skill loader (Phase D / D21).

Coverage:

- Frontmatter parsing (valid, missing block, invalid YAML, wrong shape,
  name mismatch, empty body, name regex).
- Layered shadowing (project > user > package).
- 2 KB cap on the system-prompt block (full vs name-only fallback).
- ``load_skill`` tool happy path + unknown-name error.
- ``SkillsCapability.reload`` picks up on-disk changes.
- ``/skill list|use|reload`` slash subcommands.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from textwrap import dedent

import pytest
from rich.console import Console

from jac.capabilities.skills import (
    _INSTRUCTIONS_CAP_BYTES,
    LoadedSkill,
    SkillCatalog,
    SkillFrontmatter,
    SkillsCapability,
    _render_skills_block,
    load_all_skills,
    make_skills_capability,
)
from jac.cli.slash import (
    Handled,
    InjectUserText,
    SlashContext,
    dispatch,
)
from jac.runtime.session import Session
from jac.workspace import paths

# ---------- fixtures ---------------------------------------------------


@pytest.fixture
def isolated_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point user + project skill dirs at tmp_path; clear the package dir.

    We don't actually wipe the package dir on disk (it ships with the
    repo) — we just redirect :func:`package_skills_dir` to an empty tmp
    subdir for the duration of the test. That keeps the shipped
    reference skills from leaking into unit-test fixtures.
    """
    user_skills = tmp_path / ".jac" / "skills"
    project_root = tmp_path / "project"
    project_skills = project_root / ".agents" / "skills"
    pkg_skills = tmp_path / "pkg-skills"

    user_skills.mkdir(parents=True)
    project_skills.mkdir(parents=True)
    pkg_skills.mkdir(parents=True)
    # A .git so paths.is_in_project_repo() returns True under the project root.
    (project_root / ".git").mkdir()

    paths.find_project_root.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(paths, "USER_SKILLS_DIR", user_skills)
    monkeypatch.setattr(paths, "package_skills_dir", lambda: pkg_skills)
    # find_project_root walks up looking for .git; monkeypatch its cache result
    # by pointing the function at a wrapper that ignores cache (tests run cheap
    # enough that we re-walk per call).
    monkeypatch.setattr(
        paths,
        "find_project_root",
        lambda start=None: project_root,
    )
    yield tmp_path


def _write_skill(
    directory: Path, name: str, *, description: str = "desc", body: str = "body"
) -> Path:
    skill_dir = directory / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        dedent(
            f"""\
            ---
            name: {name}
            description: {description}
            ---
            {body}
            """
        ),
        encoding="utf-8",
    )
    return path


# ---------- frontmatter parsing ---------------------------------------


def test_frontmatter_validates_required_fields() -> None:
    """name + description are required; extra fields rejected by extra='forbid'."""
    fm = SkillFrontmatter.model_validate({"name": "x", "description": "y"})
    assert fm.name == "x"
    assert fm.description == "y"
    assert fm.tools_required == []

    with pytest.raises(Exception, match="tools"):
        SkillFrontmatter.model_validate({"name": "x", "description": "y", "tools": ["read"]})


def test_load_all_skills_parses_a_valid_skill(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "alpha", description="alpha desc", body="alpha body text")
    catalog = load_all_skills()
    assert isinstance(catalog, SkillCatalog)
    assert "alpha" in catalog.active
    assert catalog.active["alpha"].description == "alpha desc"
    assert catalog.active["alpha"].body == "alpha body text"
    assert catalog.active["alpha"].source == "user"
    assert catalog.shadowed == []


def test_missing_frontmatter_is_skipped(
    isolated_skills: Path, caplog: pytest.LogCaptureFixture
) -> None:
    skill_dir = paths.USER_SKILLS_DIR / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        catalog = load_all_skills()
    assert "broken" not in catalog.active
    assert any("missing YAML frontmatter" in rec.message for rec in caplog.records)


def test_invalid_yaml_frontmatter_is_skipped(
    isolated_skills: Path, caplog: pytest.LogCaptureFixture
) -> None:
    skill_dir = paths.USER_SKILLS_DIR / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad\ndescription: [unclosed\n---\nbody\n", encoding="utf-8"
    )
    with caplog.at_level("WARNING"):
        catalog = load_all_skills()
    assert "bad" not in catalog.active


def test_name_mismatch_with_folder_is_skipped(
    isolated_skills: Path, caplog: pytest.LogCaptureFixture
) -> None:
    skill_dir = paths.USER_SKILLS_DIR / "folder-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: different-name\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    with caplog.at_level("WARNING"):
        catalog = load_all_skills()
    assert "different-name" not in catalog.active
    assert "folder-name" not in catalog.active


def test_invalid_name_regex_is_skipped(isolated_skills: Path) -> None:
    skill_dir = paths.USER_SKILLS_DIR / "BadName"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: BadName\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    assert "BadName" not in load_all_skills().active


def test_empty_body_is_skipped(isolated_skills: Path) -> None:
    skill_dir = paths.USER_SKILLS_DIR / "empty"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: empty\ndescription: d\n---\n", encoding="utf-8")
    assert "empty" not in load_all_skills().active


def test_long_description_warns_but_loads(
    isolated_skills: Path, caplog: pytest.LogCaptureFixture
) -> None:
    long_desc = "x" * 500
    _write_skill(paths.USER_SKILLS_DIR, "verbose", description=long_desc)
    with caplog.at_level("WARNING"):
        catalog = load_all_skills()
    assert "verbose" in catalog.active
    assert any("soft cap" in rec.message for rec in caplog.records)


# ---------- shadowing --------------------------------------------------


def test_project_shadows_user(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "shared", description="user version", body="user body")
    _write_skill(
        paths.project_skills_dir(), "shared", description="project ver", body="project body"
    )
    catalog = load_all_skills()
    assert catalog.active["shared"].source == "project"
    assert catalog.active["shared"].body == "project body"
    # The user-level version is captured as shadowed, not silently dropped.
    assert len(catalog.shadowed) == 1
    assert catalog.shadowed[0].name == "shared"
    assert catalog.shadowed[0].source == "user"


def test_user_shadows_package(isolated_skills: Path) -> None:
    _write_skill(paths.package_skills_dir(), "shared", description="pkg", body="pkg body")
    _write_skill(paths.USER_SKILLS_DIR, "shared", description="user", body="user body")
    catalog = load_all_skills()
    assert catalog.active["shared"].source == "user"
    assert catalog.active["shared"].body == "user body"
    assert [s.source for s in catalog.shadowed] == ["package"]


def test_distinct_names_from_all_sources_coexist(isolated_skills: Path) -> None:
    _write_skill(paths.package_skills_dir(), "pkg-only", description="d", body="b")
    _write_skill(paths.USER_SKILLS_DIR, "user-only", description="d", body="b")
    _write_skill(paths.project_skills_dir(), "project-only", description="d", body="b")
    catalog = load_all_skills()
    assert set(catalog.active) == {"pkg-only", "user-only", "project-only"}
    assert catalog.shadowed == []


def test_three_way_collision_shadows_two(isolated_skills: Path) -> None:
    """Project + user + package all defining 'shared' → project active, two shadowed."""
    _write_skill(paths.package_skills_dir(), "shared", body="pkg body")
    _write_skill(paths.USER_SKILLS_DIR, "shared", body="user body")
    _write_skill(paths.project_skills_dir(), "shared", body="project body")
    catalog = load_all_skills()
    assert catalog.active["shared"].source == "project"
    assert {s.source for s in catalog.shadowed} == {"user", "package"}


# ---------- rendering / 2 KB cap --------------------------------------


def test_empty_skills_renders_empty_string() -> None:
    assert _render_skills_block({}) == ""


def test_full_block_under_cap_is_full(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "a", description="short")
    _write_skill(paths.USER_SKILLS_DIR, "b", description="short")
    block = _render_skills_block(load_all_skills().active)
    assert "**a** — short" in block
    assert "**b** — short" in block
    assert "truncated" not in block


def test_oversize_block_falls_back_to_name_only(isolated_skills: Path) -> None:
    """A pile of long-described skills must exceed 2 KB → name-only fallback."""
    big_desc = "x" * 180  # well under per-skill soft cap, but cumulative wins
    for i in range(30):
        _write_skill(paths.USER_SKILLS_DIR, f"skill-{i:02d}", description=big_desc)
    block = _render_skills_block(load_all_skills().active)
    assert "truncated" in block
    # Name-only listing is much shorter than full descriptions.
    assert len(block.encode("utf-8")) < _INSTRUCTIONS_CAP_BYTES * 2
    # Every name still appears.
    for i in range(30):
        assert f"skill-{i:02d}" in block


# ---------- load_skill tool -------------------------------------------


def test_load_skill_returns_body(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "alpha", body="alpha body content")
    cap = SkillsCapability()
    out = cap.load_skill("alpha")
    assert out == "alpha body content"


def test_load_skill_unknown_raises_with_available(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "alpha", body="b")
    cap = SkillsCapability()
    with pytest.raises(ValueError, match="unknown skill"):
        cap.load_skill("nonexistent")


def test_load_skill_with_no_skills_loaded_raises(isolated_skills: Path) -> None:
    cap = SkillsCapability()  # empty dirs → empty catalog
    with pytest.raises(ValueError, match="no skills are loaded"):
        cap.load_skill("anything")


def test_two_capabilities_do_not_clobber_each_other(isolated_skills: Path) -> None:
    # R14: with the module global gone, two instances keep independent
    # catalogs — the second's tool resolves against its own skills, not a
    # shared global the last constructor stomped.
    _write_skill(paths.USER_SKILLS_DIR, "alpha", body="alpha body")
    cap_a = SkillsCapability()  # discovers alpha from disk
    beta = LoadedSkill(
        frontmatter=SkillFrontmatter(name="beta", description="d"),
        body="beta body",
        source="user",
        path=Path("beta/SKILL.md"),
    )
    cap_b = SkillsCapability(skills={"beta": beta})  # hand-crafted, no discovery
    assert cap_a.load_skill("alpha") == "alpha body"
    assert cap_b.load_skill("beta") == "beta body"
    with pytest.raises(ValueError, match="unknown skill"):
        cap_b.load_skill("alpha")


# ---------- capability reload -----------------------------------------


def test_reload_picks_up_new_skills(isolated_skills: Path) -> None:
    cap = make_skills_capability()
    assert cap.skills == {}
    _write_skill(paths.USER_SKILLS_DIR, "fresh", description="d", body="b")
    cap.reload()
    assert "fresh" in cap.skills


def test_reload_drops_removed_skills(isolated_skills: Path) -> None:
    path = _write_skill(paths.USER_SKILLS_DIR, "ephemeral", body="b")
    cap = make_skills_capability()
    assert "ephemeral" in cap.skills
    path.unlink()
    path.parent.rmdir()
    cap.reload()
    assert "ephemeral" not in cap.skills


def test_get_instructions_is_dynamic(isolated_skills: Path) -> None:
    """Re-rendering on each call means /skill reload is reflected without a Gru rebuild."""
    cap = make_skills_capability()
    fn = cap.get_instructions()
    assert fn(None) == ""
    _write_skill(paths.USER_SKILLS_DIR, "late", description="late desc", body="b")
    cap.reload()
    out = fn(None)
    assert "late" in out
    assert "late desc" in out


# ---------- slash handler ---------------------------------------------


def _slash_ctx(skills_cap: SkillsCapability | None) -> tuple[SlashContext, StringIO]:
    buf = StringIO()
    ctx = SlashContext(
        console=Console(file=buf, force_terminal=False, width=120),
        session=Session(session_id="test", message_history=[]),
        profile_name=None,
        profile=None,
        model_id="anthropic:claude-sonnet-4-5",
        skills=skills_cap,
    )
    return ctx, buf


def test_slash_skill_no_args_prints_usage(isolated_skills: Path) -> None:
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    result = dispatch("/skill", ctx)
    assert isinstance(result, Handled)
    assert "usage" in buf.getvalue().lower()


def test_slash_skill_list_empty(isolated_skills: Path) -> None:
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    result = dispatch("/skill list", ctx)
    assert isinstance(result, Handled)
    assert "no skills installed" in buf.getvalue().lower()


def test_slash_skill_list_shows_table(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "alpha", description="alpha desc")
    _write_skill(paths.project_skills_dir(), "beta", description="beta desc")
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    dispatch("/skill list", ctx)
    output = buf.getvalue()
    assert "alpha" in output
    assert "beta" in output
    assert "alpha desc" in output
    assert "project" in output  # source column
    assert "user" in output


def test_slash_skill_list_surfaces_shadowed(isolated_skills: Path) -> None:
    """Overridden skills must show up in the shadowed table so users see them."""
    _write_skill(paths.USER_SKILLS_DIR, "shared", description="user one")
    _write_skill(paths.project_skills_dir(), "shared", description="project one")
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    dispatch("/skill list", ctx)
    output = buf.getvalue()
    assert "shadowed" in output.lower()
    # The active project version + the shadowed user version both appear.
    assert "project one" in output
    # Canonical user-skill path (not the absolute tmp dir) shows in the shadow table.
    assert "~/.jac/skills/shared/SKILL.md" in output


def test_slash_skill_use_returns_inject(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "alpha", body="alpha body text")
    cap = make_skills_capability()
    ctx, _ = _slash_ctx(cap)
    result = dispatch("/skill use alpha", ctx)
    assert isinstance(result, InjectUserText)
    assert result.text == "alpha body text"


def test_slash_skill_use_unknown(isolated_skills: Path) -> None:
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    result = dispatch("/skill use nope", ctx)
    assert isinstance(result, Handled)
    assert "unknown skill" in buf.getvalue().lower()


def test_slash_skill_use_extra_args_are_warned(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "alpha", body="b")
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    result = dispatch("/skill use alpha and more", ctx)
    assert isinstance(result, InjectUserText)
    assert "ignoring extra args" in buf.getvalue()


def test_slash_skill_reload_reports_diff(isolated_skills: Path) -> None:
    _write_skill(paths.USER_SKILLS_DIR, "before", body="b")
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    # Add a new skill on disk; reload should show +1 new.
    _write_skill(paths.USER_SKILLS_DIR, "after", body="b")
    result = dispatch("/skill reload", ctx)
    assert isinstance(result, Handled)
    assert "after" in buf.getvalue()
    assert "+1 new" in buf.getvalue()


def test_slash_skill_without_capability_is_friendly(isolated_skills: Path) -> None:
    ctx, buf = _slash_ctx(skills_cap=None)
    result = dispatch("/skill list", ctx)
    assert isinstance(result, Handled)
    assert "not wired" in buf.getvalue()


def test_slash_skill_unknown_subcommand(isolated_skills: Path) -> None:
    cap = make_skills_capability()
    ctx, buf = _slash_ctx(cap)
    result = dispatch("/skill banana", ctx)
    assert isinstance(result, Handled)
    assert "unknown /skill subcommand" in buf.getvalue()


# ---------- shipped reference skills ----------------------------------


def test_shipped_reference_skills_parse() -> None:
    """The three reference skills must always load — they're part of the package.

    Don't isolate the package dir here; we explicitly want to validate
    what ships with JAC. Use the real package_skills_dir().
    """
    skills_dir = paths.package_skills_dir()
    if not skills_dir.is_dir():
        pytest.skip("reference skills not present (likely an editable-install quirk)")
    expected = {"code-review", "summarize-large-files", "verify-change"}
    found = {p.parent.name for p in skills_dir.glob("*/SKILL.md")}
    assert expected.issubset(found), f"missing reference skills: {expected - found}"
