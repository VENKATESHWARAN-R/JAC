"""Tests for the ``remember`` / ``forget`` memory tools (Phase 2a / 2a.1).

Two layers of coverage:

- **Tool behaviour** — ``remember`` and ``forget`` end to end against an
  isolated tmp workspace: bootstrap-on-first-write, category routing,
  scope routing, de-dup, the size hint, the audit comment, and the
  fail-first paths (empty content, multiline content, project scope
  outside a repo, ambiguous/absent ``forget`` targets).
- **Parsing internals** — the markdown helpers (``_extract_section``,
  ``_insert_into_section``, ``_find_all_matches``, ``_remove_line``,
  ``_find_duplicate``, ``_strip_bullet_metadata``, ``_count_bullets``)
  on the edge cases most likely to bite: empty sections, trailing
  newlines, adjacent bullets, the last section in the file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jac.capabilities import memory
from jac.capabilities.memory import (
    _count_bullets,
    _extract_section,
    _find_all_matches,
    _find_duplicate,
    _insert_into_section,
    _remove_line,
    _strip_bullet_metadata,
    forget,
    remember,
)
from jac.errors import JacConfigError
from jac.workspace import paths
from jac.workspace.session_ctx import set_current_session_id

# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point user + project memory at tmp_path so writes don't escape.

    ``memory.py`` reaches the filesystem through the ``paths`` module
    (``USER_MEMORY_FILE`` plus the ``project_memory_file`` / ``in_project``
    callables), so patching those attributes is enough to fully sandbox
    both scopes. The active session id is cleared so audit comments are
    deterministic; individual tests set it when they assert on the
    ``session:`` field.
    """
    user_mem = tmp_path / "user" / "memory.md"
    project_mem = tmp_path / "project" / ".agents" / "memory.md"
    monkeypatch.setattr(paths, "USER_MEMORY_FILE", user_mem)
    monkeypatch.setattr(paths, "project_memory_file", lambda: project_mem)
    monkeypatch.setattr(paths, "in_project", lambda start=None: True)
    set_current_session_id(None)
    yield tmp_path
    set_current_session_id(None)


# ---------- remember: happy path ----------


def test_remember_bootstraps_file_and_stores_under_section() -> None:
    msg = remember(
        reason="seed",
        content="uses uv, not pip",
        category="convention",
        scope="user",
    )
    assert "stored under Conventions (user scope)" in msg
    text = paths.USER_MEMORY_FILE.read_text()
    # Template heading + the new bullet both present.
    assert "## Conventions" in text
    assert "- uses uv, not pip" in text
    # Schema header from the template survived.
    assert "<!-- jac:memory schema=1 -->" in text


def test_remember_routes_each_category_to_its_section() -> None:
    remember(reason="r", content="tests live in tests/", category="fact", scope="user")
    remember(reason="r", content="prefers terse output", category="preference", scope="user")
    remember(reason="r", content="watch the cache TTL", category="gotcha", scope="user")
    remember(reason="r", content="adopted Monty for sandbox", category="decision", scope="user")

    text = paths.USER_MEMORY_FILE.read_text()
    facts = _extract_section(text, "Facts")
    prefs = _extract_section(text, "Preferences")
    gotchas = _extract_section(text, "Gotchas")
    decisions = _extract_section(text, "Decisions")
    assert facts is not None and "tests live in tests/" in facts
    assert prefs is not None and "prefers terse output" in prefs
    assert gotchas is not None and "watch the cache TTL" in gotchas
    assert decisions is not None and "adopted Monty for sandbox" in decisions


def test_remember_stamps_session_id_when_set() -> None:
    set_current_session_id("2026-05-29T09-00-00")
    remember(reason="r", content="a durable fact", category="fact", scope="user")
    text = paths.USER_MEMORY_FILE.read_text()
    assert "session: 2026-05-29T09-00-00" in text


def test_remember_omits_session_field_when_unset() -> None:
    remember(reason="r", content="a durable fact", category="fact", scope="user")
    line = next(ln for ln in paths.USER_MEMORY_FILE.read_text().splitlines() if ln.startswith("- "))
    assert "<!-- jac:" in line
    assert "session:" not in line


def test_audit_timestamp_uses_dashes_not_colons() -> None:
    """Regression: the audit comment must be filesystem-safe (no colons),
    matching the documented ``YYYY-MM-DDTHH-MM-SS`` form."""
    remember(reason="r", content="a durable fact", category="fact", scope="user")
    line = next(ln for ln in paths.USER_MEMORY_FILE.read_text().splitlines() if ln.startswith("- "))
    comment = line[line.index("<!--") :]
    # No clock colons inside the comment.
    assert ":" not in comment.replace("session:", "").replace("jac:", "")


# ---------- remember: de-dup ----------


def test_remember_rejects_exact_duplicate() -> None:
    remember(reason="r", content="uses uv, not pip", category="convention", scope="user")
    msg = remember(reason="r", content="uses uv, not pip", category="convention", scope="user")
    assert "already recorded" in msg
    # Only one bullet exists.
    text = paths.USER_MEMORY_FILE.read_text()
    assert text.count("- uses uv, not pip") == 1


def test_remember_dedup_is_case_and_whitespace_insensitive() -> None:
    remember(reason="r", content="Uses UV, Not Pip", category="convention", scope="user")
    msg = remember(
        reason="r", content="  uses   uv,  not   pip ", category="convention", scope="user"
    )
    assert "already recorded" in msg


def test_remember_substring_is_not_a_duplicate() -> None:
    """``uses uv`` must not shadow ``uses uvicorn`` — exact-normalized only."""
    remember(reason="r", content="uses uv", category="convention", scope="user")
    msg = remember(reason="r", content="uses uvicorn", category="convention", scope="user")
    assert "stored under" in msg
    assert paths.USER_MEMORY_FILE.read_text().count("- uses uv") == 2


def test_dedup_only_within_target_section() -> None:
    """Same text in a different section is not a duplicate."""
    remember(reason="r", content="overlapping text", category="fact", scope="user")
    msg = remember(reason="r", content="overlapping text", category="gotcha", scope="user")
    assert "stored under Gotchas" in msg


# ---------- remember: size hint ----------


def test_remember_size_hint_past_threshold() -> None:
    for i in range(memory._SECTION_SIZE_WARN):
        remember(reason="r", content=f"fact number {i}", category="fact", scope="user")
    # The next one crosses the threshold.
    msg = remember(reason="r", content="one more fact past the line", category="fact", scope="user")
    assert "consider" in msg
    assert "entries" in msg


def test_remember_no_size_hint_below_threshold() -> None:
    msg = remember(reason="r", content="a lone fact", category="fact", scope="user")
    assert "consider" not in msg


# ---------- remember: validation / fail-first ----------


def test_remember_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        remember(reason="r", content="   ", category="fact", scope="user")


def test_remember_rejects_multiline_content() -> None:
    with pytest.raises(ValueError, match="single line"):
        remember(reason="r", content="line one\nline two", category="fact", scope="user")


def test_remember_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="unknown category"):
        remember(reason="r", content="x", category="bogus", scope="user")  # type: ignore[arg-type]


def test_remember_project_scope_outside_repo_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "in_project", lambda start=None: False)
    with pytest.raises(JacConfigError, match="requires a project"):
        remember(reason="r", content="x", category="fact", scope="project")


def test_remember_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        remember(reason="r", content="x", category="fact", scope="elsewhere")  # type: ignore[arg-type]


# ---------- scopes write to distinct files ----------


def test_user_and_project_scope_use_distinct_files() -> None:
    remember(reason="r", content="user-level pref", category="preference", scope="user")
    remember(reason="r", content="project-level convention", category="convention", scope="project")

    user_text = paths.USER_MEMORY_FILE.read_text()
    project_text = paths.project_memory_file().read_text()
    assert "user-level pref" in user_text and "user-level pref" not in project_text
    assert "project-level convention" in project_text
    assert "project-level convention" not in user_text
    # Each scope's template names itself.
    assert "# User memory" in user_text
    assert "# Project memory" in project_text


# ---------- forget ----------


def test_forget_removes_matching_entry() -> None:
    remember(reason="r", content="a removable fact", category="fact", scope="user")
    msg = forget(reason="reversed", content="a removable fact", scope="user")
    assert "removed from Facts (user scope)" in msg
    assert "a removable fact" not in paths.USER_MEMORY_FILE.read_text()


def test_forget_is_case_and_whitespace_insensitive() -> None:
    remember(reason="r", content="a removable fact", category="fact", scope="user")
    forget(reason="r", content="  A REMOVABLE   FACT ", scope="user")
    assert "a removable fact" not in paths.USER_MEMORY_FILE.read_text()


def test_forget_no_match_raises() -> None:
    remember(reason="r", content="something", category="fact", scope="user")
    with pytest.raises(ValueError, match="no entry matching"):
        forget(reason="r", content="not present", scope="user")


def test_forget_ambiguous_match_raises() -> None:
    """Same prose in two sections → ambiguous, must refuse."""
    remember(reason="r", content="shared phrase", category="fact", scope="user")
    remember(reason="r", content="shared phrase", category="gotcha", scope="user")
    with pytest.raises(ValueError, match="entries match"):
        forget(reason="r", content="shared phrase", scope="user")


def test_forget_missing_file_raises() -> None:
    with pytest.raises(JacConfigError, match="nothing to forget"):
        forget(reason="r", content="anything", scope="user")


def test_forget_leaves_other_entries_intact() -> None:
    remember(reason="r", content="keep me", category="fact", scope="user")
    remember(reason="r", content="drop me", category="fact", scope="user")
    forget(reason="r", content="drop me", scope="user")
    text = paths.USER_MEMORY_FILE.read_text()
    assert "keep me" in text
    assert "drop me" not in text


# ---------- internals: _extract_section ----------


_SAMPLE = """<!-- jac:memory schema=1 -->
# Project memory

## Conventions
- alpha
- beta

## Facts
- gamma

## Decisions
"""


def test_extract_section_returns_body_between_headings() -> None:
    body = _extract_section(_SAMPLE, "Conventions")
    assert body is not None
    assert "- alpha" in body and "- beta" in body
    assert "- gamma" not in body  # didn't bleed into the next section


def test_extract_section_last_section_runs_to_eof() -> None:
    body = _extract_section(_SAMPLE, "Decisions")
    assert body is not None
    assert body.strip() == ""  # empty trailing section


def test_extract_section_absent_returns_none() -> None:
    assert _extract_section(_SAMPLE, "Nonexistent") is None


# ---------- internals: _insert_into_section ----------


def test_insert_appends_after_last_bullet() -> None:
    out = _insert_into_section(_SAMPLE, "Conventions", "- delta")
    conv = _extract_section(out, "Conventions")
    assert conv is not None
    lines = [ln for ln in conv.splitlines() if ln.startswith("- ")]
    assert lines == ["- alpha", "- beta", "- delta"]


def test_insert_into_empty_section() -> None:
    out = _insert_into_section(_SAMPLE, "Decisions", "- first decision")
    dec = _extract_section(out, "Decisions")
    assert dec is not None
    assert "- first decision" in dec
    # Didn't disturb earlier sections.
    assert _extract_section(out, "Facts") == _extract_section(_SAMPLE, "Facts")


def test_insert_preserves_trailing_newline() -> None:
    assert _SAMPLE.endswith("\n")
    out = _insert_into_section(_SAMPLE, "Facts", "- new fact")
    assert out.endswith("\n")


def test_insert_into_missing_section_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        _insert_into_section(_SAMPLE, "Ghost", "- x")


# ---------- internals: _find_all_matches / _remove_line ----------


def test_find_all_matches_reports_section_and_index() -> None:
    hits = _find_all_matches(_SAMPLE, "alpha")
    assert len(hits) == 1
    section, line_index, prose = hits[0]
    assert section == "Conventions"
    assert prose == "alpha"
    assert _SAMPLE.splitlines()[line_index].strip() == "- alpha"


def test_find_all_matches_across_sections() -> None:
    text = _SAMPLE.replace("- gamma", "- alpha")  # duplicate prose in Facts
    hits = _find_all_matches(text, "alpha")
    assert {h[0] for h in hits} == {"Conventions", "Facts"}


def test_find_all_matches_finds_orphaned_bullet_above_first_heading() -> None:
    # R18: a hand-edited bullet above the first `## heading` used to be
    # invisible to forget — now it matches under the "(no section)" label.
    text = (
        "<!-- jac:memory schema=1 -->\n# Project memory\n- orphan fact\n\n## Conventions\n- alpha\n"
    )
    hits = _find_all_matches(text, "orphan fact")
    assert len(hits) == 1
    section, _line_index, prose = hits[0]
    assert section == "(no section)"
    assert prose == "orphan fact"


def test_forget_removes_orphaned_bullet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # R18 end-to-end: forget must succeed (not raise "no entry matching")
    # on a bullet that sits outside any `## section`.
    from jac.capabilities import memory as memory_mod

    path = tmp_path / "memory.md"
    path.write_text(
        "<!-- jac:memory schema=1 -->\n# User memory\n- orphan fact\n\n## Conventions\n- alpha\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(memory_mod, "_memory_path_for_scope", lambda scope: path)
    out = forget(reason="cleanup", content="orphan fact", scope="user")
    assert "orphan fact" in out
    assert "(no section)" in out
    assert "- orphan fact" not in path.read_text(encoding="utf-8")


def test_remove_line_drops_only_that_line() -> None:
    lines = _SAMPLE.splitlines()
    idx = lines.index("- beta")
    out = _remove_line(_SAMPLE, idx)
    assert "- beta" not in out
    assert "- alpha" in out and "- gamma" in out


def test_remove_line_out_of_range_raises() -> None:
    with pytest.raises(IndexError):
        _remove_line(_SAMPLE, 9999)


# ---------- internals: misc helpers ----------


def test_strip_bullet_metadata_drops_prefix_and_comment() -> None:
    line = "- the fact here <!-- jac: 2026-05-29T09-00-00 session: x -->"
    assert _strip_bullet_metadata(line) == "the fact here"


def test_strip_bullet_metadata_without_comment() -> None:
    assert _strip_bullet_metadata("- plain fact") == "plain fact"


def test_count_bullets() -> None:
    body = "- one\nsome prose\n- two\n\n- three"
    assert _count_bullets(body) == 3


def test_find_duplicate_matches_ignoring_metadata() -> None:
    body = "- the fact <!-- jac: 2026-05-29T09-00-00 -->"
    assert _find_duplicate(body, "THE   fact") == "the fact"


def test_find_duplicate_none_when_absent() -> None:
    body = "- the fact\n- another fact"
    assert _find_duplicate(body, "third fact") is None


# ---------- read_memory_entries (powers /memory) ----------


def test_read_memory_entries_absent_file_returns_empty_sections() -> None:
    from jac.capabilities.memory import read_memory_entries

    path, sections = read_memory_entries("user")
    assert path == paths.USER_MEMORY_FILE
    assert not path.is_file()  # read-only: never created the file
    assert set(sections) == {"Conventions", "Facts", "Preferences", "Gotchas", "Decisions"}
    assert all(v == [] for v in sections.values())


def test_read_memory_entries_returns_prose_without_metadata() -> None:
    from jac.capabilities.memory import read_memory_entries

    set_current_session_id("2026-05-29T09-00-00")
    remember(reason="r", content="uses uv, not pip", category="convention", scope="user")
    remember(reason="r", content="tests live in tests/", category="fact", scope="user")

    _path, sections = read_memory_entries("user")
    assert sections["Conventions"] == ["uses uv, not pip"]
    assert sections["Facts"] == ["tests live in tests/"]
    # No audit metadata leaks into the prose.
    assert all("<!--" not in entry for entries in sections.values() for entry in entries)


def test_read_memory_entries_project_scope_outside_repo_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike ``remember``, reading project memory outside a repo must not raise."""
    monkeypatch.setattr(paths, "in_project", lambda start=None: False)
    from jac.capabilities.memory import read_memory_entries

    path, sections = read_memory_entries("project")
    assert path == paths.project_memory_file()
    assert all(v == [] for v in sections.values())
