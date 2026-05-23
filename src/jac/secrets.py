"""Secrets storage backends + the resolver that feeds ``os.environ``.

Three backends, picked by ``secrets.backend`` in ``~/.jac/config.yaml``:

- ``keyring`` *(default)* — OS-native keychain via the ``keyring`` library.
- ``dotenv`` — plaintext at ``~/.jac/.env``, ``chmod 600``.
- ``env-only`` — JAC stores nothing; only reads process environment.

Resolution at runtime (highest → lowest precedence):

1. Process environment (whatever the shell exported).
2. Configured backend.

Missing values are fail-first with an actionable message — never silent.
"""

from __future__ import annotations

import contextlib
import os
import stat
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.providers.registry import get_provider_registry, provider_prefix
from jac.workspace import paths

KEYRING_SERVICE = "jac"
"""All keys are stored under this service name in the OS keyring."""

DOTENV_FILE: Path = paths.USER_WORKSPACE / ".env"
"""Where the dotenv backend persists keys."""

SecretBackendName = Literal["keyring", "dotenv", "env-only"]


# ---------- backends ----------


class SecretBackend(ABC):
    name: SecretBackendName

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    def unset(self, key: str) -> None: ...


class KeyringBackend(SecretBackend):
    """OS keyring (macOS Keychain / libsecret / Windows Credential Manager)."""

    name: SecretBackendName = "keyring"

    def get(self, key: str) -> str | None:
        import keyring  # lazy: avoid import cost when other backends are in use

        return keyring.get_password(KEYRING_SERVICE, key)

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, key, value)

    def unset(self, key: str) -> None:
        import keyring
        import keyring.errors

        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(KEYRING_SERVICE, key)


class DotenvBackend(SecretBackend):
    """Plaintext key/value store at ``~/.jac/.env``."""

    name: SecretBackendName = "dotenv"

    def _read(self) -> dict[str, str]:
        if not DOTENV_FILE.is_file():
            return {}
        result: dict[str, str] = {}
        for raw_line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            result[k.strip()] = v
        return result

    def _write(self, data: dict[str, str]) -> None:
        DOTENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# JAC user-level secrets — managed by `jac keys`.",
            "# Do not commit this file. JAC enforces chmod 600 on write.",
            "",
        ]
        for k in sorted(data):
            lines.append(f"{k}={data[k]}")
        DOTENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):  # Windows may not honor this; best-effort
            DOTENV_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600

    def get(self, key: str) -> str | None:
        return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def unset(self, key: str) -> None:
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)


class EnvOnlyBackend(SecretBackend):
    """Read-through to process env; refuses to store anything."""

    name: SecretBackendName = "env-only"

    def get(self, key: str) -> str | None:
        return os.environ.get(key)

    def set(self, key: str, value: str) -> None:
        raise JacConfigError(
            "secrets backend is `env-only`; JAC won't store credentials. "
            "Set them in your shell, or run `jac init` to choose a different backend."
        )

    def unset(self, key: str) -> None:
        raise JacConfigError("secrets backend is `env-only`; nothing to unset.")


# ---------- factory + resolver ----------


def get_backend(name: SecretBackendName | None = None) -> SecretBackend:
    """Return a secret backend by name; default = current config setting."""
    if name is None:
        from jac.config import get_settings

        name = get_settings().secrets.backend
    if name == "keyring":
        return KeyringBackend()
    if name == "dotenv":
        return DotenvBackend()
    if name == "env-only":
        return EnvOnlyBackend()
    raise JacConfigError(f"unknown secrets backend: {name!r}")


def resolve(key: str) -> tuple[str | None, str]:
    """Return ``(value, source)`` for ``key``.

    ``source`` is one of ``"env"``, the backend name, or ``"missing"``.
    Process env always wins so direnv / 1Password CLI / CI overrides take
    precedence over stored values.
    """
    if key in os.environ:
        return os.environ[key], "env"
    backend = get_backend()
    value = backend.get(key)
    if value is not None:
        return value, backend.name
    return None, "missing"


def snapshot_env(keys: list[str]) -> dict[str, str | None]:
    """Capture each key's current value in ``os.environ``.

    Returns a mapping where unset keys map to ``None`` and set keys map to
    their string value. Pair with :func:`restore_env` to roll back partial
    mutations after a failed profile/model switch.

    Used by the REPL's rebuild path so ``/profile NAME`` and
    ``/model PROVIDER:ID`` don't leave half-applied state when credentials
    are missing or the YAML is malformed.
    """
    return {k: os.environ.get(k) for k in keys}


def restore_env(snapshot: dict[str, str | None]) -> None:
    """Inverse of :func:`snapshot_env`. Idempotent.

    For each ``(key, value)`` in the snapshot:
    - If ``value`` is ``None``, the key is removed from ``os.environ`` (if present).
    - Otherwise the key is set to ``value``.
    """
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _resolve_env_keys(keys: list[str]) -> list[str]:
    """Resolve each key into ``os.environ`` from the configured backend.

    Returns the names that couldn't be resolved anywhere — caller decides
    whether that's fatal.
    """
    missing: list[str] = []
    for key in keys:
        if key in os.environ:
            continue
        value, _ = resolve(key)
        if value is None:
            missing.append(key)
        else:
            os.environ[key] = value
    return missing


def resolve_optional_keys(keys: list[str]) -> None:
    """Best-effort resolve ``keys`` from the configured backend into ``os.environ``.

    Mirrors :func:`_resolve_env_keys` but **never raises** on missing keys —
    callers use this for keys that enable optional features (e.g.
    ``TAVILY_API_KEY`` upgrades ``web_search`` from DDG to Tavily). The REPL
    runs this once at startup so users who stored the key via ``jac keys
    set`` get it auto-injected before any tool call.
    """
    _resolve_env_keys(keys)


def apply_profile_env(profile_name: str, profile: Profile) -> None:
    """Inject the profile's default model + env + resolved secrets into ``os.environ``.

    Sets ``JAC_MODEL`` to ``profile.default_model()`` (the active tier's first
    entry — D22) so :func:`jac.config.get_settings` picks it up via the
    normal env path. Resolves the **union** of secrets across all tier models
    so tier-switching mid-session never hits a missing key. Raises
    :class:`JacConfigError` listing any missing required secrets so the user
    gets one actionable error, not N.
    """
    os.environ["JAC_MODEL"] = profile.default_model()
    for k, v in profile.env.items():
        os.environ[k] = v

    missing = _resolve_env_keys(profile.required_env_keys())
    if missing:
        listed = ", ".join(missing)
        first = missing[0]
        raise JacConfigError(
            f"profile {profile_name!r} requires {listed} but they aren't set. "
            f"Run `jac keys set {first}` to store, or export in your shell."
        )


def apply_ad_hoc_model_env(model: str) -> None:
    """Activate a one-shot ``--model PROVIDER:ID`` override.

    Sets ``JAC_MODEL`` and best-effort resolves the secrets required by the
    model's provider prefix. Used by the ``--model`` CLI flag and by the
    in-REPL ``/model PROVIDER:ID`` slash command (Phase 1.7.c PR3).

    Missing secrets are reported as one actionable error.
    """
    os.environ["JAC_MODEL"] = model
    required = get_provider_registry().required_env_for_prefix(provider_prefix(model))
    missing = _resolve_env_keys(required)
    if missing:
        listed = ", ".join(missing)
        first = missing[0]
        raise JacConfigError(
            f"model {model!r} requires {listed} but they aren't set. "
            f"Run `jac keys set {first}` to store, or export in your shell."
        )
