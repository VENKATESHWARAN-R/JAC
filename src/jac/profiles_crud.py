"""Profile CRUD — list/get/add/remove/set-default + active-profile resolution.

Reads and writes ``~/.jac/config.yaml`` via :mod:`jac.profiles_io`. Every
mutator validates names with :func:`jac.profiles.validate_profile_name` and
persists atomically via :func:`jac.profiles_io._save_raw_config`.

Kept separate from the schema (:mod:`jac.profiles`) so the model classes
remain import-light, and from raw I/O (:mod:`jac.profiles_io`) so the YAML
layer can change without touching call sites.
"""

from __future__ import annotations

from jac.errors import JacConfigError
from jac.profiles import Profile, validate_profile_name
from jac.profiles_io import _is_old_shape, _load_raw_config, _save_raw_config


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
    profiles[name] = profile.model_dump(exclude_none=True, exclude_defaults=True)
    if set_default or "default_profile" not in raw:
        raw["default_profile"] = name
    _save_raw_config(raw)


def remove_profile(name: str) -> None:
    raw = _load_raw_config()
    profiles = raw.get("profiles", {}) or {}
    if name not in profiles:
        raise JacConfigError(f"no profile named {name!r}")
    del profiles[name]
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
