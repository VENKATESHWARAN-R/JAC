"""Starlette app factory for the web surface (D48; redesigned).

:func:`create_app` builds the ASGI app — the embeddable entry point, testable
without uvicorn. The surface has two natures (see ``docs/design/web-surface.md``):

- **Console** — the chat-first home: SSE event stream + HITL POSTs, driven by
  :class:`jac.web.chat.WebChatManager`.
- **Control Panel** — management of every config domain. Each domain is a *panel
  fragment* (``templates/panel/<name>.html``) that renders two ways: inside a
  slide-over **drawer** (loaded over htmx, so the Console stays live) or as a
  standalone **full page** (direct URL / deep link / the test path).

Write handlers are **htmx-aware**: an ``HX-Request`` (a drawer form) gets the
re-rendered fragment back with an inline flash; a plain POST (full page / tests)
gets the legacy Post/Redirect/Get with an ``ok=`` / ``err=`` query flash.

Panels register in :data:`PANELS` (section → fragment template + read-side
context fn + drawer title). Adding a domain = one registry entry + its fragment
+ its write handlers; the drawer/full-page plumbing is shared.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from jac import __version__
from jac.web import actions, panel
from jac.web.chat import get_manager

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------- panel registry ----------
# section -> (fragment template, read-side context fn, drawer title).
# Grows one entry per management domain; the drawer + full-page plumbing below
# is shared, so a new panel needs only its fragment + write handlers.
# Context fns take the Request (some panels read query params, e.g. config's
# ?scope) and return the read-side view model.
PANELS: dict[str, tuple[str, Callable[[Request], dict[str, object]], str]] = {
    "profiles": ("panel/profiles.html", lambda r: panel.profiles_context(), "Profiles & models"),
    "keys": ("panel/keys.html", lambda r: panel.keys_context(), "Keys & secrets"),
    "config": (
        "panel/config.html",
        lambda r: panel.config_context(r.query_params.get("scope")),
        "Config",
    ),
    "mcp": (
        "panel/mcp.html",
        lambda r: panel.mcp_context(r.query_params.get("scope")),
        "MCP servers",
    ),
    "a2a": ("panel/a2a.html", lambda r: panel.a2a_context(), "A2A"),
    "skills": ("panel/skills.html", lambda r: panel.skills_context(), "Skills"),
    "context": ("panel/context.html", lambda r: panel.context_context(), "Context & prompts"),
    "providers": ("panel/providers.html", lambda r: panel.providers_context(), "Providers"),
    "memory": ("panel/memory.html", lambda r: panel.memory_context(), "Memory"),
    "dashboard": ("panel/dashboard.html", lambda r: panel.dashboard_context(), "Dashboard"),
    "settings": ("panel/settings.html", lambda r: {}, "Settings"),
}


# ---------- rendering helpers ----------


def _chrome(
    request: Request, active: str = "", active_session: str | None = None
) -> dict[str, object]:
    """Shared chrome context: version, nav/session rail, flash, scope/profile/model."""
    base: dict[str, object] = {
        "version": __version__,
        "active": active,
        "ok": request.query_params.get("ok"),
        "err": request.query_params.get("err"),
        "active_session": active_session,
    }
    base.update(panel.sidebar_context())
    return base


def _full_page(request: Request, section: str) -> Response:
    """Render a panel as a standalone page (direct URL / deep link / tests)."""
    template, ctx_fn, _title = PANELS[section]
    data = _chrome(request, active=section)
    data.update({"fragment": template, "in_drawer": False})
    data.update(ctx_fn(request))
    return templates.TemplateResponse(request, "_page.html", data)


def _fragment(
    request: Request, template: str, ctx: dict[str, object], *, flash: dict[str, str] | None = None
) -> Response:
    """Render a panel fragment alone (drawer load / htmx re-render after a write)."""
    data = _chrome(request)
    data.update({"in_drawer": True, "flash": flash})
    data.update(ctx)
    return templates.TemplateResponse(request, template, data)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect(path: str, result: actions.ActionResult) -> RedirectResponse:
    """Post/Redirect/Get back to ``path`` carrying the result as a flash param."""
    key = "ok" if result.ok else "err"
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{key}={quote(result.message)}", status_code=303)


def _after_write(
    request: Request, result: actions.ActionResult, section: str, redirect_path: str
) -> Response:
    """Answer a write: re-render the fragment for an htmx drawer form, else PRG."""
    if _is_htmx(request):
        template, ctx_fn, _title = PANELS[section]
        flash = {"kind": "ok" if result.ok else "err", "msg": result.message}
        return _fragment(request, template, ctx_fn(request), flash=flash)
    return _redirect(redirect_path, result)


# ---------- GET: pages + drawer fragments ----------


async def root(request: Request) -> Response:
    return RedirectResponse("/chat", status_code=307)


async def profiles(request: Request) -> Response:
    return _full_page(request, "profiles")


async def keys(request: Request) -> Response:
    return _full_page(request, "keys")


async def settings(request: Request) -> Response:
    return _full_page(request, "settings")


async def config(request: Request) -> Response:
    return _full_page(request, "config")


async def mcp(request: Request) -> Response:
    return _full_page(request, "mcp")


async def a2a(request: Request) -> Response:
    return _full_page(request, "a2a")


async def skills(request: Request) -> Response:
    return _full_page(request, "skills")


async def context(request: Request) -> Response:
    return _full_page(request, "context")


async def providers(request: Request) -> Response:
    return _full_page(request, "providers")


async def memory(request: Request) -> Response:
    return _full_page(request, "memory")


async def dashboard(request: Request) -> Response:
    return _full_page(request, "dashboard")


async def doctor_json(request: Request) -> Response:
    """Readiness status for the top-bar dot (ok / warn / bad)."""
    return JSONResponse({"status": panel.doctor_status()})


async def panel_fragment(request: Request) -> Response:
    """Drawer content for a management section (htmx-loaded into #drawer-body)."""
    section = request.path_params["section"]
    entry = PANELS.get(section)
    if entry is None:
        return _fragment(request, "panel/_stub.html", {"section": section})
    template, ctx_fn, _title = entry
    return _fragment(request, template, ctx_fn(request))


# ---------- POST: management writes (htmx-aware) ----------


async def profile_set_default(request: Request) -> Response:
    form = await request.form()
    result = actions.set_default_profile_action(str(form.get("name", "")))
    return _after_write(request, result, "profiles", "/profiles")


async def profile_delete(request: Request) -> Response:
    form = await request.form()
    result = actions.delete_profile_action(str(form.get("name", "")))
    return _after_write(request, result, "profiles", "/profiles")


async def profile_save(request: Request) -> Response:
    form = await request.form()
    result = actions.save_profile_action(
        str(form.get("name", "")),
        str(form.get("yaml", "")),
        set_default=bool(form.get("set_default")),
    )
    return _after_write(request, result, "profiles", "/profiles")


async def key_set(request: Request) -> Response:
    form = await request.form()
    result = actions.set_secret_action(str(form.get("key", "")), str(form.get("value", "")))
    return _after_write(request, result, "keys", "/keys")


async def key_unset(request: Request) -> Response:
    form = await request.form()
    result = actions.unset_secret_action(str(form.get("key", "")))
    return _after_write(request, result, "keys", "/keys")


# config writes carry their own scope (project/user), so they re-render / redirect
# preserving it rather than going through the generic _after_write.
def _config_after(request: Request, result: actions.ActionResult, scope: str) -> Response:
    if _is_htmx(request):
        flash = {"kind": "ok" if result.ok else "err", "msg": result.message}
        return _fragment(request, "panel/config.html", panel.config_context(scope), flash=flash)
    return _redirect(f"/config?scope={scope}", result)


async def config_set(request: Request) -> Response:
    form = await request.form()
    scope = str(form.get("scope", ""))
    result = actions.config_set_action(
        scope,
        str(form.get("group", "")),
        str(form.get("field", "")),
        str(form.get("type", "")),
        str(form.get("value", "")),
    )
    return _config_after(request, result, scope)


async def config_unset(request: Request) -> Response:
    form = await request.form()
    scope = str(form.get("scope", ""))
    result = actions.config_unset_action(
        scope, str(form.get("group", "")), str(form.get("field", ""))
    )
    return _config_after(request, result, scope)


async def config_raw(request: Request) -> Response:
    form = await request.form()
    scope = str(form.get("scope", ""))
    result = actions.config_raw_save_action(scope, str(form.get("text", "")))
    return _config_after(request, result, scope)


# ---------- MCP writes ----------


def _mcp_after(request: Request, result: actions.ActionResult, scope: str | None) -> Response:
    # The panel wrote mcp.json (file editor). If a chat session is live, make
    # the edit take effect in it too — re-scan the catalog + rebuild Gru — so
    # the web matches the CLI's /mcp reload|enable|disable instead of waiting
    # for a restart.
    if result.ok:
        get_manager().reload_mcp_if_live()
    if _is_htmx(request):
        flash = {"kind": "ok" if result.ok else "err", "msg": result.message}
        return _fragment(request, "panel/mcp.html", panel.mcp_context(scope), flash=flash)
    return _redirect(f"/mcp?scope={scope}" if scope else "/mcp", result)


async def mcp_knob(request: Request) -> Response:
    form = await request.form()
    result = actions.mcp_set_knob_action(
        str(form.get("source", "")),
        str(form.get("name", "")),
        str(form.get("key", "")),
        str(form.get("value", "")),
    )
    return _mcp_after(request, result, None)


async def mcp_save_server(request: Request) -> Response:
    form = await request.form()
    scope = str(form.get("scope", ""))
    result = actions.mcp_save_server_action(
        scope, str(form.get("name", "")), str(form.get("entry", ""))
    )
    return _mcp_after(request, result, scope)


async def mcp_delete_server(request: Request) -> Response:
    form = await request.form()
    scope = str(form.get("scope", ""))
    result = actions.mcp_delete_server_action(scope, str(form.get("name", "")))
    return _mcp_after(request, result, scope)


async def mcp_raw(request: Request) -> Response:
    form = await request.form()
    scope = str(form.get("scope", ""))
    result = actions.mcp_raw_save_action(scope, str(form.get("text", "")))
    return _mcp_after(request, result, scope)


# ---------- A2A writes ----------


def _a2a_after(request: Request, result: actions.ActionResult) -> Response:
    if _is_htmx(request):
        flash = {"kind": "ok" if result.ok else "err", "msg": result.message}
        return _fragment(request, "panel/a2a.html", panel.a2a_context(), flash=flash)
    return _redirect("/a2a", result)


async def a2a_add_peer(request: Request) -> Response:
    form = await request.form()
    result = actions.a2a_add_peer_action(
        str(form.get("name", "")), str(form.get("url", "")), str(form.get("token", ""))
    )
    return _a2a_after(request, result)


async def a2a_remove_peer(request: Request) -> Response:
    form = await request.form()
    result = actions.a2a_remove_peer_action(str(form.get("name", "")))
    return _a2a_after(request, result)


async def a2a_allow_private(request: Request) -> Response:
    form = await request.form()
    result = actions.a2a_set_allow_private_action(str(form.get("value", "")))
    return _a2a_after(request, result)


# ---------- Skills / Context / Providers / Memory writes (R4) ----------


async def skills_save(request: Request) -> Response:
    form = await request.form()
    result = actions.skill_save_action(
        str(form.get("scope", "")), str(form.get("name", "")), str(form.get("text", ""))
    )
    if result.ok:
        get_manager().reload_skills_if_live()
    return _after_write(request, result, "skills", "/skills")


async def skills_delete(request: Request) -> Response:
    form = await request.form()
    result = actions.skill_delete_action(str(form.get("scope", "")), str(form.get("name", "")))
    if result.ok:
        get_manager().reload_skills_if_live()
    return _after_write(request, result, "skills", "/skills")


async def context_agents(request: Request) -> Response:
    form = await request.form()
    result = actions.context_save_agents_action(
        str(form.get("scope", "")), str(form.get("text", ""))
    )
    return _after_write(request, result, "context", "/context")


async def prompts_save(request: Request) -> Response:
    form = await request.form()
    result = actions.prompt_save_action(
        str(form.get("scope", "")), str(form.get("name", "")), str(form.get("text", ""))
    )
    return _after_write(request, result, "context", "/context")


async def prompts_delete(request: Request) -> Response:
    form = await request.form()
    result = actions.prompt_delete_action(str(form.get("scope", "")), str(form.get("name", "")))
    return _after_write(request, result, "context", "/context")


async def providers_raw(request: Request) -> Response:
    form = await request.form()
    result = actions.providers_raw_save_action(str(form.get("text", "")))
    return _after_write(request, result, "providers", "/providers")


async def memory_raw(request: Request) -> Response:
    form = await request.form()
    result = actions.memory_raw_save_action(str(form.get("scope", "")), str(form.get("text", "")))
    return _after_write(request, result, "memory", "/memory")


async def session_delete(request: Request) -> Response:
    form = await request.form()
    result = actions.delete_session_action(str(form.get("id", "")))
    # Sessions live in the left rail; there's no /sessions page — go home (chat).
    return _redirect("/chat", result)


# ---------- chat (Console) ----------


async def chat_page(request: Request) -> Response:
    from jac.runtime.session import Session

    resume = request.query_params.get("session")
    new = request.query_params.get("new")
    active = resume if resume else (None if new else Session.latest_id())
    data = _chrome(request, active="chat", active_session=active)
    data.update({"resume": resume, "new": bool(new)})
    return templates.TemplateResponse(request, "chat.html", data)


async def chat_stream(request: Request) -> Response:
    from sse_starlette.sse import EventSourceResponse

    manager = get_manager()
    await manager.ensure_started(session_id=request.query_params.get("session"))
    return EventSourceResponse(manager.sse_events())


async def chat_send(request: Request) -> Response:
    data = await request.json()
    return JSONResponse(await get_manager().send(str(data.get("text", ""))))


async def chat_approve(request: Request) -> Response:
    data = await request.json()
    ok = get_manager().resolve_approval(
        str(data.get("id", "")), bool(data.get("approved")), data.get("feedback")
    )
    return JSONResponse({"ok": ok})


async def chat_clarify(request: Request) -> Response:
    data = await request.json()
    idx = data.get("index")
    ok = get_manager().resolve_clarify(
        selected_index=int(idx) if idx is not None else None,
        selected_text=data.get("text"),
        free_text=bool(data.get("free_text")),
    )
    return JSONResponse({"ok": ok})


async def chat_new(request: Request) -> Response:
    return JSONResponse(await get_manager().new_session())


async def chat_status(request: Request) -> Response:
    return JSONResponse(get_manager().dashboard())


async def chat_history(request: Request) -> Response:
    return JSONResponse({"messages": get_manager().history_messages()})


async def chat_environment(request: Request) -> Response:
    return JSONResponse(get_manager().environment())


async def chat_switcher(request: Request) -> Response:
    """Options for the top-bar profile/model dropdowns."""
    return JSONResponse(get_manager().switcher_options())


async def chat_switch_model(request: Request) -> Response:
    data = await request.json()
    return JSONResponse(await get_manager().switch_model(str(data.get("model", ""))))


async def chat_switch_profile(request: Request) -> Response:
    data = await request.json()
    return JSONResponse(await get_manager().switch_profile(str(data.get("profile", ""))))


async def chat_use_skill(request: Request) -> Response:
    """Run a turn seeded with a loaded skill's body (mirrors CLI ``/skill use``)."""
    data = await request.json()
    return JSONResponse(await get_manager().use_skill(str(data.get("name", ""))))


def create_app() -> Starlette:
    """Build the JAC web-UI ASGI app (Console + Control Panel)."""
    routes = [
        Route("/", root, name="root"),
        # management — full pages (deep link / tests) + drawer fragments
        Route("/profiles", profiles, name="profiles"),
        Route("/keys", keys, name="keys"),
        Route("/settings", settings, name="settings"),
        Route("/config", config, name="config"),
        Route("/mcp", mcp, name="mcp"),
        Route("/a2a", a2a, name="a2a"),
        Route("/skills", skills, name="skills"),
        Route("/context", context, name="context"),
        Route("/providers", providers, name="providers"),
        Route("/memory", memory, name="memory"),
        Route("/dashboard", dashboard, name="dashboard"),
        Route("/doctor.json", doctor_json, name="doctor_json"),
        Route("/panel/{section}", panel_fragment, name="panel"),
        # management — writes
        Route("/profiles/default", profile_set_default, methods=["POST"]),
        Route("/profiles/delete", profile_delete, methods=["POST"]),
        Route("/profiles/save", profile_save, methods=["POST"]),
        Route("/keys/set", key_set, methods=["POST"]),
        Route("/keys/unset", key_unset, methods=["POST"]),
        Route("/config/set", config_set, methods=["POST"]),
        Route("/config/unset", config_unset, methods=["POST"]),
        Route("/config/raw", config_raw, methods=["POST"]),
        Route("/mcp/knob", mcp_knob, methods=["POST"]),
        Route("/mcp/save-server", mcp_save_server, methods=["POST"]),
        Route("/mcp/delete-server", mcp_delete_server, methods=["POST"]),
        Route("/mcp/raw", mcp_raw, methods=["POST"]),
        Route("/a2a/add-peer", a2a_add_peer, methods=["POST"]),
        Route("/a2a/remove-peer", a2a_remove_peer, methods=["POST"]),
        Route("/a2a/allow-private", a2a_allow_private, methods=["POST"]),
        Route("/skills/save", skills_save, methods=["POST"]),
        Route("/skills/delete", skills_delete, methods=["POST"]),
        Route("/context/agents", context_agents, methods=["POST"]),
        Route("/prompts/save", prompts_save, methods=["POST"]),
        Route("/prompts/delete", prompts_delete, methods=["POST"]),
        Route("/providers/raw", providers_raw, methods=["POST"]),
        Route("/memory/raw", memory_raw, methods=["POST"]),
        Route("/sessions/delete", session_delete, methods=["POST"]),
        # console
        Route("/chat", chat_page, name="chat"),
        Route("/chat/stream", chat_stream, name="chat_stream"),
        Route("/chat/send", chat_send, methods=["POST"]),
        Route("/chat/approve", chat_approve, methods=["POST"]),
        Route("/chat/clarify", chat_clarify, methods=["POST"]),
        Route("/chat/new", chat_new, methods=["POST"]),
        Route("/chat/status", chat_status, name="chat_status"),
        Route("/chat/history", chat_history, name="chat_history"),
        Route("/chat/environment", chat_environment, name="chat_environment"),
        Route("/chat/switcher", chat_switcher, name="chat_switcher"),
        Route("/chat/switch-model", chat_switch_model, methods=["POST"]),
        Route("/chat/switch-profile", chat_switch_profile, methods=["POST"]),
        Route("/chat/use-skill", chat_use_skill, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static"),
    ]
    return Starlette(routes=routes)
