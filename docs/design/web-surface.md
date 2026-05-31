# Web surface — local-first UI (design)

> **Status:** **Shipped** behind `jac web serve` — control panel + streaming chat + activity
> dashboard (slices 1–3), then the **R0–R5 redesign** (chat-first light full-bleed Console +
> a full Control Panel). This is the **single design doc** for the web surface: charter,
> engine seam, scoping, the redesign, and risks.
>
> **Decisions:** [`architecture.md`](../architecture.md) §5 **D48** (charter) + **D49** (the
> SDK control plane that the top-bar switcher and panel writes now drive).
> **Surface/engine layering:** [`architecture.md`](../architecture.md) §1.5.
> **User/operator guide:** [`user-guide/web-ui.md`](../user-guide/web-ui.md).

JAC has three surfaces: the **CLI** (interactive REPL), **A2A** (a headless server that lets
peer agents drive a read-only guest Gru), and this — a **local web UI**: a browser-based chat
plus a control panel for managing profiles, providers, secrets, config, MCP, A2A, skills, and
sessions.

## What it is — and the one constraint that shapes everything

**JAC's web UI is a single-user, local-first control surface. It is not, and will never be, a
multi-tenant hosted service.**

This is not a limitation we hope to lift later — it's a deliberate scope choice that keeps the
whole design honest:

- **One operator.** The person running `jac web serve` is the same person who owns the
  workspace, the API keys, and the sessions. There are no accounts, no per-user isolation, no
  login.
- **Loopback by default.** The server binds `127.0.0.1`. The loopback boundary *is* the
  security model. Binding to a non-loopback address is allowed but prints a loud warning,
  because the settings panel reads and writes API keys — on a shared network that would be a
  credential leak.
- **No new runtime mode.** The web UI is a *renderer + a management API* over the exact same
  `SessionDriver` / `EventBus` / capability stack the CLI uses (see [`jac.sdk`](../../src/jac/sdk.py)).
  It adds zero agent-engine concepts. This keeps it on the right side of the CLAUDE.md
  anti-pattern *"don't add another runtime mode when the answer is one more surface over the
  existing engine."*

If a feature would only make sense for multiple simultaneous users, it is out of scope by
definition.

## Why this is cheap to build

The seam already exists and was built on purpose. [`jac.sdk`](../../src/jac/sdk.py) names "a
browser backend" as a target consumer; A2A already proves a second non-CLI surface works
against the same engine. Concretely:

| Need | Already provided | Reuse |
| --- | --- | --- |
| Run a turn, stream events | `SessionDriver.run_turn()` + `EventBus` (`jac.sdk`) | 100% |
| Runtime mutations (switch model/profile, MCP/skill toggles) | `SessionController` (`runtime/control.py`, D49) | 100% — the same verbs the CLI slash handlers call |
| HITL approval | `ApprovalRequest` event carrying an `asyncio.Future` | 100% — resolve the future from a browser POST instead of a terminal prompt |
| List/create/delete sessions | `Session.list_summaries/new/resume/delete` | 100% |
| Profiles CRUD | `profiles_crud.*` + `profiles_io.profile_to_yaml/load_profile_from_yaml` | 100% |
| Providers + required keys + pricing | `providers/registry.py` | 100% |
| Secrets get/set/unset | `secrets.get_backend()` + `secrets.resolve()` | 100% |
| Scope-aware config-group writes + per-field precedence | `workspace/config_io.py` | 100% |
| Token/cost breakdown | `usage.jsonl` + `UsageTracker` | 100% |
| Live sub-agent state | `runtime/sub_agent._pending_spawns` | read-only |

The web stack itself adds **no new dependencies**: Starlette, uvicorn, sse-starlette, and
jinja2 are already in the lock file (transitively via fasta2a / pydantic-ai).

## Framework choice — Starlette + HTMX

Considered: FastAPI+HTMX, Chainlit, NiceGUI, Reflex, Streamlit/Gradio.

**Chosen: Starlette + Jinja2 + HTMX**, with SSE (`sse-starlette`) for the event firehose and
browser POSTs for the HITL approval/clarify replies.

- **Zero new deps** (all in the tree already). For a local-first tool that should work offline,
  not pulling a framework matters.
- **Total control of markup** — the activity dashboard (live sub-agent cards, file-change feed)
  and the precedence-aware config forms need custom UI; nothing fights us.
- **Mirrors the existing surface boundary.** Everything lives under `src/jac/web/`, exactly
  like `src/jac/cli/`.

Rejected: **FastAPI+HTMX** (adds a dependency for a typed REST surface a single-user local
panel doesn't need — revisit only if a third-party frontend ever wants an OpenAPI contract);
**Chainlit** (owns the page, fights the settings panel and theming); **NiceGUI** (opinionated,
harder to embed next to A2A); **Reflex** (heavy full-stack, overkill for v1); **Streamlit/Gradio**
(the rerun-the-script model fights streaming agents and a persistent settings panel).

## Session & working-directory scoping

A web server runs from *some* directory. Which sessions does it show? JAC already answers this:
`paths.project_state_root()` resolves to `<project_root>/.agents` when launched inside a project
(a `.git` or `.agents` marker at/above CWD), or `~/.jac` when "loose". So:

- **`jac web serve` inside project A shows only project A's sessions** — falls out for free.
- **`jac web serve` in a loose directory shows the global `~/.jac` session pool.**

**v1 scope (deliberate):** the panel shows exactly one scope — whichever the launch directory
resolves to. There is no grouped "this project / your whole workspace" view, and no switching to
a session from a *different* project. That cross-scope view has real discrepancies to resolve (a
session's `messages.json` references that project's files; resuming it elsewhere is ambiguous),
so it's deferred. The header states which scope is active.

## Module layout

```
src/jac/web/
  app.py        # Typer `jac web serve` command + uvicorn launch + loopback guard
  server.py     # create_app() — Starlette app, routes (panel + chat), Jinja2 wiring
  panel.py      # pure read-side: assemble view-models from the management APIs; sidebar_context()
  actions.py    # write-side: form POST handlers calling profiles_crud / secrets / config_io / Session
  chat.py       # WebChatManager — drives one live session; bus→SSE; HITL futures; dashboard/environment/history
  templates/    # Jinja2: base + chat + settings/profiles/keys/<domain> drawer fragments
  static/       # jac.css (light theme) + chat.js (SSE; queued HITL bar; markdown; session repaint)
```

The chat surface reuses the shared engine bootstrap and control plane:

```
src/jac/runtime/bootstrap.py   # build_session_runtime() — the engine half of a session,
                               # shared by cli/repl.py and web/chat.py
src/jac/runtime/control.py     # SessionController — switch model/profile, MCP/skill toggles (D49)
src/jac/workspace/config_io.py # scope-aware config-group writes + per-field precedence
```

`create_app()` is the embeddable entry point (testable without a running server). `app.py` is
the thin CLI wrapper, parallel to `cli/a2a.py`.

## Slices (how it was built)

1. **Slice 1 — control panel.** Pure CRUD over profiles, providers/keys, secrets, and sessions.
   No agent driving, no streaming. Highest reuse, lowest risk. *(The standalone overview/Sessions
   tabs were later folded into the chat-centric shell, then the redesign.)*
2. **Slice 2 — chat + HITL.** [`web/chat.py`](../../src/jac/web/chat.py) drives one live session
   through the **shared engine**: the REPL's bootstrap was extracted to
   [`runtime/bootstrap.py`](../../src/jac/runtime/bootstrap.py) so the CLI and web build the
   *identical* Gru + capabilities + driver. A persistent consumer task drains the `EventBus` and
   **broadcasts** JSON frames to every connected SSE subscriber (`/chat/stream`); HITL approval +
   clarify are resolved by browser POSTs that complete the same `asyncio.Future` the CLI resolves
   at a terminal prompt. One turn at a time (single-user charter).
3. **Slice 3 — activity dashboard.** A live token/cost meter, minion cards (merging the
   `SubAgentSpawned`/`SubAgentCompleted` bus lifecycle with `_pending_spawns`), and a
   files-changed list — backed by a `GET /chat/status` snapshot the browser polls, plus a
   `GET /chat/environment` block (A2A peers, MCP servers, loaded skills) fetched once per session.

Two non-obvious correctness choices from this phase are **kept** in the redesign because each
fixed a real bug:

- **`stream=False`, not `run_stream`.** HITL approval is driven by `agent.run()`'s deferred-tool-
  call handling, which `run_stream` silently bypasses — streaming would let a gated tool execute
  with **no confirmation prompt**. The reply lands on `RunCompleted`. (Token-level streaming *with*
  approval needs the `agent.iter()` graph path — a deferred enhancement, not worth losing HITL
  correctness.)
- **Broadcast, not a single shared queue.** Each SSE connection gets its own queue, closed on
  `pagehide` — otherwise a dangling generator from a prior page-load steals frames (the "first
  message not shown" bug) and unclosed streams saturate the browser's per-host connection pool.

## Redesign (R0–R5)

Slices 1–3 proved the *plumbing*. The redesign invested in the **management surface** and the
**look & feel**: every config domain editable from the browser, and a calm light theme replacing
the dark black-and-yellow. The engine is unchanged; only the surface was rewritten.

### The two-surface model

The single most important framing — everything in the UI is one of two natures (plus an
observability lens overlaying both):

| | **Console** (live) | **Control Panel** (management) |
| --- | --- | --- |
| Nature | Stateful, ephemeral, event-driven, *one turn at a time* | Stateless CRUD over files; do-anytime |
| CLI analogue | REPL loop + mid-turn slash commands | `jac init/profiles/keys`, `/mcp`, `/a2a peer …` |
| Interaction | Streaming output, HITL approvals, live activity | Forms, toggles, editors, validation |
| Transport | SSE + POST (approval futures) | Request/response, HTMX fragment swaps |
| Reloads? | **Never** (holds the SSE connection) | Free (no realtime) |

The **observability lens** (cost/usage on the Console's activity rail and a Dashboard) is not a
third surface — it overlays both, because "cost is the metric."

### Visual design language

**Tone:** calm, light, content-first — whitespace, hairline borders, one rationed accent, so the
conversation and the data are what you see. Light theme is the only theme in v1.

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#f6f7f9` | App background |
| `--surface` | `#ffffff` | Chat canvas, cards, drawers |
| `--border` | `#e6e8ec` | Hairline separators |
| `--text` | `#1c1f26` | Primary text |
| `--text-muted` | `#6b7280` | Secondary / metadata |
| `--accent` | `#5468d4` | Primary actions, links (calm indigo) |
| `--ok` / `--warn` / `--danger` | `#2f9e6b` / `#c98a23` / `#d1495b` | Status, not decoration (key set / budget warn / hard-stop) |

Accent is rationed to primary buttons, the active nav item, and focus rings. Tiers
(small/medium/large) become three muted neutral hues. The Console is full-height/full-width with
both rails collapsible; message **text** keeps a ~72–80ch reading measure while the canvas, tool
blocks, and code render wider.

### Information architecture

Chat-first: you land in the Console and rarely leave it. Management opens **over** the
conversation as HTMX-loaded slide-over drawers; the SSE connection stays alive underneath.

```
Console  (home — the one persistent SSE page)
├─ top bar (everywhere): scope ▾ · profile ▾ · model/tier ▾ · ● doctor · token meter
├─ left rail (collapsible): + New chat · session list · MANAGE ▾ · ⚙ Settings
├─ center: chat transcript + streaming + HITL pinned bar + input   ← full-bleed
└─ right rail (collapsible): ACTIVITY — tokens · minions · files · plan · environment

MANAGE drawers (open over the Console, chat stays live):
  Dashboard · Profiles · Keys · Config · Skills · MCP · A2A · Context · Memory · Providers
```

**Why drawers, not navigation:** chat-first means never losing the conversation. A full page-nav
would drop the SSE stream and force a repaint. Drawers (`hx-get` into an overlay) keep the
Console mounted; a heavy editor can expand to a full pane while the Console stays in the
background DOM (SSE intact).

### The Control Panel — every domain

Each row is a drawer. All read via `panel.*` view-models, all write via existing CRUD (no
reimplementation).

| Domain | Controls | Backing API |
| --- | --- | --- |
| **Profiles** | List, set-default, create/edit (form **and** raw YAML), remove; tier model lists | `profiles_crud`, `profiles_io` |
| **Keys** | Status grid (set/missing/source), set, unset; backend selector. **Never display values.** | `secrets`, `providers/registry` |
| **Config** | `compaction` · `budget` · `cost` · `secrets` — precedence + scope forms (below) | `config.*` + `workspace/config_io.py` |
| **Skills** | List active + shadowed, view/edit `SKILL.md`, reload, **"Use in chat"** | `skills_capability` |
| **MCP** | Server list, toggle enabled/defer/approval, edit, add, reload, show errors, list tools | `mcp_capability` + `mcp.json` |
| **A2A** | Outbound peers CRUD (bearer/api-key/oauth2), inbound audit, agent-card preview | `a2a_capability` |
| **Providers** | Catalog view + edit user overlay (pricing, required_env) | `providers/registry`, overlay |
| **Context** | Edit project + user `AGENTS.md`; prompt overlays | `workspace` loaders |
| **Memory** | View entries; manual remember/forget (**read-mostly** — JAC-managed) | memory loaders + `remember`/`forget` |
| **Sessions** | List, open, delete, prune-by-age (from the left rail) | `Session.*` |

Read-only (never editable in the UI): `messages.json`, `usage.jsonl`, A2A
`inbound.jsonl`/contexts, tool-result cache.

### Config editing: scope + precedence (the differentiator)

Generic agent UIs edit "the config." JAC's value is **layered precedence**, so every config form
is precedence- and scope-aware. Each field shows:

1. **Effective value** — what JAC actually resolves right now.
2. **Source badge** — which layer it came from (`CLI · env · .env · project · user · defaults`).
3. **Editing-layer toggle** — write to **project** (`.agents/config.yaml`) or **user**
   (`~/.jac/config.yaml`); the write touches only that layer's file.
4. **Locked rows** — if a higher-precedence layer (env/CLI) wins, the control is disabled with an
   explainer; no silent "edited but nothing happened."
5. **Advanced: raw YAML** — the actual file for the chosen scope, validated on save.

This is implemented in [`workspace/config_io.py`](../../src/jac/workspace/config_io.py) — it
replays the loader precedence per field rather than forking the precedence logic, so the badges
can't drift from the real resolution order.

### Mid-session model/profile switch — via the control plane (D49)

Switching model or profile from the top bar rebuilds Gru *in the live session* — preserving the
message history, bus, and pending-approval state. Critically, this is **not** web-local code: the
switcher calls the same [`SessionController`](../../src/jac/runtime/control.py) verbs
(`switch_model` / `switch_profile`) the CLI's `/model` and `/profile` call. The control plane
snapshots `os.environ`, applies the new profile/model env, rebuilds Gru, and **rolls back on
failure** so a bad switch (e.g. a missing key) leaves the running agent untouched. The web refuses
a rebuild while a turn is in flight and emits an error frame on failure.

This is D49's whole point: the rebuild dance lives **once**, not copied into each surface. (The
pre-D49 web carried a hand-forked `_rebuild` that had already drifted from the REPL and a bug
where toggling MCP wrote the file but never rebuilt Gru — both gone now.)

### Doctor / readiness

JAC's charter is "be loud about missing required config." The UI turns that into a single
readiness view (a top-bar dot + a Dashboard panel): model not set, missing required keys per
profile, MCP server build failures, A2A unsafe-bind warnings, budget at/over hard-stop. Green
when nothing blocks a run; amber for warnings; red when a run would fail.

### Slice plan (R0–R5, all shipped)

| Slice | Scope |
| --- | --- |
| **R0** | Shell + light theme + full-bleed Console; chat wired to the engine + HITL; top bar (scope/profile/model/doctor/tokens). |
| **R1** | Config drawers — precedence-aware forms for compaction/budget/cost/secrets + raw-YAML escape hatch + scope toggle. |
| **R2** | Profiles & Keys; top-bar model/profile switch via the control plane (rebuild + rollback guard). |
| **R3** | MCP & A2A — server/peer management, toggles, errors, audit. |
| **R4** | Skills, Context, Providers, Memory editors. |
| **R5** | Dashboard & doctor; HITL-disconnect failsafe. |

Remaining: visual polish + a narrow-width responsive breakpoint.

## Risks & how we scope around them

1. **The engine assumes one interactive session per process.** `EventBus` is single-consumer,
   `_pending_spawns` is a module global, the usage tracker is per-session. **Mitigation:** v1 is
   single-user by charter; the web owns exactly one live session. True multi-session would need
   per-session keying of `_pending_spawns` — explicitly out of scope.
2. **HITL over a socket can disconnect mid-approval.** The approval `Future` blocks the agent
   loop; a closed tab would hang the turn. **Mitigation:** a disconnect/timeout path resolves the
   future as "denied" after a grace period.
3. **Secrets transit HTTP.** On loopback this is fine. **Mitigation:** bind `127.0.0.1`; loudly
   warn on any non-loopback `--host`; never echo secret values to the browser (status only). The
   keyring backend still stores server-side.
4. **CLI + web writing the same session race on `messages.json`.** **Mitigation:** don't share a
   live session across surfaces simultaneously; the web owns its own active session.
5. **Precedence-resolver drift.** If the config forms reimplemented loader order, the source
   badges would lie. **Mitigation:** drive `config_io.py` from the same ordering the loader
   exposes; don't fork it.

## Non-goals

- Multi-user / multi-tenant hosting (charter).
- Authentication / accounts (loopback is the boundary).
- Remote access without an explicit non-loopback bind.
- Cross-project session browsing (deferred; see scoping).
- Token-level streaming *with* HITL (needs `agent.iter()`; deferred).
- A reactive SPA framework / dark theme (light-only for v1).
