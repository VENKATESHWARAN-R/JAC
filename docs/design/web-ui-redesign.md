# Web UI redesign — design (proposal)

> **Status:** **Implemented (R0–R5) behind `jac web serve`** — light full-bleed
> Console + the full Control Panel (profiles, keys, config, MCP, A2A, skills,
> context, providers, memory, dashboard), live model/profile switch with a
> rollback guard, and HITL-disconnect failsafe. `just check` green (762 tests,
> incl. new `test_web_config` / `test_web_switch` / `test_web_panels`). The
> shipped baseline it replaced is in [`web-surface.md`](web-surface.md) (D48).
> **Still to fold on review sign-off:** merge the durable parts of this doc into
> `web-surface.md` (the SSOT), record the redesign as a new decision in
> [`architecture.md`](../architecture.md) §5, and update the slice/status table in
> [`progress.md`](../progress.md). Kept separate until then so the SSOT isn't
> rewritten before the redesign is reviewed in the app.

## 1. Why redesign

The shipped surface proved the *plumbing* (shared engine, SSE event firehose,
HITL-over-POST, activity dashboard). What it did **not** invest in is the
**management surface** and the **look & feel**:

- Management today is two thin pages (Profiles as raw-YAML paste, Keys status).
  The user's goal is **everything configurable from the browser** — A2A, skills,
  MCP, profiles, models, the `config.yaml` groups, providers, prompts, context,
  memory, sessions.
- The theme is a dark black-and-yellow contrast; the chat is a fixed-width column.

This redesign keeps the engine and rewrites the surface: a **chat-first** app
where the conversation owns the screen, management slides in over it, and the
config editors are honest about JAC's layered precedence.

## 2. Charter (unchanged — these constrain every choice)

Carried verbatim from [`web-surface.md`](web-surface.md); the redesign does not
relax any of them:

- **Single-user, local-first.** No accounts, no multi-tenant, ever.
- **Loopback is the security model.** Bind `127.0.0.1`; a non-loopback `--host`
  is allowed but warns loudly (the panel reads/writes API keys in the clear).
- **Not a new runtime mode.** The web UI is a *renderer + management API* over
  the same `SessionDriver` / `EventBus` / capability stack the CLI drives via
  `runtime/bootstrap.build_session_runtime`. It adds zero agent-engine concepts.
- **One active chat session per process.** `EventBus` is single-consumer,
  `_pending_spawns` is a module global, the usage tracker is per-session. The web
  owns exactly one live session; true multi-session is out of scope.
- **Reuse CRUD, never reimplement.** Mutations call `profiles_crud`, `secrets`,
  `Session`, `mcp_capability`, `a2a_capability`, `skills_capability` — the same
  paths the CLI uses.
- **Project-vs-global scope follows the launch directory** via
  `paths.project_state_root()`. Cross-project browsing stays deferred.

## 3. The two-surface model

The single most important framing. Everything in the UI is one of two natures
(plus an observability lens that overlays both):

| | **Console** (live) | **Control Panel** (management) |
| --- | --- | --- |
| Nature | Stateful, ephemeral, event-driven, *one turn at a time* | Stateless CRUD over files; do-anytime |
| CLI analogue | REPL loop + mid-turn slash commands | `jac init/profiles/keys`, `/mcp`, `/a2a peer …` |
| Interaction | Streaming output, HITL approvals, live activity | Forms, toggles, editors, validation |
| Transport | SSE + POST (approval futures) | Request/response, HTMX fragment swaps |
| Reloads? | **Never** (holds the SSE connection) | Free (no realtime) |

**Observability lens** (not a third surface): cost/usage data overlaid on the
Console's activity rail and a dedicated Dashboard — because "cost is the metric."

## 4. Decisions locked in the brainstorm

1. **Chat-first center of gravity.** The Console is home; you live in it.
2. **Hybrid: MPA + HTMX for the panel, one SSE-driven Console page.** No SPA, no
   build step, stays in the Starlette + Jinja2 + HTMX + SSE stack already in the
   tree.
3. **Structured forms + raw-YAML escape hatch** for config editing, with
   layered-precedence visibility.
4. **Full-bleed Console.** The chat takes maximum real estate; side rails
   collapse so it can fill the viewport. No narrow boxed column.
5. **Light, subtle theme.** White / light-grey backgrounds, restrained accent;
   replace the black-and-yellow contrast.

## 5. Visual design language

**Tone:** calm, light, content-first. Lots of whitespace, hairline borders, soft
shadows, one restrained accent. The UI should recede so the conversation and the
data are what you see.

**Color tokens** (CSS custom properties; light theme is the default and only
theme for v1):

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#f6f7f9` | App background (slightly grey) |
| `--surface` | `#ffffff` | Chat canvas, cards, drawers |
| `--surface-2` | `#f1f3f5` | Subtle fills (rails, code blocks) |
| `--border` | `#e6e8ec` | Hairline separators |
| `--text` | `#1c1f26` | Primary text (near-black slate) |
| `--text-muted` | `#6b7280` | Secondary / metadata |
| `--accent` | `#5468d4` | Primary actions, links (calm indigo) |
| `--accent-soft` | `#eef0fb` | Selected/active backgrounds |
| `--ok` | `#2f9e6b` | Healthy / set / enabled |
| `--warn` | `#c98a23` | Warnings (used sparingly) |
| `--danger` | `#d1495b` | Stop / delete / missing |

- **Accent is rationed.** Primary buttons, the active nav item, and focus rings
  only. Everything else is neutral.
- **Semantic colors are status, not decoration** — a green dot for "key set,"
  amber for a budget warning, red for a hard-stop or a failed MCP server.
- **Tiers** (small/medium/large), previously yellow minion accents, become three
  muted neutral hues, readable on white.
- **Typography:** system UI stack for chrome; a mono stack for code/tool args.
  One or two sizes, generous line-height.
- **Density:** comfortable, not cramped. Cards have real padding; the activity
  rail is scannable at a glance.

**Full-bleed layout rules:**

- The Console is a full-height, full-width fluid grid — no centered max-width
  wrapper around the whole app.
- Both side rails are **collapsible**; collapsed, the chat canvas spans the
  entire viewport edge-to-edge.
- Message **text** gets a comfortable reading measure (~72–80ch) so long
  paragraphs stay legible, but the canvas, tool blocks, and code render wider.
  (This reading measure is a tunable, not a boxed column with big empty margins.)

## 6. Information architecture / sitemap

Chat-first: you land in the Console and rarely leave it. Management opens **over**
the conversation as slide-over drawers (HTMX-loaded); heavy editors expand to a
full pane within the drawer. The SSE connection stays alive underneath the whole
time.

```
Console  (home — the one persistent SSE page)
│
├─ top bar (everywhere): scope ▾ · profile ▾ · model/tier ▾ · ● doctor · token meter
│
├─ left rail (collapsible): + New chat · session list · MANAGE ▾ · ⚙ Settings
│
├─ center: chat transcript + streaming + HITL pinned bar + input  ← full-bleed
│
└─ right rail (collapsible): ACTIVITY — tokens · minions · files · plan · environment

MANAGE drawers (open over the Console, chat stays live):
  Dashboard   — cost/observability + doctor readiness
  Profiles    — list + structured form (+ raw YAML), set-default, tier model lists
  Keys        — credential status grid, set/unset, backend selector
  Config      — compaction · budget · cost · secrets  (precedence + scope forms)
  Skills      — active + shadowed, view/edit SKILL.md, reload
  MCP         — servers, knob toggles, add/edit, build errors, discovered tools
  A2A         — server control, token, peers CRUD + auth, inbound audit, card
  Context     — AGENTS.md (project/user), prompt overlays
  Memory      — view (+ manual remember/forget)  [read-mostly]
  Providers   — catalog + user overlay (pricing, required_env)
  Sessions    — managed from the left rail (open/delete/prune)
```

**Why drawers, not navigation:** chat-first means never losing the conversation.
A full page-nav would drop the SSE stream and force a repaint. Drawers (HTMX
`hx-get` into an overlay) keep the Console mounted; closing one refreshes the
relevant slice of the activity rail. *Fallback:* if a given editor is too large
for a drawer in practice, it expands to a full pane while the Console stays in
the background DOM (SSE intact).

## 7. The Console in detail

```
┌──────────────────────────────────────────────────────────────────────┐
│ JAC   scope: project ▾   profile: claude ▾   model: sonnet-4-6 ▾  ● ok │
├───────────┬───────────────────────────────────────────┬──────────────┤
│ + New     │  user: refactor the auth module            │ ACTIVITY     │
│ SESSIONS  │  gru:  I'll start by reading…              │ tokens 34%   │
│ · now     │  🔧 read_file auth.py                       │ cache 71%hit │
│ · 14:30   │  gru:  here's the plan…                     │ ──────────── │
│ · 12:05   │                                            │ MINIONS      │
│ ───────── │  ┌─ approve? run_shell ──────────────────┐ │ minion-1 med │
│ MANAGE ▾  │  │ pytest -q    [Approve][Deny][Redirect]│ │  2/5 · 4 trn │
│ Dashboard │  └────────────────────────────────────────┘ │ ──────────── │
│ Profiles  │                                            │ FILES        │
│ Config    │  ┌────────────────────────────────────────┐ │ ~ auth.py    │
│ MCP …     │  │ message…                         [Send] │ │ + test_*.py  │
│ ⚙ Settings│  └────────────────────────────────────────┘ │ ENV: mcp/a2a │
└───────────┴───────────────────────────────────────────┴──────────────┘
```

**Top bar (persistent on every surface):**

- **Scope chip** — `project` / `global`, from `paths.project_state_root()`.
- **Profile chip** (dropdown) — switch profile = `/profile`. Rebuilds Gru (§10).
- **Model/tier chip** (dropdown) — the profile's tiers + models, plus an ad-hoc
  `provider:id` field = `/model`. Rebuilds Gru (§10).
- **Doctor dot** — green/amber/red readiness (§13); click → Dashboard doctor.
- **Token meter** — compact session %/budget; click → Dashboard.

**Center (chat):**

- Streaming assistant output via SSE frames (`TextDelta` → `RunCompleted`),
  rendered through the existing escape-first markdown renderer.
- Tool calls render as compact chips (`ToolCallStarted/Completed/Failed`) with
  args as labelled rows (long values in a scrollable `<pre>`), not raw JSON.
- **HITL pinned bar** between transcript and input — approvals/clarifies render
  one-at-a-time with a `+N more waiting` queue; resolved ones leave a one-line
  notice in the scroll-back. (Carried from the shipped design — it works.)

**Right rail — ACTIVITY (collapsible):**

- **Token/cost meter** — session in/out/total, cache hit %, project total,
  budget %. (Same data as `/tokens`.)
- **Minion cards** — one per active sub-agent: tier, model, round-trips/cap,
  turns, objective, status (running/waiting). Merges `SubAgentSpawned/Completed`
  bus events with `_pending_spawns`. (Same data as `/spawns`.)
- **Files changed** — `write_file`/`edit_file` targets that exist on disk.
- **Plan checklist** — `PlanReplaced` / `PlanStepUpdated` steps.
- **Environment** — active A2A peers, MCP servers, loaded skills (fetched once
  per session; refreshed when a drawer changes them).

## 8. The Control Panel — every domain

Each row is a drawer. All read via `panel.*` view-models, all write via existing
CRUD (no reimplementation).

| Domain | Controls | Backing API | Notes |
| --- | --- | --- | --- |
| **Profiles** | List, set-default, create/edit (form **and** raw YAML), remove | `profiles_crud`, `profiles_io` | Form: tiers (small/medium/large lists), active_tier, env, requires_env, a2a sub-block |
| **Models** | Per-session active model/tier switcher (top bar) + tier lists (in Profiles) | top bar → §10 | Switcher = `/model` + `/profile` over the web |
| **Keys** | Status grid (set/missing/source), set, unset; backend selector | `secrets.resolve/get_backend`, `providers/registry` | **Never display values.** Backend = keyring/dotenv/env-only |
| **Config: compaction** | strategy, max_context_tokens, model overrides, warn/auto/refuse/target pct | `config.CompactionSettings` | Precedence + scope form (§9) |
| **Config: budget** | session_input/total, project_total, warn/hardstop pct | `config.BudgetSettings` | Precedence + scope form |
| **Config: cost** | tool_result_threshold, no/force summarize lists, sub_agent_bidirectional | `config.CostSettings` | Precedence + scope form |
| **Config: secrets** | backend | `config.SecretsSettings` | Precedence + scope form |
| **Skills** | List active + shadowed (source badge), view/edit `SKILL.md`, reload, scope | `skills_capability` | Markdown + frontmatter editor; reload re-scans disk |
| **MCP** | Server list, toggle enabled/defer/approval, edit timeout, add/edit server, reload, show build/parse errors, list discovered tools | `mcp_capability` + `mcp.json` | JSON-backed; structured form over the `mcpServers` + `jac` knobs |
| **A2A** | Start/stop server, show bearer token, peers CRUD (bearer/api-key/oauth2), inbound audit log, agent-card preview, `allow_private_peers` | `a2a_capability` | Audit = `inbound.jsonl`; token shown redacted-then-reveal |
| **Providers** | Catalog view, edit user overlay (pricing, required_env, wizard hints) | `providers/registry`, `~/.jac/providers.yaml` | Deep-merge overlay |
| **Context** | Edit project + user `AGENTS.md`; prompt overlays | `workspace` loaders | Markdown editors (project/user/package layers) |
| **Memory** | View entries; manual remember/forget | memory loaders + `remember/forget` | **Read-mostly** (JAC-managed); no free editing |
| **Sessions** | List, open, delete, prune-by-age | `Session.*` | From the left rail |

Read-only (never editable in the UI): `messages.json`, `usage.jsonl`, A2A
`inbound.jsonl`/contexts, tool-result cache.

## 9. Config editing pattern (the differentiator)

Generic agent UIs edit "the config." JAC's value is **layered precedence**, so
every config form is precedence- and scope-aware.

```
Config ▸ Compaction         Editing layer: ( Project ▾ )    [Advanced: raw YAML]
  strategy          [auto ▾]                 ← from package defaults
  max_context_tokens[256000] tokens          ← from project config.yaml
  auto_compact_pct  [70] %                   ← from package defaults
  refuse_pct        [85] %                   ← from package defaults
```

Each field shows:

1. **Effective value** — what JAC actually resolves right now.
2. **Source badge** — which layer it came from: `CLI · env · .env · project ·
   user · defaults`. (Resolved by replaying the loader precedence per field.)
3. **Editing-layer toggle** (form-level) — write to **project** (`.agents/
   config.yaml`) or **user** (`~/.jac/config.yaml`). The write touches only that
   layer's file; nothing else changes.
4. **Locked rows** — if a higher-precedence layer (env/CLI) wins, the control is
   disabled with an explainer ("set by env `JAC_MODEL`; YAML is ignored"). No
   silent "edited but nothing happened."
5. **Advanced: raw YAML** — the actual file for the chosen scope, validated on
   save (round-trips through the same schema the structured form uses).

Same pattern is the editor for `mcp.json`, `providers.yaml`, prompts, and
`AGENTS.md` (each with its scope toggle). This is the "structured forms + raw
escape hatch" decision made concrete.

## 10. Mid-session model/profile rebuild (the one new engine seam)

Switching model or profile from the top bar must rebuild Gru *in the live
session* — exactly what the REPL does for `/model` and `/profile` via
`RebuildGru`. The REPL snapshots env and rolls back if credential resolution
fails, so a bad switch leaves Gru untouched.

The web `WebChatManager` needs the **same snapshot/rollback guard**:

- Refuse a rebuild while a turn is in flight (`_busy`) — queue or reject.
- Snapshot the relevant `os.environ` before `apply_profile_env` /
  `apply_ad_hoc_model_env`; on failure, restore and emit an error frame.
- Rebuild the runtime's Gru (and dependent toolsets) in place, preserving the
  message history, bus, and pending-approval state.

This is the only genuinely new runtime-adjacent code; everything else is reuse.
It belongs in `web/chat.py` (a leaf surface), not in `runtime/`.

## 11. Tech architecture

**Stack:** Starlette + Jinja2 + **HTMX** (vendored, not CDN) + **SSE**
(`sse-starlette`) + POST. No SPA, no build step, no new dependencies (all already
in the lock file). This matches the charter (works offline, total markup control)
and the "simple/functional, not gimmick" directive.

**Reuse (the seam already exists):**

| Need | Reuse |
| --- | --- |
| Build the engine | `runtime/bootstrap.build_session_runtime` |
| Run a turn, stream events | `SessionDriver.run_turn` + `EventBus` |
| HITL approval/clarify | `ApprovalRequest` / `ClarifyRequest` futures resolved by POST |
| Profiles / keys / sessions CRUD | `profiles_crud`, `secrets`, `Session` |
| MCP / A2A / skills state + mutation | the live capabilities on `SessionRuntime` |
| Providers, pricing, required env | `providers/registry` |
| Token/cost | `UsageTracker` + `usage.jsonl` |

**New code (small, all in `web/`):**

- Drawer routes + HTMX fragment templates per management domain.
- Precedence resolver for config forms (replays loader layers per field —
  read-only introspection; no change to `config_loader`).
- Mid-session rebuild guard in `chat.py` (§10).
- HITL disconnect handling: resolve a pending approval future as **denied** when
  its SSE connection drops, so a closed tab can't hang the turn (a stated
  mitigation in the shipped design, made real here).

**Module layout** (extends the locked `web/` structure):

```
src/jac/web/
  app.py            # `jac web serve` Typer command + loopback guard (unchanged role)
  server.py         # create_app(): routes (console + drawers + chat), Jinja2 wiring
  chat.py           # WebChatManager: bus→SSE, HITL futures, rebuild guard (§10)
  panel/            # read-side view-models, one module per domain
    __init__.py     #   (profiles, keys, config, skills, mcp, a2a, providers, context, memory, sessions)
  actions/          # write-side handlers, one module per domain (call existing CRUD)
    __init__.py
  precedence.py     # per-field layer resolver for config forms (read-only)
  templates/        # base + console + per-domain drawer fragments
  static/           # css (light theme tokens) + htmx + console.js (SSE/HITL dispatch)
```

`panel.py`/`actions.py` graduate to packages because the management surface now
spans ~12 domains; keeping one file each would bloat. This stays within the
module-strategy rule (read-side vs write-side split; no CRUD reimplementation).

## 12. HITL & streaming (carry the correctness choices forward)

Two non-obvious choices from the shipped design are **kept** because each fixed a
real bug:

- **`stream=False`, not `run_stream`.** HITL approval is driven by `agent.run()`'s
  deferred-tool-call handling, which `run_stream` bypasses — streaming would let
  a gated tool run with no prompt. The reply lands on `RunCompleted`. (Token-level
  streaming *with* approval needs the `agent.iter()` graph path — still a deferred
  enhancement, not worth losing HITL correctness.)
- **Per-connection SSE queue (broadcast), closed on `pagehide`.** Prevents a
  dangling generator from stealing a live connection's frames, and prevents
  connection-pool saturation across session switches.

## 13. Doctor / readiness (fail-first, surfaced)

JAC's charter is "be loud about missing required config." The UI turns that into
a single readiness view (the top-bar dot + a Dashboard panel):

- **Model not set** (no profile/`--model` resolves).
- **Missing required keys** per configured profile (`ANTHROPIC_API_KEY`, …).
- **MCP server build failures** (missing env, unreachable, bad JSON).
- **A2A** running/stopped + unsafe-bind warnings.
- **Budget** at/over hard-stop.

Green when nothing blocks a run; amber for warnings; red when a run would fail.
This generalizes today's Keys-status idea into "what's blocking me right now."

## 14. Security (unchanged)

Loopback default; loud non-loopback warning; secrets never echoed to the browser
(status only); web owns its own active session (no shared-session race with a
CLI REPL). No accounts, no TLS, no remote access without an explicit non-loopback
opt-in. The loopback boundary is the model.

## 15. Slice plan

Sequenced for highest-value-first, each independently shippable. Mirrors how D48
was sliced (CRUD → chat → dashboard), but front-loads the management surface the
user asked for.

| Slice | Scope | Risk |
| --- | --- | --- |
| **R0 — Shell + light theme + full-bleed Console** | New base layout, light tokens, collapsible rails, top bar (scope/profile/model/doctor/tokens), chat wired to the existing engine + HITL. Re-skins and re-lays-out what already works. | Low |
| **R1 — Config drawers (the differentiator)** | Precedence-aware forms for compaction/budget/cost/secrets + raw-YAML escape hatch + scope toggle (`precedence.py`). | Med |
| **R2 — Profiles & Keys** | Profile form (+ YAML) and set-default; Keys status grid + set/unset + backend; top-bar model/profile switch with rebuild guard (§10). | Med |
| **R3 — MCP & A2A** | MCP server list + knob toggles + add/edit + errors + tools; A2A server control + token + peers CRUD + auth + inbound audit + card. | Med |
| **R4 — Skills, Context, Providers, Memory** | SKILL.md editor + reload; AGENTS.md + prompt editors; providers overlay; memory view + remember/forget. | Low |
| **R5 — Dashboard & doctor** | Dedicated observability page + readiness aggregation; HITL disconnect handling. | Low |

R0 is the foundation everyone sees; R1 is the thing nothing else can build.

## 16. Risks

- **Mid-session rebuild** (§10) is the only new runtime-adjacent code; get the
  env snapshot/rollback right or a bad switch corrupts the live agent. Cover with
  a test that asserts rollback on a missing-key switch.
- **Precedence resolver drift** — if it reimplements loader order and the loader
  changes, the source badges lie. Mitigation: drive it from the *same* source
  ordering `config_loader` exposes; don't fork the precedence logic.
- **Drawer vs full-page for heavy editors** — a raw-YAML editor may be cramped in
  a drawer. Mitigation: expand-to-full-pane while keeping the Console mounted.
- **HITL disconnect** — resolve pending futures as denied on SSE drop; otherwise
  a closed tab hangs the turn.

## 17. Non-goals

- Multi-user / multi-tenant / accounts (charter).
- Cross-project session browsing (deferred).
- Token-level streaming *with* HITL (needs `agent.iter()`; deferred).
- A reactive SPA framework (revisit only if the dashboard becomes a flagship).
- Dark theme / theming variants (light-only for v1).

## 18. Doc reconciliation (when this lands)

Per documentation discipline (one fact, one home):

- Fold the durable sections (charter, two-surface model, IA, tech, security) into
  [`web-surface.md`](web-surface.md); update its slice list and "shipped" status.
- Record the redesign as a new decision in [`architecture.md`](../architecture.md)
  §5 (next free `Dnn`).
- Mark slices in [`progress.md`](../progress.md) as they land.
- Any new CLI surface (none expected — `jac web serve` is unchanged) → `cli-reference.md` + `jac-cli/SKILL.md`.
- New modules (`web/panel/`, `web/actions/`, `web/precedence.py`) →
  [`codebase-map.md`](../developer/codebase-map.md).
- Then delete this proposal doc.
