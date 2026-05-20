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

import os
import stat
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from jac.errors import JacConfigError
from jac.profiles import Profile
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

        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            # already gone — idempotent
            pass


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
        try:
            DOTENV_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass  # Windows may not honor this; best-effort

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


def apply_profile_env(profile_name: str, profile: Profile) -> None:
    """Inject the profile's model + env + resolved secrets into ``os.environ``.

    Sets ``JAC_MODEL`` so :func:`jac.config.get_settings` picks it up via the
    normal env path. Raises :class:`JacConfigError` listing any missing
    required secrets so the user gets one actionable error, not N.
    """
    os.environ["JAC_MODEL"] = profile.model
    for k, v in profile.env.items():
        os.environ[k] = v

    missing: list[str] = []
    for key in profile.required_env_keys():
        if key in os.environ:
            continue
        value, _ = resolve(key)
        if value is None:
            missing.append(key)
        else:
            os.environ[key] = value

    if missing:
        listed = ", ".join(missing)
        first = missing[0]
        raise JacConfigError(
            f"profile {profile_name!r} requires {listed} but they aren't set. "
            f"Run `jac keys set {first}` to store, or export in your shell."
        )
