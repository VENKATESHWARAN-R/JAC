"""Profile management — named bundles of tiered models + non-secret env.

A profile lives in ``~/.jac/config.yaml`` under ``profiles.<name>``. The
CLI's ``--profile``/``-p`` flag (or ``default_profile``) picks which one is
active for a session. Each profile defines one or more **tiers** (typically
``small`` / ``medium`` / ``large``) — each tier is an ordered list of
fully-qualified model ids; the first entry is the tier's default. Required
credentials are inferred from the union of provider prefixes across every
tier model and resolved at startup by :mod:`jac.secrets`.

Profile names must match ``[a-z0-9-]+`` so they're safe in shell args and
file paths. See docs/architecture.md §11 D13 (names) and D22 (tiered models).
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from jac.errors import JacConfigError
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.workspace import paths

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_TIER_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


def validate_profile_name(name: str) -> None:
    """Raise :class:`JacConfigError` if ``name`` isn't a valid profile name."""
    if not _NAME_RE.fullmatch(name):
        raise JacConfigError(
            f"invalid profile name {name!r}. "
            "Use lowercase letters, digits, and hyphens only; cannot start or end with a hyphen."
        )


class Profile(BaseModel):
    """A named bundle of tiered models + non-secret env + (optionally) explicit secret requirements."""

    tiers: dict[str, list[str]]
    """Mapping ``tier_name -> [model_id, ...]``. Each list's first entry is that
    tier's default. Conventional tier names are ``small`` / ``medium`` / ``large``
    but any lowercase identifier works."""

    active_tier: str
    """The tier Gru uses by default on REPL start. Must be a key in ``tiers``."""

    env: dict[str, str] = Field(default_factory=dict)
    """Non-secret env vars injected when this profile activates (e.g. ``OLLAMA_BASE_URL``)."""

    requires_env: list[str] | None = None
    """If set, the explicit list of secret env vars the profile needs.
    If ``None``, JAC infers them from the union of provider prefixes across
    every tier model via the provider catalog (:mod:`jac.providers.registry`)."""

    @model_validator(mode="after")
    def _validate_shape(self) -> Profile:
        if not self.tiers:
            raise ValueError("profile must define at least one tier")
        for tier_name, models in self.tiers.items():
            if not _TIER_NAME_RE.fullmatch(tier_name):
                raise ValueError(
                    f"invalid tier name {tier_name!r}; use lowercase letters, digits, "
                    "underscores, and hyphens (cannot start or end with a separator)"
                )
            if not models:
                raise ValueError(f"tier {tier_name!r} must list at least one model")
            for model in models:
                if not isinstance(model, str) or not model.strip():
                    raise ValueError(f"tier {tier_name!r} contains an empty or non-string model id")
        if self.active_tier not in self.tiers:
            raise ValueError(
                f"active_tier {self.active_tier!r} is not defined in tiers "
                f"(have: {', '.join(sorted(self.tiers))})"
            )
        return self

    def default_model(self) -> str:
        """The active tier's default model — used as ``JAC_MODEL`` on activation."""
        return self.tiers[self.active_tier][0]

    def tier(self, name: str) -> list[str]:
        """Return the model list for ``name``, or raise :class:`JacConfigError`."""
        if name not in self.tiers:
            raise JacConfigError(
                f"profile has no tier {name!r}; available: {', '.join(sorted(self.tiers))}"
            )
        return list(self.tiers[name])

    def all_models(self) -> list[str]:
        """Every model id mentioned across all tiers, preserving first-seen order."""
        seen: list[str] = []
        for models in self.tiers.values():
            for m in models:
                if m not in seen:
                    seen.append(m)
        return seen

    def required_env_keys(self) -> list[str]:
        """Union of secret env vars required by every tier model."""
        if self.requires_env is not None:
            return list(self.requires_env)
        registry = get_provider_registry()
        seen: list[str] = []
        for model in self.all_models():
            for key in registry.required_env_for_prefix(provider_prefix(model)):
                if key not in seen:
                    seen.append(key)
        return seen


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


# ---------- old-shape detection + migration ----------


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


# ---------- profile CRUD ----------


def list_profiles() -> dict[str, Profile]:
    """Return all profiles defined in ``~/.jac/config.yaml``.

    Pre-D22 profiles (top-level ``model:``) raise :class:`JacConfigError` with
    a pointer to ``jac init`` for migration — fail-first, no silent rewrite.
    """
    raw = _load_raw_config()
    profiles_raw = raw.get("profiles", {}) or {}
    if not isinstance(profiles_raw, dict):
        raise JacConfigError("`profiles` in config.yaml must be a mapping")

    old_shape = [name for name, payload in profiles_raw.items() if _is_old_shape(payload)]
    if old_shape:
        raise JacConfigError(
            f"profile(s) {', '.join(repr(n) for n in old_shape)} use the pre-D22 "
            "`model:` schema. Run `jac init` to migrate (auto-rewrites as a single "
            "`medium` tier) or hand-edit ~/.jac/config.yaml — see CLAUDE.md "
            "'Profiles & secrets'."
        )

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
