"""Tests for jac.capabilities.a2a.guest.

The single critical assertion for PR1: the guest Gru's toolset is
**exactly** the four allowed read-only tools plus the two
approval-gated filesystem writes (write_file / edit_file — which can't
fire in the guest because no approval handler is installed). All of
the genuinely excluded capabilities (web / process / plan / clarify /
memory / shell) MUST NOT appear in the introspection.

This is the security guarantee D24 promises, expressed as a test.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset

from jac.capabilities.a2a.guest import build_guest_gru
from jac.errors import JacConfigError

# Tools that must be present (the four read-only tools the guest is allowed
# to call). write/edit are bundled by FilesystemCapability and present in
# the toolset, but unreachable in the guest because no approval handler is
# installed — see test_guest_includes_writes_but_no_handler below.
_ALLOWED = frozenset({"read_file", "list_dir", "grep", "glob"})

# Tools that MUST NOT appear in the guest toolset at all (these come from
# capabilities we don't pass to build_guest_gru — if any of them show up,
# the guest construction is leaking host capabilities into peer scope).
_FORBIDDEN = frozenset(
    {
        # shell / process — host runtime
        "run_shell",
        "start_process",
        "kill_process",
        "tail_process",
        "list_processes",
        # in-session checklist — host state
        "plan",
        "update_plan",
        "get_plan",
        # interactive prompt — no human in guest path
        "clarify",
        # memory writes — never expose to peers
        "remember",
        "forget",
        # web — host's money + host's IP address
        "web_search",
        "fetch_url",
    }
)


def _flatten_function_toolsets(toolset) -> Iterable[FunctionToolset]:
    """Recursively walk a CombinedToolset / WrapperToolset tree, yielding
    every leaf :class:`FunctionToolset` we find.

    pydantic-ai's toolset machinery is a tree of wrappers (Approval,
    Combined, etc.) around the actual ``FunctionToolset`` leaves that
    hold ``tools: dict[str, ToolsetTool]``. We don't want to depend on
    a specific wrapper layout (it's changed before across versions) —
    we walk via ``wrapped`` (single-child wrapper) and ``toolsets``
    (combiner) instead.
    """
    if isinstance(toolset, FunctionToolset):
        yield toolset
        return
    if hasattr(toolset, "wrapped"):
        yield from _flatten_function_toolsets(toolset.wrapped)
        return
    if hasattr(toolset, "toolsets"):
        for child in toolset.toolsets:
            yield from _flatten_function_toolsets(child)


def _tool_names(agent) -> frozenset[str]:
    """All tool names registered with ``agent`` across every capability."""
    cap_toolsets = getattr(agent, "_cap_toolsets", None)
    assert cap_toolsets, (
        "pydantic-ai Agent stopped exposing `_cap_toolsets` — "
        "find the new accessor and update this helper."
    )
    names: set[str] = set()
    for top in cap_toolsets:
        for fts in _flatten_function_toolsets(top):
            names.update(fts.tools.keys())
    return frozenset(names)


def _agent():
    """Build a guest Gru with a TestModel — no real provider needed."""
    return build_guest_gru(model=TestModel())


def test_build_guest_gru_requires_model():
    with pytest.raises(JacConfigError):
        build_guest_gru(model="")


def test_guest_toolset_has_all_allowed_tools():
    """Every allowed read-only tool must be present and callable."""
    agent = _agent()
    names = _tool_names(agent)
    missing = _ALLOWED - names
    assert not missing, f"guest is missing allowed tools: {sorted(missing)}"


def test_guest_toolset_excludes_all_forbidden_tools():
    """The security promise: NO forbidden tools leak into the guest."""
    agent = _agent()
    names = _tool_names(agent)
    leaked = _FORBIDDEN & names
    assert not leaked, (
        f"guest toolset leaks forbidden tools: {sorted(leaked)}. "
        "Either build_guest_gru is including the wrong capability, or "
        "FilesystemCapability / SearchCapability grew an unexpected tool."
    )


def test_guest_physically_excludes_writes():
    """Write/edit are structurally absent from the guest toolset (R3).

    The guest builds ``FilesystemCapability(allowed={"read_file",
    "list_dir"})`` so ``write_file`` / ``edit_file`` are never registered —
    not merely approval-blocked. The read-only guarantee no longer depends
    on "no approval handler installed", so it survives any future change to
    approval wiring. A network peer cannot reach a workspace-mutating tool
    because the tool isn't there.
    """
    agent = _agent()
    names = _tool_names(agent)
    assert "write_file" not in names
    assert "edit_file" not in names


def test_guest_toolset_size_is_bounded():
    """Sanity cap — guest shouldn't grow a sprawling tool surface
    accidentally. Today we expect exactly 4 read-only tools (read_file,
    list_dir, grep, glob); write/edit are filtered out (R3). Bump this
    assertion deliberately when the guest gains a new capability."""
    agent = _agent()
    names = _tool_names(agent)
    assert len(names) == 4, (
        f"expected exactly 4 guest tools, got {len(names)}: {sorted(names)}. "
        "If you added a capability to build_guest_gru, update this assertion "
        "in the same change."
    )
