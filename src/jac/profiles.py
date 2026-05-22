"""Profile management — named bundles of model + non-secret env.

A profile lives in ``~/.jac/config.yaml`` under ``profiles.<name>``. The
CLI's ``--profile``/``-p`` flag (or ``default_profile``) picks which one is
active for a session. Required credentials are inferred from the model's
provider prefix and resolved at startup by :mod:`jac.secrets`.

Profile names must match ``[a-z0-9-]+`` so they're safe in shell args and
file paths. See docs/architecture.md §11 D13.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field

from jac.errors import JacConfigError
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.workspace import paths

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def validate_profile_name(name: str) -> None:
    """Raise :class:`JacConfigError` if ``name`` isn't a valid profile name."""
    if not _NAME_RE.fullmatch(name):
        raise JacConfigError(
            f"invalid profile name {name!r}. "
            "Use lowercase letters, digits, and hyphens only; cannot start or end with a hyphen."
        )


class Profile(BaseModel):
    """A named bundle of model + non-secret env + (optionally) explicit secret requirements."""

    model: str
    """Model identifier passed to ``pydantic_ai.Agent``, e.g. ``anthropic:claude-sonnet-4-5``."""

    env: dict[str, str] = Field(default_factory=dict)
    """Non-secret env vars injected when this profile activates (e.g. ``OLLAMA_BASE_URL``)."""

    requires_env: list[str] | None = None
    """If set, the explicit list of secret env vars the profile needs.
    If ``None``, JAC infers them from the model's provider prefix via the
    provider catalog (:mod:`jac.providers.registry`)."""

    def required_env_keys(self) -> list[str]:
        if self.requires_env is not None:
            return list(self.requires_env)
        return get_provider_registry().required_env_for_prefix(provider_prefix(self.model))


# ---------- raw YAML I/O ----------

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


# ---------- profile CRUD ----------


def list_profiles() -> dict[str, Profile]:
    """Return all profiles defined in ``~/.jac/config.yaml``."""
    raw = _load_raw_config()
    profiles_raw = raw.get("profiles", {}) or {}
    if not isinstance(profiles_raw, dict):
        raise JacConfigError("`profiles` in config.yaml must be a mapping")
    result: dict[str, Profile] = {}
    for name, payload in profiles_raw.items():
        validate_profile_name(name)
        try:
            result[name] = Profile.model_validate(payload or {})
        except Exception as exc:
            raise JacConfigError(f"profile {name!r} is malformed: {exc}") from exc
    return result


def get_default_profile_name() -> str | None:
    raw = _load_raw_config()
    name = raw.get("default_profile")
    if name is None:
        return None
    if not isinstance(name, str):
        raise JacConfigError("`default_profile` must be a string")
    return name


def get_profile(name: str) -> Profile:
    validate_profile_name(name)
    profiles = list_profiles()
    if name not in profiles:
        available = ", ".join(profiles) if profiles else "(none)"
        raise JacConfigError(
            f"no profile named {name!r}. Available: {available}. Run `jac init` to add one."
        )
    return profiles[name]


def set_default_profile(name: str) -> None:
    profiles = list_profiles()
    if name not in profiles:
        available = ", ".join(profiles) if profiles else "(none)"
        raise JacConfigError(
            f"can't set default to {name!r}: no such profile. Available: {available}."
        )
    raw = _load_raw_config()
    raw["default_profile"] = name
    _save_raw_config(raw)


def add_or_update_profile(name: str, profile: Profile, *, set_default: bool = False) -> None:
    """Persist a profile. Idempotent — overwrites if ``name`` already exists."""
    validate_profile_name(name)
    raw = _load_raw_config()
    profiles = raw.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise JacConfigError("`profiles` in config.yaml must be a mapping")
    profiles[name] = profile.model_dump(exclude_none=True, exclude_defaults=False)
    if set_default or "default_profile" not in raw:
        raw["default_profile"] = name
    _save_raw_config(raw)


def remove_profile(name: str) -> None:
    raw = _load_raw_config()
    profiles = raw.get("profiles", {}) or {}
    if name not in profiles:
        raise JacConfigError(f"no profile named {name!r}")
    del profiles[name]
    # If we just removed the default, pick another (or unset).
    if raw.get("default_profile") == name:
        if profiles:
            raw["default_profile"] = next(iter(profiles))
        else:
            raw.pop("default_profile", None)
    _save_raw_config(raw)


def resolve_active_profile_name(cli_name: str | None) -> str:
    """Decide which profile to activate given an optional CLI override."""
    if cli_name is not None:
        return cli_name
    default = get_default_profile_name()
    if default is None:
        profiles = list_profiles()
        if not profiles:
            raise JacConfigError("no profiles configured. Run `jac init` to set one up.")
        raise JacConfigError(
            "no `default_profile` set. "
            f"Run `jac profiles use <name>` (available: {', '.join(profiles)})."
        )
    return default
