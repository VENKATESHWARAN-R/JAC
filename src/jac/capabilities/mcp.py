"""MCP server loader (Phase F, D28).

JAC consumes external [Model Context Protocol](https://modelcontextprotocol.io)
servers so its tool surface scales without hand-writing every tool. The
design leans entirely on what ``pydantic-ai`` already ships — we add the
*JAC fabric* around it (layered config, HITL, post-processor, tool search,
slash surface), not a bespoke MCP client.

**Config format (D28 / locked).** Servers are declared in the de-facto
ecosystem shape — the ``mcpServers`` JSON used by Claude Desktop, Cursor,
and the MCP spec — so an existing config pastes in verbatim:

    {
      "mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}},
        "weather": {"type": "http", "url": "https://example.com/mcp"}
      },
      "jac": {
        "weather": {"requires_approval": false}
      }
    }

This deviates from JAC's "YAML for human-edited config" rule on purpose:
MCP config is an *interop artifact* (like the community ``AGENTS.md``), not
JAC app config, and "don't invent a bespoke format when the community one
exists" is already our stance (CLAUDE.md anti-patterns).

The optional ``jac`` block carries JAC-specific per-server knobs
(:class:`MCPServerKnobs`); it's a *sibling* of ``mcpServers`` so the file
stays a valid standard catalog (a plain ``mcpServers`` reader ignores it).

**Layering.** ``<repo>/.agents/mcp.json`` shadows ``~/.jac/mcp.json``
**per server name**, mirroring the skill / prompt overlay precedence.

**Wrapping.** Each enabled server's toolset is composed as::

    MCPToolset → .prefixed(name)        # name-disambiguation (pydantic-ai)
               → .defer_loading()       # tool search hides it until needed
               → summarizing_wrap(all)  # large outputs hit the post-processor
               → .approval_required()   # HITL on every external call (opt-out)

``.defer_loading()`` is what keeps MCP servers from bloating the prompt:
the built-in ``ToolSearch`` capability (auto-injected by pydantic-ai)
discovers deferred tools on demand — natively on Anthropic/OpenAI, via a
local ``search_tools`` fallback elsewhere — append-only so prompt caching
survives.

The ``reason: str`` discipline (architecture §6a) does **not** apply to MCP
tools (D28): they never pass through :func:`jac.tools.jac_function_toolset`,
so the structural guard is naturally bypassed, and the approval panel
renders ``reason: (mcp tool — no reason captured)``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import WrapperToolset

from jac.tools import summarizing_wrap
from jac.workspace import paths

logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")
"""Match ``${VAR}`` and ``${VAR:-default}`` for env expansion in catalog
values — same syntax pydantic-ai / Claude Desktop accept."""

MCPSource = Literal["project", "user"]
"""Where a server entry came from. Project shadows user on name collision."""

_INSTRUCTIONS_CAP_BYTES = 1024
"""Soft ceiling on the MCP block injected into the system prompt. Keeps the
cache-friendly prefix small even with many servers."""


# --- models ------------------------------------------------------------


class MCPServerKnobs(BaseModel):
    """JAC-specific per-server knobs from the optional ``jac`` block.

    All default to the safe/efficient choice so a bare standard catalog
    (no ``jac`` block at all) behaves sensibly: every server enabled, its
    tools deferred (discovered via tool search), and every call HITL-gated.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    """When ``False`` the server's toolset is not attached. Toggle at
    runtime via ``/mcp enable|disable`` (persists to the owning file)."""

    defer: bool = True
    """Mark the server's tools for deferred loading (tool search). Turn off
    only for a tiny, always-needed server whose definitions are cheap."""

    requires_approval: bool = True
    """HITL-gate every call into this server. Set ``False`` for trusted /
    read-only servers whose calls don't need per-call approval."""

    init_timeout: float = 30.0
    """Seconds to wait for the server's connection + ``initialize`` handshake.
    pydantic-ai's default is 5s, which a browser-launching server (playwright,
    chrome-devtools) routinely exceeds — yielding "Failed to initialize server
    session". 30s is a safer default; raise it for heavier servers."""


@dataclass(frozen=True)
class LoadedMCPServer:
    """One parsed server entry plus its resolved JAC knobs and provenance."""

    name: str
    raw: dict[str, Any]
    """The standard ``mcpServers`` entry, verbatim (env unexpanded). Env
    vars are expanded at build time, per enabled server — see
    :func:`_build_server_toolset`."""
    knobs: MCPServerKnobs
    source: MCPSource

    @property
    def transport(self) -> str:
        """Best-effort transport label for ``/mcp list`` (display only)."""
        if "command" in self.raw:
            return "stdio"
        declared = str(self.raw.get("type", "")).lower()
        if declared in {"http", "streamable-http", "sse"}:
            return "sse" if declared == "sse" else "http"
        if "url" in self.raw:
            return "http"
        return "unknown"


@dataclass(frozen=True)
class MCPCatalog:
    """Outcome of a discovery pass over the project + user catalogs."""

    servers: dict[str, LoadedMCPServer] = field(default_factory=dict)
    parse_errors: list[str] = field(default_factory=list)
    """Human-readable parse failures (bad JSON, schema) surfaced in
    ``/mcp list`` so a broken file is visible rather than silently empty."""

    @property
    def enabled(self) -> dict[str, LoadedMCPServer]:
        return {n: s for n, s in self.servers.items() if s.knobs.enabled}


# --- loader ------------------------------------------------------------


def _config_files() -> list[tuple[Path, MCPSource]]:
    """User then project, in *merge* order (later wins per server name)."""
    files: list[tuple[Path, MCPSource]] = [(paths.USER_MCP_FILE, "user")]
    if paths.in_project():
        files.append((paths.project_mcp_file(), "project"))
    return files


def _parse_file(path: Path) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Return ``(mcpServers, jac_knobs, error)`` for one catalog file.

    Missing file → empty maps, no error. Unreadable / invalid JSON / wrong
    shape → empty maps + a human-readable error string (never raises — a
    broken MCP catalog is a degraded surface, not a fatal config error).
    """
    if not path.is_file():
        return {}, {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {}, f"{path}: {exc}"
    if not isinstance(data, dict):
        return {}, {}, f"{path}: top level must be a JSON object"
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {}, {}, f"{path}: 'mcpServers' must be an object"
    jac_block = data.get("jac", {})
    if not isinstance(jac_block, dict):
        return {}, {}, f"{path}: 'jac' must be an object"
    return servers, jac_block, None


def load_mcp_catalog() -> MCPCatalog:
    """Discover + merge the user and project MCP catalogs.

    Project entries shadow user entries of the same name (both the server
    definition and its ``jac`` knobs). Parse failures are collected, not
    raised. Knob validation failures fall back to defaults for that server
    and add an error note.
    """
    merged_servers: dict[str, tuple[dict[str, Any], MCPSource]] = {}
    merged_knobs: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for path, source in _config_files():
        servers, jac_block, err = _parse_file(path)
        if err is not None:
            errors.append(err)
        for name, raw in servers.items():
            if isinstance(raw, dict):
                merged_servers[name] = (raw, source)
        for name, knob in jac_block.items():
            if isinstance(knob, dict):
                merged_knobs[name] = knob

    servers: dict[str, LoadedMCPServer] = {}
    for name, (raw, source) in merged_servers.items():
        try:
            knobs = MCPServerKnobs.model_validate(merged_knobs.get(name, {}))
        except ValidationError as exc:
            errors.append(f"jac knobs for {name!r} invalid, using defaults: {exc}")
            knobs = MCPServerKnobs()
        servers[name] = LoadedMCPServer(name=name, raw=raw, knobs=knobs, source=source)

    return MCPCatalog(servers=servers, parse_errors=errors)


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in a JSON value.

    Raises ``KeyError`` (with the missing name) when a referenced variable
    is unset and no default is given — so a server with a missing secret is
    skipped with a clear message rather than silently misconfigured.
    """
    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            name, _, default = m.group(1), m.group(2), m.group(3)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise KeyError(name)

        return _ENV_VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _always_approve_filter(_ctx: Any, _tool_def: Any, _args: Any) -> bool:
    """Approval predicate: gate *every* call into an MCP server."""
    return True


@dataclass
class _ResilientMCPToolset(WrapperToolset[Any]):
    """Make a single MCP server's connection failure non-fatal.

    A server's connection + ``initialize`` handshake happens when the agent
    *enters* the toolset at the start of a run — before any tool is called.
    If that raises (server crash, bad command, init timeout), it aborts the
    **whole turn**, so one broken server would make the agent unusable. This
    wrapper catches the failure, logs it, and degrades the server to zero
    tools for the session — the other servers and the agent keep working.
    Safe to hold state: ``MCPToolset`` doesn't override ``for_run``, so the
    run loop reuses this instance rather than ``replace()``-ing it.
    """

    server_name: str = ""

    def __post_init__(self) -> None:
        self._broken = False

    async def __aenter__(self) -> Any:
        try:
            await self.wrapped.__aenter__()
        except Exception as exc:
            self._broken = True
            logger.warning(
                "MCP server %r failed to connect; skipping its tools this session: %s",
                self.server_name,
                exc,
            )
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        if getattr(self, "_broken", False):
            return False
        try:
            return await self.wrapped.__aexit__(*args)
        except Exception:
            return False

    async def get_tools(self, ctx: Any) -> Any:
        if getattr(self, "_broken", False):
            return {}
        try:
            return await self.wrapped.get_tools(ctx)
        except Exception as exc:
            self._broken = True
            logger.warning(
                "MCP server %r failed while listing tools; skipping: %s", self.server_name, exc
            )
            return {}


def _build_server_toolset(
    name: str, raw: dict[str, Any], log_dir: Path, *, init_timeout: float
) -> Any:
    """Build the bare (unwrapped) ``MCPToolset`` for one server entry.

    We build the transport ourselves rather than calling
    ``load_mcp_toolsets`` so we can **redirect stdio stderr to a log file**.
    Left on the inherited terminal, a Node-based server (chrome-devtools,
    playwright) can flip the TTY into raw mode and freeze the approval
    prompt — see :mod:`jac.cli.terminal`. Env vars are expanded here (per
    enabled server only, so a disabled server's missing secret never trips
    the others).

    A failing tool call is turned into a normal tool *result* carrying the
    error text (``tool_error_behavior='error'`` + :func:`_mcp_error_to_result`)
    rather than raised: a server error (e.g. a bad navigate) would otherwise
    exhaust pydantic-ai's retry budget and raise ``UnexpectedModelBehavior``
    out of the whole run, killing the turn. Surfacing it to the model lets it
    adapt or report, and keeps the session alive.

    Raises:
        KeyError: an env var referenced in the entry is unset (no default).
        ValueError: the entry has neither ``command`` (stdio) nor ``url``.
    """
    from pydantic_ai.mcp import MCPToolset

    raw = _expand_env(raw)
    if "command" in raw:
        from fastmcp.client.transports import StdioTransport

        log_dir.mkdir(parents=True, exist_ok=True)
        transport = StdioTransport(
            command=str(raw["command"]),
            args=[str(a) for a in raw.get("args", [])],
            env=raw.get("env"),
            cwd=str(raw["cwd"]) if raw.get("cwd") else None,
            log_file=log_dir / f"{name}.log",
        )
        return MCPToolset(
            transport,
            id=name,
            init_timeout=init_timeout,
            tool_error_behavior="error",
            process_tool_call=_mcp_error_to_result,
        )
    url = raw.get("url")
    if url:
        return MCPToolset(
            str(url),
            id=name,
            headers=raw.get("headers"),
            init_timeout=init_timeout,
            tool_error_behavior="error",
            process_tool_call=_mcp_error_to_result,
        )
    raise ValueError("server entry has neither 'command' (stdio) nor 'url' (http/sse)")


async def _mcp_error_to_result(
    ctx: Any, call_tool: Any, name: str, tool_args: dict[str, Any]
) -> Any:
    """Run an MCP tool call, converting a server-side failure into a result.

    Without this, a tool that errors raises out of pydantic-ai's tool loop;
    after the retry budget is spent the run dies with ``UnexpectedModel
    Behavior`` and the turn (and its context) is lost. We return the error
    text as the tool's result instead, so the model sees it as ordinary tool
    output and can retry with different arguments or report the failure. The
    full server stderr is in the per-server log file either way.
    """
    try:
        return await call_tool(name, tool_args)
    except Exception as exc:
        return f"MCP tool {name!r} failed: {exc}"


def build_mcp_toolsets(catalog: MCPCatalog) -> tuple[list[Any], str | None]:
    """Build the wrapped toolset list for every enabled server.

    Each enabled server is composed
    ``MCPToolset → _ResilientMCPToolset → .prefixed(name) → .defer_loading()
    → summarizing_wrap → .approval_required()``. Build is **per-server
    isolated**: a build-time failure (missing env var, malformed entry,
    fastmcp missing) is collected into the returned error string and the rest
    still load. A *connection*-time failure is absorbed at run time by
    :class:`_ResilientMCPToolset` (that server contributes zero tools rather
    than aborting the turn). The REPL surfaces build errors via ``/mcp list``.
    """
    enabled = catalog.enabled
    if not enabled:
        return [], None

    log_dir = paths.mcp_log_dir()
    wrapped: list[Any] = []
    errors: list[str] = []
    for name, srv in enabled.items():
        try:
            base = _build_server_toolset(
                name, srv.raw, log_dir, init_timeout=srv.knobs.init_timeout
            )
        except KeyError as exc:
            errors.append(f"{name}: environment variable {exc.args[0]!r} is not set")
            continue
        except ImportError as exc:  # pragma: no cover - extra declared in pyproject
            errors.append(f"{name}: MCP support unavailable (install the 'mcp' extra): {exc}")
            continue
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        ts = _ResilientMCPToolset(wrapped=base, server_name=name).prefixed(name)
        if srv.knobs.defer:
            ts = ts.defer_loading()
        ts = summarizing_wrap(ts, summarize_all=True)
        if srv.knobs.requires_approval:
            ts = ts.approval_required(_always_approve_filter)
        wrapped.append(ts)
    return wrapped, ("; ".join(errors) if errors else None)


# --- capability --------------------------------------------------------


@dataclass
class MCPCapability(AbstractCapability[Any]):
    """Attach external MCP servers' tools to Gru's toolset (Phase F, D28).

    Discovered eagerly at construction; call :meth:`reload` after editing a
    catalog file (the ``/mcp reload`` slash does this for you). Per-server
    enable/disable lives in :meth:`set_enabled` and persists to the owning
    file. ``get_toolset`` builds the wrapped toolsets from the *current*
    catalog each time it's called, so a Gru rebuild after a toggle/reload
    picks up the change without reconstructing the capability.
    """

    catalog: MCPCatalog = field(default_factory=MCPCatalog)
    last_build_error: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # Empty catalog is the signal to discover from disk. Tests that want
        # a hand-built catalog pass one in and we leave it untouched.
        if not self.catalog.servers and not self.catalog.parse_errors:
            self.catalog = load_mcp_catalog()

    def reload(self) -> None:
        """Re-scan the user + project catalogs from disk (``/mcp reload``)."""
        self.catalog = load_mcp_catalog()

    def get_toolset(self) -> Any:
        toolsets, error = build_mcp_toolsets(self.catalog)
        self.last_build_error = error
        if not toolsets:
            return None
        if len(toolsets) == 1:
            return toolsets[0]
        from pydantic_ai.toolsets import CombinedToolset

        return CombinedToolset(toolsets)

    def get_instructions(self) -> Any:
        def _instructions(_ctx: Any) -> str:
            return _render_mcp_block(self.catalog)

        return _instructions

    # ---------- enable / disable (slash-driven, persisted) ----------

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Flip a server's ``enabled`` knob and persist it. Returns success.

        ``False`` when ``name`` isn't a known server. The change is written
        back to the ``jac`` block of the file the server is defined in
        (project shadows user), then mirrored in the in-memory catalog so a
        subsequent Gru rebuild reflects it.
        """
        server = self.catalog.servers.get(name)
        if server is None:
            return False
        _persist_knob(name, server.source, "enabled", enabled)
        new_knobs = server.knobs.model_copy(update={"enabled": enabled})
        self.catalog.servers[name] = LoadedMCPServer(
            name=name, raw=server.raw, knobs=new_knobs, source=server.source
        )
        return True


def make_mcp_capability() -> MCPCapability:
    """Convenience constructor mirroring other capabilities' factories."""
    return MCPCapability()


# --- persistence -------------------------------------------------------


def _persist_knob(name: str, source: MCPSource, key: str, value: Any) -> None:
    """Set ``jac[name][key] = value`` in the owning catalog file, in place.

    Preserves ``mcpServers`` and any other ``jac`` knobs. Creates the
    ``jac`` block if absent. Best-effort: read failures re-raise so the
    slash handler can report them (a silent failure would desync disk from
    the in-memory toggle).
    """
    path = paths.project_mcp_file() if source == "project" else paths.USER_MCP_FILE
    data: dict[str, Any] = {}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    jac_block = data.setdefault("jac", {})
    server_knobs = jac_block.setdefault(name, {})
    server_knobs[key] = value
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- rendering ---------------------------------------------------------


def _render_mcp_block(catalog: MCPCatalog) -> str:
    """System-prompt hint listing the enabled tool servers.

    Anthropic's tool-search guidance: a short note describing available tool
    categories improves discovery. We list enabled server names only — the
    tool *definitions* stay deferred until search pulls them in.
    """
    names = sorted(catalog.enabled)
    if not names:
        return ""
    listed = ", ".join(names)
    block = (
        "\n\n---\n\n# MCP tool servers\n\n"
        "External tool servers are connected. Their tools are discovered on "
        "demand via tool search rather than listed upfront, so search when a "
        f"task needs one. Available servers: {listed}.\n"
    )
    if len(block.encode("utf-8")) > _INSTRUCTIONS_CAP_BYTES:
        block = (
            "\n\n---\n\n# MCP tool servers\n\n"
            f"{len(names)} external tool servers are connected; their tools are "
            "discovered on demand via tool search.\n"
        )
    return block


def _server_summaries(catalog: MCPCatalog) -> Iterable[tuple[str, LoadedMCPServer]]:
    """Stable-sorted ``(name, server)`` pairs for ``/mcp list``."""
    return sorted(catalog.servers.items())
