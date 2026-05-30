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

from dataclasses import dataclass

from jac.errors import JacConfigError
from jac.profiles_crud import add_or_update_profile, remove_profile, set_default_profile
from jac.profiles_io import load_profile_from_yaml
from jac.runtime.session import Session
from jac.secrets import get_backend


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
