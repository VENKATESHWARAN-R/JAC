"""Read-side view-model assembly for the web control panel (D48).

Pure functions: each gathers data from the existing management APIs
(:mod:`jac.profiles_crud`, :mod:`jac.providers.registry`, :mod:`jac.secrets`,
:class:`jac.runtime.session.Session`) into plain dicts the Jinja templates
render. No Starlette imports here, so this stays unit-testable without a server.

Security note: secret *values* are never surfaced — only a set/missing status
and the resolution source. The panel can write a key but never echoes one back.
"""

from __future__ import annotations

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles_crud import get_default_profile_name, get_profile, list_profiles
from jac.profiles_io import profile_to_yaml
from jac.providers.registry import get_provider_registry
from jac.runtime.session import Session
from jac.secrets import resolve
from jac.workspace import paths

# Optional feature keys that aren't tied to a model provider but still benefit
# from being managed in the panel (e.g. TAVILY_API_KEY upgrades web_search).
_OPTIONAL_FEATURE_KEYS = ["TAVILY_API_KEY"]


def workspace_scope() -> dict[str, object]:
    """Where this server reads/writes state: a named project, or the global pool."""
    in_project = paths.in_project()
    return {
        "in_project": in_project,
        "label": "project" if in_project else "global",
        "root": str(paths.project_root()) if in_project else str(paths.USER_WORKSPACE),
        "sessions_dir": str(paths.project_sessions_dir()),
    }


def profiles_context() -> dict[str, object]:
    """All profiles with their tiers + default flag, plus a round-trip YAML blob.

    On a pre-D22 / malformed config the CRUD layer raises ``JacConfigError``;
    we surface its message so the page can tell the user to run ``jac init``
    rather than 500.
    """
    try:
        profiles = list_profiles()
    except JacConfigError as exc:
        return {"error": str(exc), "profiles": [], "default": None}

    default = get_default_profile_name()
    rows = []
    for name, profile in profiles.items():
        rows.append(
            {
                "name": name,
                "active_tier": profile.active_tier,
                "tiers": profile.tiers,
                "default_model": profile.default_model(),
                "is_default": name == default,
                "yaml": profile_to_yaml(profile),
            }
        )
    return {"error": None, "profiles": rows, "default": default}


def keys_context() -> dict[str, object]:
    """Credential status grouped by provider, plus optional feature keys.

    Each entry reports whether the key resolves and from where (``env`` vs the
    configured backend) — never the value itself.
    """
    settings = get_settings()
    backend = settings.secrets.backend
    registry = get_provider_registry()

    groups = []
    for provider_id, spec in registry.providers.items():
        keys = []
        for key in spec.required_env:
            _value, source = resolve(key)
            keys.append({"key": key, "set": source != "missing", "source": source})
        if keys:
            groups.append({"provider": provider_id, "prefix": spec.prefix, "entries": keys})

    optional = []
    for key in _OPTIONAL_FEATURE_KEYS:
        _value, source = resolve(key)
        optional.append({"key": key, "set": source != "missing", "source": source})

    return {
        "backend": backend,
        "writable": backend != "env-only",
        "groups": groups,
        "optional": optional,
    }


def sidebar_context() -> dict[str, object]:
    """Shared left-rail data rendered on *every* page (chat-centric nav).

    The session list (newest first, clickable into the chat), the active
    profile + the model it resolves to, and the workspace scope. Profile/model
    resolution fails soft: a fresh workspace with no usable profile just shows
    nothing rather than 500-ing the whole UI.
    """
    sessions = [
        {
            "id": s.session_id,
            "count": s.message_count,
            "created": s.created.strftime("%b %d · %H:%M") if s.created else s.session_id,
        }
        for s in reversed(Session.list_summaries())
    ]

    profile_name: str | None = None
    model: str | None = None
    try:
        profile_name = get_default_profile_name()
        if profile_name:
            model = get_profile(profile_name).default_model()
    except JacConfigError:
        profile_name = None
        model = None

    return {
        "sb_sessions": sessions,
        "sb_profile": profile_name,
        "sb_model": model,
        "sb_scope": workspace_scope(),
    }
