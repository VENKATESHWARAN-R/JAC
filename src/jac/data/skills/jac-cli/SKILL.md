---
name: jac-cli
description: How to compose JAC CLI invocations and REPL slash commands — flag combinations, A2A peer auth shapes, budget kinds. Use when the user asks how to invoke a JAC command or form a slash.
tools_required:
  - read_file
---

# JAC CLI composition

Use this skill when the user asks **"how do I run X in JAC"** or **"what's the slash for Y"**. Answer the composition question directly; for the full command list or tool reference, `read_file docs/user-guide/cli-reference.md`.

## Mental model

There are **three command surfaces**:

1. **Typer commands** — `jac …` from the shell. Subcommands (`init`, `sessions`, `profiles`, `keys`, `a2a serve`, `web serve`) do not need a model.
2. **Root flags on `jac`** — control how the REPL boots: `--model`, `--profile`, `--resume`, `--session`.
3. **Slash commands** — typed inside the REPL. Handled locally, **never sent to the model**. Tab-complete the first word after `/`.

`--model` **bypasses** profile selection. `--profile NAME` uses the profile's active tier's first model. They're not meant to combine — pick one.

## Boot patterns

```bash
jac                                  # default profile, new session
jac --profile claude                 # one-shot profile pick
jac --resume                         # latest session in this repo
jac --session 2026-05-24T14-30-00    # specific session id
jac --model anthropic:claude-opus-4-6   # raw model — bypasses profile
jac --model openai:gpt-4o --resume   # raw model + resume latest
```

Provider id format is always `provider:model-name` (colon, no spaces). Valid providers: `anthropic`, `openai`, `google`, `openrouter`, `mistral`, plus anything in `~/.jac/providers.yaml`.

## Profiles & keys

```bash
jac init                             # wizard: backend + profile + keys
jac profiles                         # list, mark default
jac profiles use NAME                # set default_profile
jac profiles edit NAME               # opens $EDITOR; validate on save
jac profiles remove NAME             # stored keys kept
jac keys                             # status per profile's required keys
jac keys set ANTHROPIC_API_KEY       # interactive prompt (hidden input)
jac keys unset ANTHROPIC_API_KEY
```

Resolution order at runtime: **process env → configured backend → fail-first**. Process env always wins.

## Sessions & workspace

```bash
jac sessions                         # list (id + msg count + creation time)
jac sessions delete <id>             # delete one (--yes / -y skips confirm)
jac sessions prune --older-than 30d  # delete sessions older than a cutoff (w/d/h)
```

A folder is a **project** if it has `.git` or `.agents/` at/above the CWD. Outside any project, JAC runs *loose*: sessions + `usage.jsonl` go to the global workspace `~/.jac/` (not a stray `.agents/`). `jac init` in a non-project folder offers to create `.agents/` here. Project-scope memory (`scope="project"`) fails outside a project — use `scope="user"` for cross-project facts.

## In-REPL slash commands

```text
/help                                # full slash list
/sessions                            # list project sessions (id + msg count + creation time)
/sessions delete <id>                # delete one session (not the active one)
/sessions prune <dur> [yes]          # preview/delete sessions older than <dur> (e.g. 30d)
/resume [ID]                         # switch session (latest if no id)
/clear                               # new session; old kept on disk
/memory [user|project]               # show stored remember() entries; no arg = both scopes
/remember <scope> <category> <text>  # store memory yourself; scope=user|project
/forget <scope> <exact text>         # remove memory yourself; scope=user|project
/profile [NAME]                      # list or switch (rebuilds Gru, rolls back on missing keys)
/model [PROVIDER:ID]                 # numbered picker or explicit override
/tokens                              # detailed token counters
/budget [extend [KIND] N]            # see Budget composition below
/compact                             # summarize oldest history now (any strategy)
/context [N|reset]                   # show/set session context budget (e.g. 400k; ceiling 512k)
/mode [normal|plan|accept-edits]     # see Mode composition below
/skill list|use NAME|reload          # see Skill composition below
/mcp list|reload|enable NAME|disable NAME   # see MCP composition below
/spawns                              # list parked bidirectional sub-agents
/a2a …                               # see A2A composition below
/exit                                # quit (also: exit / quit / :q / Ctrl-D)
```

## Budget composition

`/budget extend` raises one limit **for this session only**:

```text
/budget extend 50000                         # default KIND = session_total
/budget extend session_input 30000
/budget extend session_total 100000
/budget extend project_total 1000000
```

Valid KIND values: `session_input`, `session_total`, `project_total`. Default is `session_total`.

## Mode composition

```text
/mode                     # show the active mode + the choices
/mode plan                # read-only: every state-changing tool call is blocked
/mode accept-edits        # file writes/edits auto-apply; shell + rest still prompt
/mode normal              # default HITL — everything risky prompts
```

Plan Mode auto-denies write/edit/delete/shell/spawn/remember so Gru plans instead
of acting (it uses the `plan`/`update_plan` checklist and presents the plan); switch
back to `normal` to execute. There is **no YOLO mode** — per the locked design it
ships only with sandboxing (v2). Switching mode rebuilds Gru in place.

## Context-budget composition

```text
/context                  # show the resolved budget + where it comes from
/context 400k             # session override (k/m suffixes ok); clamped to 512k
/context 256000           # raw token count also works
/context reset            # drop the session override
```

Resolution precedence: session override (`/context`) → per-model
`compaction.model_context_tokens` → `compaction.max_context_tokens` (default 256k).

## Skill composition

```text
/skill list                          # active + shadowed tables
/skill use NAME                      # inject the skill body as the next user turn
/skill reload                        # rescan project + user + package dirs
```

The body of a skill loads via the `load_skill(reason, name)` tool — that's the path Gru uses when it decides on its own. `/skill use NAME` is the operator-driven equivalent.

## MCP composition

```text
/mcp list                            # servers: transport, status, approval/defer, source + any errors
/mcp reload                          # rescan ~/.jac/mcp.json + <repo>/.agents/mcp.json, rebuild Gru
/mcp enable NAME                     # turn a server on  (persists + rebuilds)
/mcp disable NAME                    # turn a server off (persists + rebuilds)
```

Servers are declared in `~/.jac/mcp.json` (user) and `<repo>/.agents/mcp.json` (project, shadows user per name), in the standard `mcpServers` JSON shape (same as Claude Desktop / Cursor). Optional per-server `jac` knobs: `enabled`, `defer` (tool-search hiding, default on), `requires_approval` (HITL, default on). MCP tools are **deferred-loaded** — Gru searches for them on demand rather than seeing them upfront — and every call is HITL-gated unless the server sets `requires_approval: false`. Full reference: `read_file docs/user-guide/mcp.md`.

## A2A composition

Server lifecycle (in-REPL):

```text
/a2a serve                           # bind to profile default (127.0.0.1:8001), bearer auth
/a2a serve --port 9000 --host 127.0.0.1
/a2a serve --unsafe                  # NO AUTH — trusted networks only
/a2a stop
/a2a status                          # URL, auth, peer count, last 5 inbound
/a2a token                           # reprint current bearer
```

Headless equivalent:

```bash
jac a2a serve
jac a2a serve --profile claude --port 8001
jac a2a serve --unsafe
```

Peer management — three auth shapes:

```text
/a2a peers
/a2a peer add NAME URL --bearer                          # JAC↔JAC
/a2a peer add NAME URL --api-key HEADER_NAME             # custom header
/a2a peer add NAME URL --oauth2 TOKEN_URL CLIENT_ID [--scope SCOPE]
/a2a peer remove NAME
```

Secrets (bearer token, API key value, client secret) are always prompted with hidden input — **never** pass them on the command line.

## Web UI composition

A local-first, single-user browser control panel for profiles, keys, and sessions (chat is a later slice):

```bash
jac web serve                        # http://127.0.0.1:8770, opens browser
jac web serve --port 9000 --no-open
jac web serve --host 0.0.0.0         # WARNS — non-loopback exposes API keys; single-user, no auth
```

Loopback is the security boundary (no accounts). Which sessions it shows depends on the launch directory: inside a project → that project's sessions; loose folder → the global `~/.jac` pool. It is **not** a hosted/multi-tenant service.

## Common composition mistakes to avoid

- ❌ `jac --model anthropic/claude-opus-4-6` — use a **colon** between provider and model, not a slash.
- ❌ `jac --profile NAME --model PROVIDER:ID` — pick one; `--model` bypasses the profile.
- ❌ `/budget extend project 1000000` — the KIND is `project_total`, not `project`.
- ❌ `/a2a peer add NAME URL bearer TOKEN` — auth flags are `--bearer` / `--api-key` / `--oauth2`; the secret is prompted, not positional.
- ❌ `jac a2a serve --unsafe` on a non-loopback bind — only safe for trusted networks; bearer auth is the default for a reason.
- ❌ Treating `/clear` as deleting the prior session — it just starts a new one; the prior session stays on disk and is reachable via `/sessions` / `/resume ID`. To actually remove it use `/sessions delete <id>` or `jac sessions delete <id>`.
- ❌ `/sessions prune 30d` and expecting it to delete immediately — without a trailing `yes` it only previews; run `/sessions prune 30d yes` to delete (or `jac sessions prune --older-than 30d`).
- ❌ `jac sessions prune 30d` — the age is an option: `jac sessions prune --older-than 30d`.
- ❌ Expecting `/remember some fact` to work — scope and category are required: `/remember project convention some fact`.

## Fallback

If the user asks about a command, flag, or tool not covered here, `read_file docs/user-guide/cli-reference.md` for the full table — especially:

- The complete **Gru tool catalog** (filesystem, search, shell/processes, web, memory, plan, clarify, A2A outbound) with full signatures
- The **status bar** field meanings (`ctx:`, `bud:`, `spawns:`)
- Which tools require approval (`write_file`, `edit_file`, `run_shell`, `remember`, `forget`, `start_process`, `kill_process`)

For broader workflow context: `docs/user-guide/a2a-operator.md` (A2A topologies and file transfer), `docs/user-guide/configuration.md` (YAML config schemas), `docs/user-guide/cost-controls.md` (post-processor + budgets), `docs/user-guide/sessions-and-memory.md` (memory 2×2 matrix).
