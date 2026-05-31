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
from pathlib import Path

from jac.capabilities.mcp import load_mcp_catalog
from jac.capabilities.skills import load_all_skills
from jac.config import get_settings
from jac.errors import JacConfigError
from jac.profiles_crud import get_default_profile_name, get_profile, list_profiles
from jac.profiles_io import profile_to_yaml
from jac.providers.registry import get_provider_registry
from jac.runtime.session import Session
from jac.secrets import resolve
from jac.workspace import config_io, paths


def _read_jsonl_tail(path: Path, n: int) -> list[dict]:
    """Best-effort read of the last ``n`` JSON objects in a .jsonl file."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


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


# ---------- config groups (R1: precedence- and scope-aware forms) ----------

# Structured editor spec for the non-profile config groups. Each field is
# (name, label, type, options). `type` drives the control + the parse on save:
#   select  → dropdown (options)        int     → required number
#   int_opt → number, blank = unset     bool    → true/false
#   list    → comma-separated tokens
# The big `summarize_prompt_template` and the `model_context_tokens` map are
# intentionally omitted here — they're reachable via the raw-YAML escape hatch.
_CONFIG_GROUPS: list[dict] = [
    {
        "key": "compaction",
        "title": "Compaction",
        "help": "How history is kept within the context budget (% of the resolved budget).",
        "fields": [
            ("strategy", "Strategy", "select", ["auto", "sliding", "manual"]),
            ("max_context_tokens", "Max context tokens", "int", None),
            ("warn_pct", "Warn at %", "int", None),
            ("auto_compact_pct", "Auto-compact at %", "int", None),
            ("refuse_pct", "Refuse at %", "int", None),
            ("target_pct_after_compact", "Target % after compact", "int", None),
        ],
    },
    {
        "key": "budget",
        "title": "Budget",
        "help": "Opt-in token caps — leave blank for unlimited.",
        "fields": [
            ("session_input_tokens", "Session input cap", "int_opt", None),
            ("session_total_tokens", "Session total cap", "int_opt", None),
            ("project_total_tokens", "Project total cap", "int_opt", None),
            ("warn_pct", "Warn at %", "int", None),
            ("hardstop_pct", "Hard-stop at %", "int", None),
        ],
    },
    {
        "key": "cost",
        "title": "Cost & sub-agents",
        "help": "Tool-result summarization threshold and sub-agent behavior.",
        "fields": [
            ("tool_result_threshold_tokens", "Summarize tool output over (tokens)", "int", None),
            ("sub_agent_bidirectional", "Bidirectional sub-agents", "bool", None),
            ("no_summarize_tools", "Never summarize (tool names)", "list", None),
            ("summarize_tools", "Always summarize (tool names)", "list", None),
        ],
    },
    {
        "key": "secrets",
        "title": "Secrets",
        "help": "Where API keys are stored.",
        "fields": [("backend", "Backend", "select", ["keyring", "dotenv", "env-only"])],
    },
]


def _field_value_str(value: object, ftype: str) -> str:
    """Render a field's effective value for an <input>/<select>/textarea."""
    if value is None:
        return ""
    if ftype == "list" and isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if ftype == "bool":
        return "true" if value else "false"
    return str(value)


def config_context(scope_param: str | None) -> dict[str, object]:
    """Build the config form model for the chosen edit scope.

    For every field: the effective value (from the resolved settings), which
    layer supplies it (``origin``), and whether editing ``scope`` would be a
    no-op because a higher-precedence layer wins (``locked``). Also carries the
    raw scope file text for the advanced escape hatch.
    """
    in_project = paths.in_project()
    scope = (
        scope_param if scope_param in ("project", "user") else ("project" if in_project else "user")
    )
    settings = get_settings()

    groups: list[dict[str, object]] = []
    for group in _CONFIG_GROUPS:
        gkey = str(group["key"])
        model = getattr(settings, gkey)
        fields: list[dict[str, object]] = []
        for name, label, ftype, options in group["fields"]:
            value = getattr(model, name)
            origin = config_io.field_origin(gkey, name)
            fields.append(
                {
                    "name": name,
                    "label": label,
                    "type": ftype,
                    "options": options,
                    "value_str": _field_value_str(value, ftype),
                    "checked": bool(value) if ftype == "bool" else False,
                    "origin": origin,
                    "locked": config_io.is_locked_for(scope, origin),
                    "in_scope": origin == scope,
                }
            )
        groups.append(
            {"key": gkey, "title": group["title"], "help": group["help"], "fields": fields}
        )

    return {
        "cfg_scope": scope,
        "cfg_in_project": in_project,
        "cfg_groups": groups,
        "cfg_raw": config_io.scope_raw_text(scope),
    }


# ---------- MCP servers (R3) ----------


def _mcp_scope(scope_param: str | None) -> str:
    in_project = paths.in_project()
    return (
        scope_param if scope_param in ("project", "user") else ("project" if in_project else "user")
    )


def mcp_context(scope_param: str | None) -> dict[str, object]:
    """MCP catalog: every server with its knobs + provenance, plus the raw file.

    Knob edits persist to each server's *source* file; the scope toggle only
    chooses where new servers / raw edits land.
    """
    catalog = load_mcp_catalog()
    scope = _mcp_scope(scope_param)
    servers = []
    for name, srv in sorted(catalog.servers.items()):
        servers.append(
            {
                "name": name,
                "transport": srv.transport,
                "source": srv.source,
                "enabled": srv.knobs.enabled,
                "defer": srv.knobs.defer,
                "requires_approval": srv.knobs.requires_approval,
                "init_timeout": srv.knobs.init_timeout,
                "raw_json": json.dumps(srv.raw, indent=2),
            }
        )
    raw_path = paths.project_mcp_file() if scope == "project" else paths.USER_MCP_FILE
    raw = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else ""
    return {
        "mcp_scope": scope,
        "mcp_in_project": paths.in_project(),
        "mcp_servers": servers,
        "mcp_errors": catalog.parse_errors,
        "mcp_raw": raw,
    }


# ---------- A2A (R3) ----------


def a2a_context() -> dict[str, object]:
    """A2A view: the default profile's peers + server defaults, and inbound audit.

    Peers and server defaults live on the profile (also editable via the
    Profiles YAML); the server lifecycle itself stays in the chat (`/a2a serve`)
    or the headless `jac a2a serve`.
    """
    profile_name = None
    peers: list[dict] = []
    host, port, retention, allow_private = "127.0.0.1", 8001, 3, False
    error = None
    try:
        profile_name = get_default_profile_name()
        if profile_name:
            a2a = get_profile(profile_name).a2a
            host, port = a2a.host, a2a.port
            retention, allow_private = a2a.context_retention_days, a2a.allow_private_peers
            for name, peer in sorted(a2a.peers.items()):
                peers.append(
                    {
                        "name": name,
                        "url": peer.url,
                        "auth": type(peer.auth).__name__.replace("Auth", "").lower()
                        if peer.auth
                        else "none",
                        "description": peer.description,
                    }
                )
    except JacConfigError as exc:
        error = str(exc)

    audit = _read_jsonl_tail(paths.project_a2a_inbound_log(), 8) if paths.in_project() else []
    audit.reverse()  # newest first
    return {
        "a2a_profile": profile_name,
        "a2a_error": error,
        "a2a_peers": peers,
        "a2a_host": host,
        "a2a_port": port,
        "a2a_retention": retention,
        "a2a_allow_private": allow_private,
        "a2a_audit": audit,
    }


# ---------- Skills (R4) ----------


def skills_context() -> dict[str, object]:
    """Active + shadowed skills; active ones carry their SKILL.md text for editing.

    Package skills are read-only (you can copy one into project/user to edit).
    """
    catalog = load_all_skills()
    active = []
    for name, sk in sorted(catalog.active.items()):
        try:
            text = sk.path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        active.append(
            {
                "name": name,
                "description": sk.description,
                "source": sk.source,
                "editable": sk.source != "package",
                "text": text,
            }
        )
    shadowed = [{"name": s.name, "source": s.source} for s in catalog.shadowed]
    return {
        "skills_active": active,
        "skills_shadowed": shadowed,
        "skills_in_project": paths.in_project(),
    }


# ---------- Context & prompts (R4) ----------


def _read_text(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if (path and path.is_file()) else ""


def context_context() -> dict[str, object]:
    """Auto-loaded AGENTS.md (project + user) and the prompt overlays."""
    prompts = []
    dirs = [("user", paths.USER_PROMPTS_DIR)]
    if paths.in_project():
        dirs.append(("project", paths.project_prompts_dir()))
    for scope, directory in dirs:
        if directory and directory.is_dir():
            for f in sorted(directory.glob("*.md")):
                prompts.append(
                    {"scope": scope, "name": f.stem, "text": f.read_text(encoding="utf-8")}
                )
    return {
        "ctx_in_project": paths.in_project(),
        "agents_project": _read_text(paths.project_context_file() if paths.in_project() else None),
        "agents_user": _read_text(paths.USER_CONTEXT_FILE),
        "prompts": prompts,
    }


# ---------- Providers (R4) ----------


def providers_context() -> dict[str, object]:
    """Provider catalog (read) + the user overlay (raw YAML editor)."""
    registry = get_provider_registry()
    provs = []
    for pid, spec in sorted(registry.providers.items()):
        pricing = getattr(spec, "pricing", None) or {}
        provs.append(
            {
                "id": pid,
                "prefix": spec.prefix,
                "required_env": list(spec.required_env),
                "models": sorted(pricing.keys()),
            }
        )
    return {"providers": provs, "providers_raw": _read_text(paths.USER_PROVIDERS_FILE)}


# ---------- Memory (R4) ----------


def memory_context() -> dict[str, object]:
    """JAC-managed memory (read-mostly): parsed entries + a raw editor per scope."""
    from typing import Literal

    from jac.capabilities.memory import read_memory_entries

    def entries(scope: Literal["user", "project"]) -> dict:
        try:
            _path, sections = read_memory_entries(scope)
        except Exception:
            return {}
        return sections

    in_project = paths.in_project()
    return {
        "mem_in_project": in_project,
        "mem_user": entries("user"),
        "mem_project": entries("project") if in_project else {},
        "mem_user_raw": _read_text(paths.USER_MEMORY_FILE),
        "mem_project_raw": _read_text(paths.project_memory_file() if in_project else None),
    }


# ---------- Doctor / readiness + Dashboard (R5) ----------


def doctor_items() -> list[dict]:
    """Fail-first readiness checks — what's blocking (or risking) a run."""
    items: list[dict] = []

    name = None
    try:
        name = get_default_profile_name()
    except JacConfigError:
        name = None
    if not name:
        items.append(
            {
                "level": "bad",
                "msg": "No default profile — no model is bound.",
                "fix": "Add one under Profiles & models.",
            }
        )
    else:
        try:
            profile = get_profile(name)
            missing = [k for k in profile.required_env_keys() if resolve(k)[1] == "missing"]
            if missing:
                items.append(
                    {
                        "level": "bad",
                        "msg": f"Profile '{name}' is missing {', '.join(missing)}.",
                        "fix": "Set the key(s) under Keys & secrets.",
                    }
                )
            else:
                items.append(
                    {
                        "level": "ok",
                        "msg": f"Profile '{name}' → {profile.default_model()}",
                        "fix": "",
                    }
                )
        except JacConfigError as exc:
            items.append({"level": "bad", "msg": str(exc), "fix": ""})

    errors = load_mcp_catalog().parse_errors
    if errors:
        items.append(
            {
                "level": "warn",
                "msg": f"{len(errors)} MCP catalog error(s).",
                "fix": "Fix them under MCP servers.",
            }
        )
    return items


def doctor_status(items: list[dict] | None = None) -> str:
    """Worst level across the readiness items: ``ok`` / ``warn`` / ``bad``."""
    items = doctor_items() if items is None else items
    levels = {i["level"] for i in items}
    return "bad" if "bad" in levels else "warn" if "warn" in levels else "ok"


def dashboard_context() -> dict[str, object]:
    """Observability home: live cost/usage snapshot + readiness."""
    from jac.web.chat import get_manager

    items = doctor_items()
    dash = get_manager().dashboard()
    return {"doctor_items": items, "doctor_status": doctor_status(items), "dash": dash}
