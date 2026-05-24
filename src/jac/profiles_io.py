"""Profile YAML I/O + pre-D22 schema migration.

Raw read/write of ``~/.jac/config.yaml`` plus the helpers that detect
and rewrite legacy ``model:`` profiles into the tiered schema. The CRUD
layer in :mod:`jac.profiles_crud` builds on top of these.

Kept separate from the schema (:mod:`jac.profiles`) so the model
classes stay import-light, and from the CRUD (:mod:`jac.profiles_crud`)
so YAML / migration logic is a self-contained concern.
"""

from __future__ import annotations

from typing import Any

import yaml

from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.workspace import paths

_CONFIG_HEADER = (
    "# JAC user-level configuration.\n"
    '# See CLAUDE.md "Configuration & workspace" for the layering rules.\n'
    "# Edit by hand or via `jac init`, `jac profiles use NAME`, `jac profiles remove NAME`.\n\n"
)


def _load_raw_config() -> dict[str, Any]:
    if not paths.USER_CONFIG_FILE.is_file():
        return {}
    text = paths.USER_CONFIG_FILE.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _save_raw_config(data: dict[str, Any]) -> None:
    paths.USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    paths.USER_CONFIG_FILE.write_text(_CONFIG_HEADER + body, encoding="utf-8")


def _is_old_shape(payload: Any) -> bool:
    """A profile is in the pre-D22 shape if it has a top-level ``model:`` and no ``tiers:``."""
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("model"), str)
        and "tiers" not in payload
    )


def detect_old_profiles() -> list[str]:
    """Return the names of profiles still on the pre-D22 ``model:`` schema."""
    raw = _load_raw_config()
    profiles_raw = raw.get("profiles", {}) or {}
    if not isinstance(profiles_raw, dict):
        return []
    return [name for name, payload in profiles_raw.items() if _is_old_shape(payload)]


def migrate_old_profiles() -> list[str]:
    """Rewrite any pre-D22 ``model:`` profiles as ``tiers: {medium: [model]}``.

    Returns the list of profile names that were migrated. Idempotent — running
    twice is a no-op the second time.
    """
    raw = _load_raw_config()
    profiles_raw = raw.get("profiles", {}) or {}
    if not isinstance(profiles_raw, dict):
        return []

    migrated: list[str] = []
    for name, payload in list(profiles_raw.items()):
        if not _is_old_shape(payload):
            continue
        old_model = payload["model"]
        new_payload: dict[str, Any] = {
            "tiers": {"medium": [old_model]},
            "active_tier": "medium",
        }
        if payload.get("env"):
            new_payload["env"] = payload["env"]
        if payload.get("requires_env") is not None:
            new_payload["requires_env"] = payload["requires_env"]
        profiles_raw[name] = new_payload
        migrated.append(name)

    if migrated:
        raw["profiles"] = profiles_raw
        _save_raw_config(raw)
    return migrated


def profile_to_yaml(profile: Profile) -> str:
    """Serialize a single profile to YAML (just the inner block).

    Field order is preserved (``tiers`` first, then ``active_tier``, then
    optional ``env`` / ``requires_env``) so the round-trip with
    :func:`load_profile_from_yaml` is stable for the editor flow.
    ``env`` and ``requires_env`` are omitted when they're at their defaults
    so the YAML the user opens isn't littered with empty placeholders.
    """
    payload = profile.model_dump(exclude_none=True, exclude_defaults=True)
    return yaml.safe_dump(payload, default_flow_style=False, sort_keys=False)


def load_profile_from_yaml(text: str) -> Profile:
    """Parse + validate YAML into a :class:`Profile`.

    Raises:
        JacConfigError: on YAML syntax errors or schema validation failures.
            The original validator message is surfaced so the user can see
            exactly what went wrong (e.g. ``active_tier 'foo' not defined
            in tiers``).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise JacConfigError(f"invalid YAML: {exc}") from exc
    if data is None:
        raise JacConfigError("profile YAML is empty")
    if not isinstance(data, dict):
        raise JacConfigError("profile YAML must be a mapping at the top level")
    try:
        return Profile.model_validate(data)
    except Exception as exc:
        raise JacConfigError(f"profile is malformed: {exc}") from exc
