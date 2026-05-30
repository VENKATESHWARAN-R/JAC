"""Starlette app factory for the web control panel (D48).

:func:`create_app` builds the ASGI app — the embeddable entry point, testable
without uvicorn. ``GET`` routes render Jinja pages from the read-side
assemblers in :mod:`jac.web.panel`; ``POST`` routes parse a form, call one
mutator in :mod:`jac.web.actions`, and redirect back (Post/Redirect/Get) with
an ``ok=`` / ``err=`` flash query param.

This is Slice 1 — pure config/session management, no agent driving. The chat +
HITL surface (Slice 2) mounts its SSE/WebSocket routes onto this same app.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from jac import __version__
from jac.web import actions, panel

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _render(request: Request, name: str, active: str, ctx: dict[str, object]) -> Response:
    """Render ``name`` with the shared chrome context (version, nav, flash)."""
    base: dict[str, object] = {
        "version": __version__,
        "active": active,
        "ok": request.query_params.get("ok"),
        "err": request.query_params.get("err"),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def _redirect(path: str, result: actions.ActionResult) -> RedirectResponse:
    """Post/Redirect/Get back to ``path`` carrying the result as a flash param."""
    key = "ok" if result.ok else "err"
    return RedirectResponse(f"{path}?{key}={quote(result.message)}", status_code=303)


# ---------- GET pages ----------


async def overview(request: Request) -> Response:
    return _render(request, "overview.html", "overview", panel.overview_context())


async def profiles(request: Request) -> Response:
    return _render(request, "profiles.html", "profiles", panel.profiles_context())


async def keys(request: Request) -> Response:
    return _render(request, "keys.html", "keys", panel.keys_context())


async def sessions(request: Request) -> Response:
    return _render(request, "sessions.html", "sessions", panel.sessions_context())


# ---------- POST actions ----------


async def profile_set_default(request: Request) -> Response:
    form = await request.form()
    result = actions.set_default_profile_action(str(form.get("name", "")))
    return _redirect("/profiles", result)


async def profile_delete(request: Request) -> Response:
    form = await request.form()
    result = actions.delete_profile_action(str(form.get("name", "")))
    return _redirect("/profiles", result)


async def profile_save(request: Request) -> Response:
    form = await request.form()
    result = actions.save_profile_action(
        str(form.get("name", "")),
        str(form.get("yaml", "")),
        set_default=bool(form.get("set_default")),
    )
    return _redirect("/profiles", result)


async def key_set(request: Request) -> Response:
    form = await request.form()
    result = actions.set_secret_action(str(form.get("key", "")), str(form.get("value", "")))
    return _redirect("/keys", result)


async def key_unset(request: Request) -> Response:
    form = await request.form()
    result = actions.unset_secret_action(str(form.get("key", "")))
    return _redirect("/keys", result)


async def session_delete(request: Request) -> Response:
    form = await request.form()
    result = actions.delete_session_action(str(form.get("id", "")))
    return _redirect("/sessions", result)


def create_app() -> Starlette:
    """Build the JAC web-UI ASGI app (Slice 1 control panel)."""
    routes = [
        Route("/", overview, name="overview"),
        Route("/profiles", profiles, name="profiles"),
        Route("/profiles/default", profile_set_default, methods=["POST"]),
        Route("/profiles/delete", profile_delete, methods=["POST"]),
        Route("/profiles/save", profile_save, methods=["POST"]),
        Route("/keys", keys, name="keys"),
        Route("/keys/set", key_set, methods=["POST"]),
        Route("/keys/unset", key_unset, methods=["POST"]),
        Route("/sessions", sessions, name="sessions"),
        Route("/sessions/delete", session_delete, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static"),
    ]
    return Starlette(routes=routes)
