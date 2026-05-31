"""Surface-agnostic control plane over a live session (the SDK's Layer 2).

Every *runtime mutation* a surface can trigger — switch model, switch profile,
refresh toolsets, enable/disable/reload MCP servers, reload skills — lives here
once, as a verb on :class:`SessionController`. Before this module each surface
re-implemented the same work: the CLI REPL had ``_rebuild_gru`` + the
``RefreshToolsets`` rebuild inline, the web had a hand-copied ``_rebuild`` +
``mcp_set_knob_action``. They drifted (the web learned to pass ``model_override``
+ reset the settings cache; the CLI never did), and the web MCP toggle only
wrote a file without rebuilding Gru — so it did nothing until restart.

The controller wraps a :class:`~jac.runtime.bootstrap.SessionRuntime` and mutates
it **in place** (``gru``, ``driver.gru``, ``model_id``, ``active_profile``,
``profile_name``, plus the A2A capability's metadata). It returns a plain
:class:`ControlResult`; it does **not** render and does **not** emit on the bus —
slash commands run outside the renderer's turn loop, so each surface styles the
result its own way (the CLI prints a panel, the web emits an SSE frame).

A surface constructs one controller per session and reuses it::

    controller = SessionController(runtime)
    result = controller.switch_model("anthropic:claude-opus-4-8")
    if result.ok:
        ...render result.message / result.data...
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from jac.config import reset_settings_cache
from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.profiles_crud import get_profile
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.runtime.bootstrap import SessionRuntime, resolve_summarizer_model
from jac.runtime.gru import build_gru
from jac.runtime.tool_summarize import set_summarizer_model
from jac.secrets import (
    apply_ad_hoc_model_env,
    apply_profile_env,
    restore_env,
    snapshot_env,
)


@dataclass(frozen=True)
class ControlResult:
    """Outcome of a control-plane verb, rendered however the surface likes.

    Attributes:
        ok: whether the mutation succeeded. On ``False`` the runtime is
            unchanged (the env snapshot was rolled back).
        message: a plain human-readable summary — the surface styles it
            (CLI panel, web notice frame, …). Never pre-formatted markup.
        data: optional structured payload (e.g. ``{"model": ..., "profile": ...}``)
            for surfaces that update widgets rather than print prose.
    """

    ok: bool
    message: str
    data: dict[str, Any] | None = None


class SessionController:
    """Owns every in-place mutation of one live :class:`SessionRuntime`."""

    def __init__(self, runtime: SessionRuntime) -> None:
        self.runtime = runtime

    # ----- model / profile switching --------------------------------------

    def switch_model(self, model_id: str) -> ControlResult:
        """Switch to ``model_id`` ad-hoc, keeping the active profile (if any)."""
        model_id = (model_id or "").strip()
        if not model_id:
            return ControlResult(False, "empty model id")
        return self._rebuild(new_model_id=model_id, new_profile_name=self.runtime.profile_name)

    def switch_profile(self, name: str) -> ControlResult:
        """Switch to profile ``name``, binding its default (active-tier) model."""
        name = (name or "").strip()
        if not name:
            return ControlResult(False, "empty profile name")
        try:
            profile = get_profile(name)
        except JacConfigError as exc:
            return ControlResult(False, str(exc))
        return self._rebuild(new_model_id=profile.default_model(), new_profile_name=name)

    # ----- toolset refresh (no model/profile change) ----------------------

    def refresh_toolsets(self, *, note: str = "") -> ControlResult:
        """Rebuild Gru against the *current* model so a changed capability
        toolset (e.g. the reused :class:`MCPCapability`, whose ``get_toolset``
        reads its live catalog) is re-consulted. No env dance, no model switch.
        """
        rt = self.runtime
        if rt.model_id == "unknown":
            return ControlResult(
                False, "no model bound; cannot rebuild — set one with /model first"
            )
        try:
            gru = build_gru(
                model_override=rt.model_id,
                extra_capabilities=rt.persisted_capabilities,
                bus=rt.bus,
                summarizer_model=resolve_summarizer_model(rt.profile_name),
            )
        except JacConfigError as exc:
            return ControlResult(False, f"rebuild failed: {exc}")
        rt.gru = gru
        rt.driver.gru = gru
        return ControlResult(True, note or "toolsets refreshed")

    # ----- MCP knobs ------------------------------------------------------

    def set_mcp_enabled(self, name: str, enabled: bool) -> ControlResult:
        """Enable/disable one MCP server, persist it, and rebuild Gru.

        Centralizes what the CLI ``/mcp enable|disable`` did and what the web
        UI *failed* to do (it only wrote the file). The persisted flag lands in
        the owning ``mcp.json``'s ``jac`` block; the rebuild re-consults the
        live catalog so the change takes effect immediately, not on restart.
        """
        mcp = self.runtime.mcp_capability
        if mcp is None:
            return ControlResult(False, "MCP capability is not wired into this session")
        if name not in mcp.catalog.servers:
            available = ", ".join(sorted(mcp.catalog.servers)) or "(none)"
            return ControlResult(False, f"unknown MCP server {name!r} (available: {available})")
        verb = "enabled" if enabled else "disabled"
        if mcp.catalog.servers[name].knobs.enabled == enabled:
            return ControlResult(True, f"{name} is already {verb}")
        try:
            mcp.set_enabled(name, enabled)
        except OSError as exc:
            return ControlResult(False, f"could not persist change: {exc}")
        return self.refresh_toolsets(note=f"{verb} MCP server {name}")

    def reload_mcp(self) -> ControlResult:
        """Re-scan the MCP catalog from disk and rebuild Gru."""
        mcp = self.runtime.mcp_capability
        if mcp is None:
            return ControlResult(False, "MCP capability is not wired into this session")
        mcp.reload()
        n = len(mcp.catalog.enabled)
        return self.refresh_toolsets(
            note=f"reloaded MCP catalog ({n} server{'s' if n != 1 else ''} enabled)"
        )

    # ----- skills ---------------------------------------------------------

    def reload_skills(self) -> ControlResult:
        """Re-scan the skill catalog from disk.

        No Gru rebuild: skills are read live (the skills tool consults the
        capability's dict each call), so a reload is visible immediately.
        """
        skills = self.runtime.skills_capability
        if skills is None:
            return ControlResult(False, "skills capability is not wired into this session")
        skills.reload()
        n = len(skills.skills)
        return ControlResult(True, f"reloaded skills ({n} available)", data={"count": n})

    # ----- internal -------------------------------------------------------

    def _rebuild(self, *, new_model_id: str, new_profile_name: str | None) -> ControlResult:
        """Rebuild Gru for a new model/profile with snapshot/rollback on failure.

        Snapshot every env key either profile (or the new model's provider)
        could touch, apply the new env, reset the cached settings, and rebuild
        Gru against the **same** bus + persisted capabilities. On any failure
        (missing key, unknown model, bad profile) restore the snapshot and keep
        the running agent intact. The history, bus, and pending approvals are
        untouched — only the agent swaps.
        """
        rt = self.runtime
        new_profile: Profile | None = None
        if new_profile_name is not None:
            try:
                new_profile = get_profile(new_profile_name)
            except JacConfigError as exc:
                return ControlResult(False, str(exc))

        keys: set[str] = {"JAC_MODEL"}
        if rt.active_profile is not None:
            keys.update(rt.active_profile.env)
            keys.update(rt.active_profile.required_env_keys())
        if new_profile is not None:
            keys.update(new_profile.env)
            keys.update(new_profile.required_env_keys())
        keys.update(get_provider_registry().required_env_for_prefix(provider_prefix(new_model_id)))
        snap = snapshot_env(list(keys))

        try:
            if new_profile_name is None:
                apply_ad_hoc_model_env(new_model_id)
            elif new_profile is not None and new_profile_name != rt.profile_name:
                apply_profile_env(new_profile_name, new_profile)
                if new_model_id != new_profile.default_model():
                    os.environ["JAC_MODEL"] = new_model_id
            else:
                apply_ad_hoc_model_env(new_model_id)
            reset_settings_cache()
            resolved = resolve_summarizer_model(
                new_profile_name if new_profile_name is not None else rt.profile_name
            )
            # Pass model_override explicitly: get_settings() is cached, so the
            # env change alone wouldn't be seen by build_gru's fallback.
            new_gru = build_gru(
                model_override=new_model_id,
                extra_capabilities=rt.persisted_capabilities,
                bus=rt.bus,
                summarizer_model=resolved,
            )
            set_summarizer_model(resolved)
        except Exception as exc:
            # Any switch failure rolls back to the prior agent: JacConfigError
            # (missing key), pydantic-ai UserError (unknown model), or a provider
            # construction error. Snapshot/restore keeps the running agent intact.
            restore_env(snap)
            reset_settings_cache()
            return ControlResult(False, str(exc))

        target_profile = new_profile if new_profile is not None else rt.active_profile
        target_profile_name = new_profile_name if new_profile_name is not None else rt.profile_name

        rt.gru = new_gru
        rt.driver.gru = new_gru
        rt.model_id = new_model_id
        rt.active_profile = target_profile
        rt.profile_name = target_profile_name

        a2a = rt.a2a_capability
        if a2a is not None:
            a2a.model = new_model_id
            a2a.profile_name = target_profile_name
            if target_profile is not None:
                a2a.retention_days = target_profile.a2a.context_retention_days
                a2a.allow_private_peers = target_profile.a2a.allow_private_peers
                a2a.profile_peers.clear()
                a2a.profile_peers.update(target_profile.a2a.peers)

        return ControlResult(
            True,
            f"switched to {new_model_id}",
            data={"model": new_model_id, "profile": target_profile_name},
        )
