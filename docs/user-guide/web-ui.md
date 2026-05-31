# Web UI

> **Audience:** users who want a browser instead of (or alongside) the terminal.

`jac web serve` opens a **local-first web UI** for JAC: a streaming chat **Console**
plus a full **Control Panel** for profiles, keys, config, MCP, A2A, skills, and more.
It drives the **same engine, tools, and human-in-the-loop approvals as the CLI** — it is
a renderer over the shared session engine, [not a new runtime mode](../architecture.md#15-surfaces-the-shared-engine).

!!! warning "Local-first and single-user by design"
    The web UI binds `127.0.0.1` (loopback) and has **no accounts and no authentication** —
    the loopback boundary *is* the access control. Its settings panel reads and writes API
    keys in the clear over HTTP. A non-loopback `--host` is allowed but prints a loud warning;
    only use it on a network you fully trust. It is **never** a multi-tenant or hosted service.

## Start it

```bash
cd your-project
jac web serve
```

This boots the server in the foreground (Ctrl-C to stop) and opens the UI in your browser.
Unlike the REPL, `jac web serve` does **not** require a profile or keys up front — the panel
exists precisely to set those up, so it boots on a fresh workspace (same posture as `jac keys`
/ `jac profiles`).

### Flags

| Flag | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address. Non-loopback binds warn loudly (keys handled in the clear, no auth). |
| `--port` / `-p` | `8770` | Bind port. |
| `--open` / `--no-open` | `--open` | Open the UI in your browser on start. |

### Which sessions does it show?

The same scoping as the CLI: the **launch directory decides**. Inside a project (a `.git`
or `.agents/` marker at or above the directory) it shows that project's sessions; in a loose
folder it shows the global `~/.jac` session pool. Cross-project browsing is deliberately
deferred — a session references its project's files, so resuming it elsewhere is ambiguous.
The top bar always states the active scope. See [Sessions & memory](sessions-and-memory.md).

## The Console (chat)

The Console is home — a full-bleed chat that fills the screen. You live in it; management
slides in over it without ever dropping the conversation.

- **Streaming replies** over Server-Sent Events (SSE), rendered through a built-in markdown
  renderer. Tool calls appear as compact chips with their arguments as labelled rows.
- **HITL approvals in the browser.** Gated tool calls (`write_file`, `edit_file`, `run_shell`,
  `remember`, MCP tools, sub-agent spawns) pause for your approval — exactly as in the CLI,
  except you click instead of typing `y`/`n`. Approvals render **one at a time in a pinned bar**
  between the transcript and the input, with a `+N more waiting` queue so a parallel spawn's
  approvals can't be missed. (Internally the browser `POST` resolves the *same* approval
  `Future` the CLI resolves at a terminal prompt.)
- **One turn at a time** — single-user charter. If your tab disconnects mid-approval, a
  failsafe auto-denies the pending call after a grace period so a closed tab can't hang a turn.
- **Top-bar model/profile switcher** — change the active model or profile mid-session. This
  rebuilds Gru in place with an environment snapshot/rollback guard (a bad switch leaves the
  running agent untouched), mirroring the REPL's `/model` and `/profile`.

## The Control Panel

Every configuration domain opens as an **htmx drawer over the live chat** (the Console never
reloads). Each drawer reads through the same view-models and writes through the same CRUD the
CLI uses — nothing is re-implemented.

| Drawer | What you manage | CLI analogue |
| --- | --- | --- |
| **Dashboard** | Cost/usage overview + readiness ("doctor") | `/tokens` |
| **Profiles** | List, set-default, create/edit (structured form **and** raw YAML), tier model lists | `jac profiles`, `/profile` |
| **Keys** | Credential status grid, set/unset, backend selector. **Values are never displayed.** | `jac keys` |
| **Config** | `compaction` · `budget` · `cost` · `secrets` — scope- and precedence-aware forms | `config.yaml`, `/context`, `/budget` |
| **MCP** | Server list, enable/disable/defer/approval toggles, add/edit, build errors, discovered tools | `/mcp list\|reload\|enable\|disable` |
| **A2A** | Outbound peers (CRUD + auth), inbound audit log, agent-card preview | `/a2a peers`, `/a2a peer add\|remove` |
| **Skills** | Active + shadowed skills, view/edit `SKILL.md`, reload, **"Use in chat"** | `/skill list\|use\|reload` |
| **Context** | Project + user `AGENTS.md` and prompt overlays | — |
| **Memory** | View entries; manual remember/forget (read-mostly) | `/memory`, `/remember`, `/forget` |
| **Providers** | Catalog view + user overlay (pricing, required keys) | `providers.yaml` |

### Scope- and precedence-aware config

The config drawers are the differentiator. Because JAC resolves config through
[layered precedence](configuration.md), every field shows its **effective value**, a **source
badge** (which layer it came from — CLI · env · `.env` · project · user · defaults), and an
**editing-layer toggle** so you choose whether a write lands in the **project** (`.agents/config.yaml`)
or **user** (`~/.jac/config.yaml`) file. If a higher-precedence layer (e.g. an env var) wins,
the control is disabled with an explainer instead of silently writing a value that does nothing.
A raw-YAML escape hatch (validated on save) backs every form.

## Activity dashboard

A collapsible rail on the Console shows live session activity — the same data the CLI's
`/tokens` and `/spawns` read:

- **Token/cost meter** — session in/out/total, cache hit %, project total, budget %.
- **Minion cards** — one per active sub-agent: tier, model, round-trips, turns, status
  (`running` / `waiting`).
- **Files changed** — `write_file` / `edit_file` targets that actually exist on disk
  (a denied or phantom write never shows).
- **Environment** — the session's A2A peers, MCP servers, and loaded skills.

## How it relates to the CLI

The web UI and the CLI are two surfaces over **one engine**. They share the session bootstrap
(`build_session_runtime`), the turn pipeline (`SessionDriver`), the event stream (`EventBus`),
and every capability; runtime mutations (model/profile switch, MCP/skill toggles) go through
the single `SessionController` both surfaces call. The only thing that differs is the renderer.
For the full layering and boundary rules, see
[Architecture §1.5 — Surfaces & the shared engine](../architecture.md#15-surfaces-the-shared-engine);
for the design charter, see [`design/web-surface.md`](../design/web-surface.md).

!!! note "Don't drive one session from two surfaces at once"
    The web UI owns its own active session. Running the CLI REPL and the web UI against the
    *same* live session simultaneously would race on `messages.json` — open them on different
    sessions.

## Related

- [Getting started](getting-started.md)
- [CLI reference](cli-reference.md) — the `jac web serve` command and every slash command
- [Configuration](configuration.md) — the layered precedence the config drawers expose
- [A2A operator](a2a-operator.md) — exposing JAC to and calling other agents
