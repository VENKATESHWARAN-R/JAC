"""Read-side view-model assembly for the web control panel (D48).

Pure functions: each gathers data from the existing management APIs
(:mod:`jac.profiles_crud`, :mod:`jac.providers.registry`, :mod:`jac.secrets`,
:class:`jac.runtime.session.Session`) into plain dicts the Jinja templates
render. No Starlette imports here, so this stays unit-testable without a server.

Security note: secret *values* are never surfaced — only a set/missing status
and the resolution source. The panel can write a key but never echoes one back.
"""

from __future__ import annotations

import json

from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles_crud import get_default_profile_name, list_profiles
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


def _project_token_total() -> int | None:
    """Best-effort sum of all tokens logged to ``usage.jsonl`` for this scope.

    Returns ``None`` if the ledger is absent or unreadable — the overview just
    omits the figure rather than failing the page.
    """
    usage_file = paths.project_usage_file()
    if not usage_file.is_file():
        return 0
    total = 0
    try:
        for line in usage_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            total += int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return total


def overview_context() -> dict[str, object]:
    """Top-level dashboard: scope, defaults, backend, model, and counts."""
    settings = get_settings()
    try:
        profile_count: int | None = len(list_profiles())
    except JacConfigError:
        # Legacy/malformed config — the Profiles page renders the actual error.
        profile_count = None

    return {
        "scope": workspace_scope(),
        "default_profile": get_default_profile_name(),
        "secrets_backend": settings.secrets.backend,
        "model": settings.model,
        "profile_count": profile_count,
        "session_count": len(Session.list_ids()),
        "token_total": _project_token_total(),
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


def sessions_context() -> dict[str, object]:
    """Sessions for the current scope, newest first."""
    summaries = Session.list_summaries()
    rows = [
        {
            "id": s.session_id,
            "count": s.message_count,
            "created": s.created.isoformat(sep=" ", timespec="seconds") if s.created else None,
        }
        for s in reversed(summaries)
    ]
    return {"sessions": rows, "scope": workspace_scope()}
