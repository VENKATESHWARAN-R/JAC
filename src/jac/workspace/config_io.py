"""Read / modify / write the non-profile config groups, scope-aware.

Profiles own their slice of ``config.yaml`` via :mod:`jac.profiles_io`; this
module owns the *other* groups — ``compaction``, ``budget``, ``cost``,
``secrets`` (and the top-level ``model``) — which, unlike profiles, can be
written to **either** scope:

- ``project`` → ``<repo>/.agents/config.yaml``
- ``user``    → ``~/.jac/config.yaml``

A read-modify-write preserves every other key in the file (profiles included),
so writing a config group never clobbers profiles and vice-versa.

:func:`field_origin` reports which layer currently *supplies* a field, replaying
the **same** precedence order as :mod:`jac.workspace.config_loader` so the web's
source badges can't drift from the real resolution. CLI/init kwargs are not a
persisted layer, so they're out of scope here (the web can't set them anyway).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml

from jac.workspace import paths

Origin = Literal["env", "dotenv", "project", "user", "defaults", "code"]

# Precedence rank (lower = higher precedence), matching config_loader's stack.
# Used to decide whether a field is "locked" for a chosen edit scope: a value is
# locked when a strictly-higher-precedence layer than the edit scope supplies it.
_RANK: dict[str, int] = {"env": 0, "dotenv": 1, "project": 2, "user": 3, "defaults": 4, "code": 5}

_HEADER = (
    "# JAC configuration (layered: project > user > package defaults).\n"
    '# See CLAUDE.md "Configuration & workspace". Edited by `jac` and the web UI;\n'
    "# safe to hand-edit. Profiles in this file are managed by `jac profiles`.\n\n"
)


def _scope_path(scope: str) -> Path:
    return paths.project_config_file() if scope == "project" else paths.USER_CONFIG_FILE


def load_scope_raw(scope: str) -> dict[str, Any]:
    """Return the raw mapping for one scope's ``config.yaml`` (``{}`` if absent)."""
    path = _scope_path(scope)
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def save_scope_raw(scope: str, data: dict[str, Any]) -> None:
    """Atomically write one scope's ``config.yaml`` (with the shared header)."""
    path = _scope_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    path.write_text(_HEADER + body, encoding="utf-8")


def scope_raw_text(scope: str) -> str:
    """The on-disk text for the scope (for the raw-YAML escape hatch editor)."""
    path = _scope_path(scope)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def set_value(scope: str, group: str | None, field: str, value: Any) -> None:
    """Set ``group.field`` (or top-level ``field`` when ``group`` is None) in scope."""
    raw = load_scope_raw(scope)
    if group is None:
        raw[field] = value
    else:
        sub = raw.get(group)
        if not isinstance(sub, dict):
            sub = {}
        sub[field] = value
        raw[group] = sub
    save_scope_raw(scope, raw)


def unset_value(scope: str, group: str | None, field: str) -> None:
    """Remove ``group.field`` from scope (reverting it to an inherited layer)."""
    raw = load_scope_raw(scope)
    target = raw if group is None else raw.get(group)
    if isinstance(target, dict) and field in target:
        del target[field]
        if group is not None and isinstance(target, dict) and not target:
            raw.pop(group, None)  # drop a now-empty group block
        save_scope_raw(scope, raw)


def _env_key(group: str | None, field: str) -> str:
    return f"JAC_{field.upper()}" if group is None else f"JAC_{group.upper()}__{field.upper()}"


def _dotenv_keys() -> set[str]:
    path = Path(".env")
    if not path.is_file():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def _yaml_has(data: dict[str, Any], group: str | None, field: str) -> bool:
    if group is None:
        return field in data
    sub = data.get(group)
    return isinstance(sub, dict) and field in sub


def _defaults_raw() -> dict[str, Any]:
    path = paths.package_defaults_file()
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def field_origin(group: str | None, field: str) -> Origin:
    """Which layer currently supplies ``group.field`` (highest precedence first)."""
    key = _env_key(group, field)
    if key in os.environ:
        return "env"
    if key in _dotenv_keys():
        return "dotenv"
    if _yaml_has(load_scope_raw("project"), group, field):
        return "project"
    if _yaml_has(load_scope_raw("user"), group, field):
        return "user"
    if _yaml_has(_defaults_raw(), group, field):
        return "defaults"
    return "code"


def is_locked_for(scope: str, origin: Origin) -> bool:
    """True when ``origin`` outranks ``scope`` — editing ``scope`` won't take effect."""
    return _RANK[origin] < _RANK[scope]
