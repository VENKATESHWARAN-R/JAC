"""Write-side handlers for the web control panel (D48).

Each function takes already-parsed form values, performs one mutation through
the existing management APIs, and returns an :class:`ActionResult`. Kept free of
Starlette so the mutation logic is testable in isolation; ``server.py`` parses
the request, calls one of these, and turns the result into a redirect.

Every mutator catches :class:`JacConfigError` and reports it as a failed result
rather than raising — the panel always redirects back with a message, never 500s
on user error (a bad YAML profile, a missing key, the env-only backend).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass

import yaml
from pydantic import ValidationError

from jac.config import (
    BudgetSettings,
    CompactionSettings,
    CostSettings,
    SecretsSettings,
    get_settings,
    reset_settings_cache,
)
from jac.errors import JacConfigError
from jac.profiles import Profile
from jac.profiles_crud import (
    add_or_update_profile,
    get_default_profile_name,
    remove_profile,
    set_default_profile,
)
from jac.profiles_io import load_profile_from_yaml
from jac.runtime.session import Session
from jac.secrets import get_backend
from jac.workspace import config_io, paths


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def set_default_profile_action(name: str) -> ActionResult:
    name = name.strip()
    if not name:
        return ActionResult(False, "no profile name given")
    try:
        set_default_profile(name)
    except JacConfigError as exc:
        return ActionResult(False, str(exc))
    return ActionResult(True, f"default profile is now '{name}'")


def delete_profile_action(name: str) -> ActionResult:
    name = name.strip()
    if not name:
        return ActionResult(False, "no profile name given")
    try:
        remove_profile(name)
    except JacConfigError as exc:
        return ActionResult(False, str(exc))
    return ActionResult(True, f"deleted profile '{name}'")


def save_profile_action(name: str, yaml_text: str, *, set_default: bool = False) -> ActionResult:
    """Add or update a profile from a raw YAML block (the editor flow).

    Round-trips through :func:`load_profile_from_yaml` so the same validation the
    CLI applies (tiers present, ``active_tier`` defined, name shape) runs here.
    """
    name = name.strip()
    if not name:
        return ActionResult(False, "no profile name given")
    if not yaml_text.strip():
        return ActionResult(False, "profile YAML is empty")
    try:
        profile = load_profile_from_yaml(yaml_text)
        add_or_update_profile(name, profile, set_default=set_default)
    except JacConfigError as exc:
        return ActionResult(False, str(exc))
    return ActionResult(True, f"saved profile '{name}'")


def set_secret_action(key: str, value: str) -> ActionResult:
    key = key.strip()
    if not key:
        return ActionResult(False, "no key name given")
    if not value:
        return ActionResult(False, f"no value given for {key}")
    try:
        get_backend().set(key, value)
    except JacConfigError as exc:
        return ActionResult(False, str(exc))
    return ActionResult(True, f"stored {key}")


def unset_secret_action(key: str) -> ActionResult:
    key = key.strip()
    if not key:
        return ActionResult(False, "no key name given")
    try:
        get_backend().unset(key)
    except JacConfigError as exc:
        return ActionResult(False, str(exc))
    return ActionResult(True, f"removed {key}")


def delete_session_action(session_id: str) -> ActionResult:
    session_id = session_id.strip()
    if not session_id:
        return ActionResult(False, "no session id given")
    try:
        Session.delete(session_id)
    except JacConfigError as exc:
        return ActionResult(False, str(exc))
    return ActionResult(True, f"deleted session '{session_id}'")


# ---------- config groups (R1) ----------

_GROUP_MODELS = {
    "compaction": CompactionSettings,
    "budget": BudgetSettings,
    "cost": CostSettings,
    "secrets": SecretsSettings,
}


def _parse_config_value(ftype: str, raw: str) -> object:
    """Coerce a submitted form value by field type. Raises ValueError on a bad int."""
    raw = raw.strip()
    if ftype == "bool":
        return raw.lower() in ("true", "on", "1", "yes")
    if ftype == "int":
        return int(raw)
    if ftype == "int_opt":
        return None if raw == "" else int(raw)
    if ftype == "list":
        return [tok.strip() for tok in raw.split(",") if tok.strip()]
    return raw  # select / text


def config_set_action(
    scope: str, group: str, field: str, ftype: str, raw_value: str
) -> ActionResult:
    """Set one config field in one scope, validating against its pydantic model.

    A blank ``int_opt`` (e.g. an unlimited budget knob) unsets the field rather
    than writing ``null``. Everything else is validated by reconstructing the
    group's sub-model so a bad value (over the 512k ceiling, a non-literal
    backend, a non-positive budget) is rejected *before* it touches disk.
    """
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    if group not in _GROUP_MODELS:
        return ActionResult(False, f"unknown config group {group!r}")
    try:
        value = _parse_config_value(ftype, raw_value)
    except ValueError:
        return ActionResult(False, f"{group}.{field}: {raw_value!r} is not a whole number")

    if ftype == "int_opt" and value is None:
        config_io.unset_value(scope, group, field)
        reset_settings_cache()
        return ActionResult(True, f"unset {group}.{field}")

    # Validate by rebuilding the sub-model with the new value layered on current.
    current = getattr(get_settings(), group).model_dump()
    current[field] = value
    try:
        _GROUP_MODELS[group](**current)
    except ValidationError as exc:
        errs = exc.errors()
        msg = str(errs[0].get("msg", exc)) if errs else str(exc)
        msg = msg.removeprefix("Value error, ")
        return ActionResult(False, f"{group}.{field}: {msg}")
    except Exception as exc:  # non-pydantic value error
        return ActionResult(False, f"{group}.{field}: {exc}")

    config_io.set_value(scope, group, field, value)
    reset_settings_cache()
    return ActionResult(True, f"set {group}.{field} in {scope} config")


def config_unset_action(scope: str, group: str, field: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    config_io.unset_value(scope, group, field)
    reset_settings_cache()
    return ActionResult(True, f"reset {group}.{field} to inherited")


def config_raw_save_action(scope: str, text: str) -> ActionResult:
    """Write the scope's whole ``config.yaml`` verbatim (the escape hatch).

    Only structural validation here (parses to a mapping) — this is the power
    path; per-field validation lives in :func:`config_set_action`.
    """
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        return ActionResult(False, f"invalid YAML: {exc}")
    if data is not None and not isinstance(data, dict):
        return ActionResult(False, "config must be a mapping at the top level")
    config_io.save_scope_raw(scope, data or {})
    reset_settings_cache()
    return ActionResult(True, f"saved {scope} config.yaml")


# ---------- MCP servers (R3) ----------


def _mcp_path(scope: str):
    return paths.project_mcp_file() if scope == "project" else paths.USER_MCP_FILE


def _mcp_read(scope: str) -> dict:
    path = _mcp_path(scope)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _mcp_write(scope: str, data: dict) -> None:
    path = _mcp_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def mcp_set_knob_action(source: str, name: str, key: str, value_raw: str) -> ActionResult:
    """Flip one MCP server knob in its owning (source) file."""
    if source not in ("project", "user"):
        return ActionResult(False, f"bad source {source!r}")
    if key not in ("enabled", "defer", "requires_approval", "init_timeout"):
        return ActionResult(False, f"unknown knob {key!r}")
    data = _mcp_read(source)
    srv = data.setdefault("jac", {}).setdefault(name, {})
    if key == "init_timeout":
        try:
            srv[key] = float(value_raw)
        except ValueError:
            return ActionResult(False, "init_timeout must be a number")
    else:
        srv[key] = value_raw.lower() in ("true", "on", "1", "yes")
    _mcp_write(source, data)
    return ActionResult(True, f"{name}: {key} updated")


def mcp_save_server_action(scope: str, name: str, entry_json: str) -> ActionResult:
    """Create / replace one server's ``mcpServers`` entry from JSON."""
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    name = name.strip()
    if not name:
        return ActionResult(False, "no server name given")
    try:
        entry = json.loads(entry_json)
    except json.JSONDecodeError as exc:
        return ActionResult(False, f"invalid JSON: {exc}")
    if not isinstance(entry, dict):
        return ActionResult(False, "server entry must be a JSON object")
    if "command" not in entry and "url" not in entry:
        return ActionResult(False, "server needs a 'command' (stdio) or 'url' (http/sse)")
    data = _mcp_read(scope)
    data.setdefault("mcpServers", {})[name] = entry
    _mcp_write(scope, data)
    return ActionResult(True, f"saved MCP server '{name}'")


def mcp_delete_server_action(scope: str, name: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    data = _mcp_read(scope)
    data.get("mcpServers", {}).pop(name, None)
    data.get("jac", {}).pop(name, None)
    _mcp_write(scope, data)
    return ActionResult(True, f"deleted MCP server '{name}'")


def mcp_raw_save_action(scope: str, text: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        return ActionResult(False, f"invalid JSON: {exc}")
    if not isinstance(data, dict):
        return ActionResult(False, "mcp.json must be a JSON object")
    _mcp_write(scope, data)
    return ActionResult(True, f"saved {scope} mcp.json")


# ---------- A2A peers (R3) ----------


def _default_profile_or_error() -> tuple[str | None, str | None]:
    try:
        name = get_default_profile_name()
    except JacConfigError as exc:
        return None, str(exc)
    if not name:
        return None, "no default profile set — add one under Profiles first"
    return name, None


def _edit_default_profile_a2a(mutate) -> ActionResult:
    """Mutate the default profile's ``a2a`` block in user config.yaml, in place.

    Edits the raw config subtree rather than round-tripping the whole profile
    through ``model_dump`` — only the ``a2a`` block changes, the rest of the
    user's config is left alone. Validates the result via
    ``Profile.model_validate`` before writing. (The auth ``type:`` discriminator
    that ``exclude_defaults`` used to strip is now preserved at the model layer —
    see ``jac.profiles._AuthStrategy`` — so a full re-dump round-trips too.)
    """
    profile_name, err = _default_profile_or_error()
    if err:
        return ActionResult(False, err)
    raw = config_io.load_scope_raw("user")
    pdata = (raw.get("profiles") or {}).get(profile_name)
    if not isinstance(pdata, dict):
        return ActionResult(False, f"profile {profile_name!r} not found in user config")
    a2a = pdata.setdefault("a2a", {})
    result = mutate(a2a)
    if isinstance(result, ActionResult) and not result.ok:
        return result
    try:
        Profile.model_validate(pdata)  # peer-name shape, port range, etc.
    except Exception as exc:
        return ActionResult(False, str(exc))
    config_io.save_scope_raw("user", raw)
    return result if isinstance(result, ActionResult) else ActionResult(True, "saved")


def a2a_add_peer_action(name: str, url: str, token: str) -> ActionResult:
    """Add (or replace) a bearer/no-auth A2A peer on the default profile."""
    name, url = name.strip(), url.strip()
    if not name or not url:
        return ActionResult(False, "peer needs a name and a URL")

    def _mut(a2a: dict) -> ActionResult:
        peer: dict = {"url": url}
        if token.strip():
            peer["auth"] = {"type": "bearer", "token": token.strip()}
        a2a.setdefault("peers", {})[name] = peer
        return ActionResult(True, f"added peer '{name}'")

    return _edit_default_profile_a2a(_mut)


def a2a_remove_peer_action(name: str) -> ActionResult:
    name = name.strip()

    def _mut(a2a: dict) -> ActionResult:
        peers = a2a.get("peers") or {}
        if name not in peers:
            return ActionResult(False, f"no peer named '{name}'")
        del peers[name]
        return ActionResult(True, f"removed peer '{name}'")

    return _edit_default_profile_a2a(_mut)


def a2a_set_allow_private_action(value: str) -> ActionResult:
    allow = value.lower() in ("true", "on", "1", "yes")

    def _mut(a2a: dict) -> ActionResult:
        a2a["allow_private_peers"] = allow
        return ActionResult(True, f"allow_private_peers = {allow}")

    return _edit_default_profile_a2a(_mut)


# ---------- Skills (R4) ----------

_SKILL_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")


def skill_save_action(scope: str, name: str, text: str) -> ActionResult:
    """Write a SKILL.md under the user/project skills dir (folder = name)."""
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    name = name.strip()
    if not _SKILL_NAME_RE.match(name):
        return ActionResult(False, "name must be lowercase letters, digits, hyphens")
    if not text.strip():
        return ActionResult(False, "skill body is empty")
    if not text.lstrip().startswith("---"):
        return ActionResult(False, "a SKILL.md must start with a `---` YAML frontmatter block")
    base = paths.project_skills_dir() if scope == "project" else paths.USER_SKILLS_DIR
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return ActionResult(True, f"saved skill '{name}' — reload chat to load it")


def skill_delete_action(scope: str, name: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    base = paths.project_skills_dir() if scope == "project" else paths.USER_SKILLS_DIR
    skill_dir = base / name
    if not skill_dir.is_dir():
        return ActionResult(False, f"no skill '{name}' in {scope}")
    shutil.rmtree(skill_dir)
    return ActionResult(True, f"deleted skill '{name}'")


# ---------- Context & prompts (R4) ----------


def context_save_agents_action(scope: str, text: str) -> ActionResult:
    """Write the auto-loaded AGENTS.md for project (repo root) or user."""
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    path = paths.project_context_file() if scope == "project" else paths.USER_CONTEXT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return ActionResult(True, f"saved {scope} AGENTS.md")


def prompt_save_action(scope: str, name: str, text: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    name = name.strip().removesuffix(".md")
    if not _SKILL_NAME_RE.match(name):
        return ActionResult(False, "prompt name must be lowercase letters, digits, hyphens")
    base = paths.project_prompts_dir() if scope == "project" else paths.USER_PROMPTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{name}.md").write_text(text, encoding="utf-8")
    return ActionResult(True, f"saved prompt '{name}'")


def prompt_delete_action(scope: str, name: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    base = paths.project_prompts_dir() if scope == "project" else paths.USER_PROMPTS_DIR
    path = base / f"{name}.md"
    if not path.is_file():
        return ActionResult(False, f"no prompt '{name}' in {scope}")
    path.unlink()
    return ActionResult(True, f"deleted prompt '{name}'")


# ---------- Providers (R4) ----------


def providers_raw_save_action(text: str) -> ActionResult:
    """Write the user providers.yaml overlay (validated as a YAML mapping)."""
    from jac.providers.registry import reset_provider_registry_cache

    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        return ActionResult(False, f"invalid YAML: {exc}")
    if data is not None and not isinstance(data, dict):
        return ActionResult(False, "providers.yaml must be a mapping at the top level")
    paths.USER_PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.USER_PROVIDERS_FILE.write_text(text, encoding="utf-8")
    reset_provider_registry_cache()
    return ActionResult(True, "saved providers.yaml overlay")


# ---------- Memory (R4) ----------


def memory_raw_save_action(scope: str, text: str) -> ActionResult:
    if scope not in ("project", "user"):
        return ActionResult(False, f"bad scope {scope!r}")
    path = paths.USER_MEMORY_FILE if scope == "user" else paths.project_memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return ActionResult(True, f"saved {scope} memory.md")
