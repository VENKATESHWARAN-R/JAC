# Web surface — local-first UI (design)

> **Status:** Slice 1 (control panel) landing. Slice 2 (chat + HITL) and Slice 3
> (minion dashboard) are designed here but not yet built.
> **Decision:** [`architecture.md`](../architecture.md) §5 **D48**.

JAC has two surfaces today: the **CLI** (interactive REPL) and **A2A** (a
headless server that lets peer agents drive a read-only guest Gru). This adds a
third: a **local web UI** — a browser-based chat plus a settings panel for
managing profiles, providers, secrets, and sessions.

## What it is — and the one constraint that shapes everything

**JAC's web UI is a single-user, local-first control surface. It is not, and
will never be, a multi-tenant hosted service.**

This is not a limitation we hope to lift later — it's a deliberate scope choice
that keeps the whole design honest:

- **One operator.** The person running `jac web serve` is the same person who
  owns the workspace, the API keys, and the sessions. There are no accounts, no
  per-user isolation, no login.
- **Loopback by default.** The server binds `127.0.0.1`. The loopback boundary
  *is* the security model. Binding to a non-loopback address is allowed but
  prints a loud warning, because the settings panel reads and writes API keys —
  on a shared network that would be a credential leak.
- **No new runtime mode.** The web UI is a *renderer + a management API* over
  the exact same `SessionDriver` / `EventBus` / capability stack the CLI uses
  (see `jac.sdk`). It adds zero agent-engine concepts. This keeps it on the
  right side of the CLAUDE.md anti-pattern *"don't add another runtime mode when
  the answer is one more surface over the existing engine."*

If a feature would only make sense for multiple simultaneous users, it is out of
scope by definition.

## Why this is cheap to build

The seam already exists and was built on purpose. [`jac.sdk`](../../src/jac/sdk.py)
names "a browser backend" as a target consumer; A2A already proves a second
non-CLI surface works against the same engine. Concretely:

| Need | Already provided | Reuse |
| --- | --- | --- |
| Run a turn, stream events | `SessionDriver.run_turn()` + `EventBus` (`jac.sdk`) | 100% |
| HITL approval | `ApprovalRequest` event carrying an `asyncio.Future` | 100% — resolve the future from a WebSocket handler instead of a terminal prompt |
| List/create/delete sessions | `Session.list_summaries/new/resume/delete` | 100% |
| Profiles CRUD | `profiles_crud.*` + `profiles_io.profile_to_yaml/load_profile_from_yaml` | 100% |
| Providers + required keys + pricing | `providers/registry.py` | 100% |
| Secrets get/set/unset | `secrets.get_backend()` + `secrets.resolve()` | 100% |
| Token/cost breakdown | `usage.jsonl` + `UsageTracker` | 100% |
| Live sub-agent state | `runtime/sub_agent._pending_spawns` | read-only |

The web stack itself adds **no new dependencies**: Starlette, uvicorn,
websockets, sse-starlette, and jinja2 are already in the lock file (transitively
via fasta2a / pydantic-ai).

## Framework choice — Starlette + HTMX

Considered: FastAPI+HTMX, Chainlit, NiceGUI, Reflex, Streamlit/Gradio.

**Chosen: Starlette + Jinja2 + HTMX**, with SSE for the event firehose and a
WebSocket for the bidirectional approval channel.

- **Zero new deps** (all in the tree already). For a local-first tool that should
  work offline, not pulling a framework matters.
- **Total control of markup** — the future "minion dashboard" (live sub-agent
  cards, file-change feed) needs custom UI; nothing fights us.
- **Mirrors the existing surface boundary.** Everything lives under
  `src/jac/web/`, exactly like `src/jac/cli/`.

Rejected:

- **FastAPI+HTMX** — fine, but adds a dependency for a typed REST surface a
  single-user local panel doesn't need yet. Revisit if a third-party/native
  frontend ever wants an OpenAPI contract.
- **Chainlit** — fastest path to *chat*, but owns the page and fights the
  settings panel and custom theming. Great demo, poor long-term home.
- **NiceGUI** — pure-Python and pleasant, but opinionated and harder to embed
  next to A2A or theme bespoke-ly.
- **Reflex** — the right tool for the *eventual* rich dashboard, but a heavy
  full-stack framework that's overkill for v1. Reconsider for Slice 3 only if
  the dashboard graduates into a flagship feature.
- **Streamlit/Gradio** — the rerun-the-script model fights streaming agents and
  a persistent settings panel.

HTMX is layered in progressively: **Slice 1 is plain server-rendered forms with
no JavaScript at all** (full-page POST/redirect), so it works offline with zero
vendored assets. HTMX (vendored, not CDN) arrives with Slice 2 where partial
updates and the streaming chat actually need it.

## Session & working-directory scoping

A web server runs from *some* directory. Which sessions does it show?

JAC already answers this: `paths.project_state_root()` resolves to
`<project_root>/.agents` when launched inside a project (a `.git` or `.agents`
marker at/above CWD), or `~/.jac` when "loose". `Session.list_summaries()` reads
from there. So:

- **`jac web serve` inside project A shows only project A's sessions.** This is
  the v1 behavior, and it falls out for free — no special-casing.
- **`jac web serve` in a loose directory shows the global `~/.jac` session
  pool** (the same place a loose `jac` REPL persists to).

**v1 scope (deliberate):** the panel shows exactly one scope — whichever the
launch directory resolves to. We do **not** yet show a grouped "this project /
your whole workspace" view, and we do **not** support switching to a session
from a *different* project. That cross-scope view is a real feature with real
discrepancies to resolve (a session's `messages.json` references that project's
files; resuming it elsewhere is ambiguous), so it's deferred until the
single-scope panel is solid. The header states which scope is active so the user
always knows what they're looking at.

## Module layout

```
src/jac/web/
  __init__.py
  app.py        # Typer `jac web serve` command + uvicorn launch + loopback guard
  server.py     # create_app() — Starlette app, routes, Jinja2 wiring
  panel.py      # pure read-side: assemble view models from the management APIs
  actions.py    # write-side: form POST handlers calling profiles_crud / secrets / Session
  templates/    # Jinja2: base.html + overview/profiles/keys/sessions
  static/        # jac.css (+ vendored htmx.min.js from Slice 2)
```

`create_app()` is the embeddable entry point (testable without a running
server). `app.py` is the thin CLI wrapper, parallel to `cli/a2a.py`.

## Slices

1. **Slice 1 — control panel (this change).** Pure CRUD over profiles,
   providers/keys, secrets, and sessions, plus an overview card (workspace
   scope, default profile, secrets backend, model, token totals). No agent
   driving, no streaming, no concurrency concerns. Highest reuse, lowest risk.
   Useful on its own as a GUI for `jac init` / `profiles` / `keys` / `sessions`.
2. **Slice 2 — streaming chat + HITL.** A `WebRenderer` consuming the `EventBus`
   over SSE; a WebSocket carrying approval responses (resolve the
   `ApprovalRequest` future). Drives one live session via the SDK bootstrap.
   This is where the real engineering lives (see Risks).
3. **Slice 3 — minion dashboard.** Live sub-agent cards, file-change feed, token
   meter — built on the Slice 2 event feed. Optional richer frontend here.

## Risks & how we scope around them

These are real and worth stating up front (most bite only at Slice 2+):

1. **The engine assumes one interactive session per process.** `EventBus` is
   single-producer/single-consumer, `_pending_spawns` is a module global, the
   usage tracker is per-session. The CLI gets away with this because it's one
   human, one loop. **Mitigation:** v1 is single-user by charter; Slice 2 adds a
   `SessionManager` owning one `(bus, gru, driver)` tuple for the single active
   chat session. True multi-session would need per-session keying of
   `_pending_spawns` — explicitly out of scope.
2. **HITL over a socket can disconnect mid-approval.** The approval `Future`
   blocks the agent loop; a closed browser tab would hang the turn. The terminal
   never had this failure mode. **Mitigation (Slice 2):** a cancel/timeout path
   that resolves the future as "denied" on disconnect.
3. **Secrets transit HTTP.** On loopback this is fine. **Mitigation:** bind
   `127.0.0.1` by default; loudly warn on any non-loopback `--host`. Document
   that loopback is the boundary. The keyring backend still stores server-side.
4. **CLI + web writing the same session race on `messages.json`.** **Mitigation:
   ** don't share a live session across surfaces simultaneously; the web UI owns
   its own active session.
5. **Transport split.** SSE for the one-way event stream (we already ship
   `sse-starlette`); WebSocket for approval replies and user interrupt. HTMX's
   SSE extension handles the streaming chat.

## Non-goals

- Multi-user / multi-tenant hosting (charter).
- Authentication / accounts (loopback is the boundary).
- Remote access without the operator explicitly opting into a non-loopback bind.
- Cross-project session browsing (deferred; see scoping).
