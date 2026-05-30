# Codebase map

> **Audience:** contributors navigating `src/jac/` as built in **v0.8.0**.

Package root: `src/jac/`. Console entry: `jac.cli.app:main` (`pyproject.toml`).

For the rules behind this layout — what goes where, slash-vs-capability, when to split a file — read [`module-strategy.md`](module-strategy.md). This page is the *as-built* tree; that page is the *why*.

> This page mirrors facts whose source of truth is the code. The slash-command set and the package version are guarded against drift by `just drift` ([`scripts/check_drift.py`](../../scripts/check_drift.py)); keep this page in the same change when you add a module, slash command, or tool.

## Module tree (as-built)

```text
src/jac/
├── __init__.py              # __version__ = "0.8.0"
├── __main__.py              # python -m jac
├── config.py                # Settings, CompactionSettings, BudgetSettings, CostSettings
├── errors.py                # JacConfigError
├── profiles.py              # Profile, A2APeerConfig, A2AProfileConfig, auth models (schema only)
├── profiles_io.py           # YAML codec + pre-D22 migration helpers
├── profiles_crud.py         # list/get/add/remove + resolve_active_profile_name
├── secrets.py               # keyring / dotenv / env-only backends
├── sdk.py                   # jac.sdk — documented embedding facade (R5d)
├── cli/
│   ├── app.py               # Typer root: jac, init, sessions; sub-apps profiles/keys/a2a/web
│   ├── repl.py              # Interactive loop, build_gru wiring, slash dispatch
│   ├── renderer.py          # Rich UI, approval/clarify panels, compaction notices, mode markers
│   ├── statusbar.py         # prompt-toolkit bottom toolbar (inline branch debounce)
│   ├── terminal.py          # cooked_mode() — force canonical TTY around prompts (D46)
│   ├── init.py              # jac init wizard + _run_pending_migrations
│   ├── profiles_cmd.py      # jac profiles *
│   ├── keys_cmd.py          # jac keys *
│   ├── a2a.py               # jac a2a serve (headless)
│   ├── session_view.py      # jac sessions / /sessions listing
│   ├── sessions_cmd.py      # jac sessions sub-app (list / delete / prune)
│   ├── profile_view.py      # Shared profile table for CLI + /profile
│   ├── editor.py            # $EDITOR helper for profiles edit
│   ├── _a2a_banner.py       # Shared server-started banner (REPL + headless)
│   └── slash/
│       ├── registry.py      # SLASH_COMMANDS, register, parse, dispatch
│       ├── context.py       # SlashContext
│       ├── result.py        # Handled, RebuildGru, SwitchSession, StartA2AServer, CompactNow, …
│       └── handlers/        # ONE FILE PER COMMAND (see module-strategy.md)
│           ├── meta.py      # /help + /exit
│           ├── sessions.py  # /sessions
│           ├── resume.py    # /resume
│           ├── clear.py     # /clear
│           ├── profile.py   # /profile
│           ├── model.py     # /model
│           ├── budget.py    # /budget
│           ├── tokens.py    # /tokens
│           ├── compact.py   # /compact (D20)
│           ├── context.py   # /context (D20 budget override)
│           ├── mode.py      # /mode normal|plan|accept-edits (D23)
│           ├── memory.py    # /memory (read-only view)
│           ├── memory_edit.py  # /remember + /forget (D14 user-driven edits)
│           ├── skill.py     # /skill list|use|reload (D21)
│           ├── spawns.py    # /spawns — active bidirectional sub-agent channels (Phase E)
│           ├── mcp.py       # /mcp list|reload|enable|disable (Phase F / D46)
│           └── a2a/         # /a2a multi-subcommand subpackage
│               ├── __init__.py  # /a2a dispatcher
│               ├── _args.py     # parsers
│               ├── _shared.py   # auth label, desc tail, secret prompt
│               ├── serve.py     # /a2a serve
│               ├── stop.py      # /a2a stop
│               ├── status.py    # /a2a status
│               ├── token.py     # /a2a token
│               ├── peers.py     # /a2a peers
│               └── peer.py      # /a2a peer add|remove
├── web/                     # Local-first web UI surface (D48) — mirrors cli/
│   ├── app.py               # Typer `jac web serve` + uvicorn launch + loopback guard
│   ├── server.py            # create_app() — Starlette app, routes, Jinja2 wiring
│   ├── panel.py             # Read-side: view-model assembly from management APIs
│   ├── actions.py           # Write-side: form POST handlers → profiles_crud/secrets/Session
│   ├── templates/           # Jinja2: base + overview/profiles/keys/sessions
│   └── static/jac.css       # Panel styling (dark, minion-yellow)
├── runtime/
│   ├── gru.py               # build_gru, _default_tool_capabilities, sub_agent_capabilities
│   ├── driver.py            # SessionDriver — surface-agnostic turn pipeline + budget guards (R5)
│   ├── session.py           # Session persistence, plan.json, delete/prune
│   ├── events.py            # Typed JacEvent union + EventBus
│   ├── hooks.py             # make_hooks → EventBus (PAI Hooks wiring)
│   ├── approval.py          # make_approval_handler (+ mode auto-decision, D23)
│   ├── modes.py             # ModeCapability policy — Plan / Accept-Edits (D23)
│   ├── observability.py     # logfire.configure() — global pipeline
│   ├── usage.py             # UsageTracker, BudgetLimits, usage.jsonl
│   ├── sub_agent/           # sub-agent package (split from sub_agent.py, R7a)
│   │   ├── tiers.py         #   tier names + cascade resolution
│   │   ├── packet.py        #   SubAgentTaskPacket / SubAgentSpawnSpec / SubAgentResult
│   │   ├── state.py         #   SubAgentCapability + setters; agent-label contextvar; minion-N counter
│   │   ├── runner.py        #   worker-Agent build (allowed_tools filter, external ask_supervisor) + simple run
│   │   ├── suspend.py       #   suspend/resume transport: PendingSpawn registry + drive loop (Phase 4/R7b)
│   │   └── tools.py         #   spawn_sub_agent(s) + respond_to_sub_agent (Phase B/E)
│   ├── sub_agent_usage.py   # sub-agent cost attribution helpers (rolled into UsageTracker)
│   └── tool_summarize.py    # maybe_summarize_tool_result — cheap-tier summarization gate (Phase A)
├── capabilities/
│   ├── context.py           # ContextCapability — dynamic AGENTS.md/memory.md (get_instructions)
│   ├── filesystem.py        # read/write/edit/list (+ allowed= filter, R3)
│   ├── search.py            # grep, glob
│   ├── shell.py             # run_shell
│   ├── memory.py            # remember, forget, read_memory_entries
│   ├── web.py               # web_search, fetch_url
│   ├── history.py           # make_history_capability (D20 compaction, wraps ProcessHistory)
│   ├── plan.py              # plan, update_plan, get_plan
│   ├── process.py           # background processes (state on capability directly)
│   ├── clarify.py           # clarify
│   ├── skills.py            # SkillsCapability + load_skill (Phase D / D21)
│   ├── mcp.py               # MCPCapability — external mcpServers loader (Phase F / D46)
│   ├── sub_agent.py         # SubAgentToolCapability + RespondToSubAgentCapability (Phase B/E)
│   └── a2a/
│       ├── __init__.py      # A2ACapability, make_a2a_capability
│       ├── server.py        # A2AServer, AuditingAgentWorker, uvicorn lifecycle
│       ├── guest.py         # build_guest_gru (inbound; read-only toolset, R3)
│       ├── guest_files.py   # inbound file-part materialization (Phase 4.d.4)
│       ├── client.py        # a2a_discover, a2a_call (+ outbound SSRF guard, R1)
│       ├── _files.py        # shared filename sanitizer (client + guest_files, R15)
│       ├── card.py          # Agent card JSON
│       ├── auth.py          # Bearer middleware, token generation
│       ├── auth_strategies.py  # bearer / api_key / oauth2_client_credentials
│       ├── storage.py       # Per-context message history on disk
│       └── audit.py         # inbound.jsonl, context retention cleanup
├── workspace/
│   ├── paths.py             # All path constants (SSOT) + load_prompt
│   ├── config_loader.py     # YAML layering for Settings
│   ├── bootstrap.py         # ensure_user_workspace / init_project_workspace
│   ├── context.py           # AGENTS.md + memory.md loaders (+ load_agents_context for sub-agents)
│   └── session_ctx.py       # ContextVar session id (consumed by memory + history)
├── tools/
│   ├── decorator.py         # @jac_tool (+ summarizable overloads)
│   └── toolset.py           # jac_function_toolset, summarizing_wrap, restrict_toolset enforcement
├── providers/
│   └── registry.py          # providers.yaml catalog, prefix → env vars, pricing
├── data/
│   ├── defaults.yaml        # Non-required tunables (secrets.backend, compaction, cost)
│   ├── providers.yaml       # Provider catalog for init + key inference + tier pricing
│   └── skills/              # Shipped reference skills (Phase D)
│       ├── code-review/SKILL.md
│       ├── jac-cli/SKILL.md
│       ├── summarize-large-files/SKILL.md
│       └── verify-change/SKILL.md
└── prompts/
    ├── gru_system.md            # Core Gru instructions
    ├── sub_agent_system.md      # Sub-agent (minion) instructions
    ├── gru_bidirectional.md     # Addendum when sub_agent_bidirectional is on (Phase 4 suspend/resume)
    ├── gru_plan_mode.md         # Addendum in Plan mode (D23)
    ├── gru_accept_edits.md      # Addendum in Accept-Edits mode (D23)
    └── a2a_guest_addendum.md    # Guest-mode addendum (D24)
```

## Slash commands (registered)

Filename = command. Open `cli/slash/handlers/` to see the catalog. The set is
drift-guarded by `just drift`.

| Command | File | Purpose |
| --- | --- | --- |
| `/help` | `handlers/meta.py` | List slash commands |
| `/exit` | `handlers/meta.py` | Leave REPL (same as `exit` / Ctrl-D) |
| `/sessions [delete\|prune …]` | `handlers/sessions.py` | List / delete / prune project sessions |
| `/resume [ID]` | `handlers/resume.py` | Switch session (latest if no ID) |
| `/clear` | `handlers/clear.py` | New session in place |
| `/profile [NAME]` | `handlers/profile.py` | List or switch profile |
| `/model [PROVIDER:ID]` | `handlers/model.py` | Picker or ad-hoc model switch |
| `/budget [extend …]` | `handlers/budget.py` | Show limits or extend session budget |
| `/tokens` | `handlers/tokens.py` | Detailed usage counters |
| `/compact` | `handlers/compact.py` | Force a summarizing compaction now (D20) |
| `/context [N\|reset]` | `handlers/context.py` | Show / override the session context budget (D20) |
| `/mode [normal\|plan\|accept-edits]` | `handlers/mode.py` | Switch interaction mode (D23) |
| `/memory [user\|project]` | `handlers/memory.py` | Read-only view of stored memory (D14) |
| `/remember` | `handlers/memory_edit.py` | User-driven memory write (no model call, D14) |
| `/forget` | `handlers/memory_edit.py` | User-driven memory removal (D14) |
| `/skill list\|use\|reload` | `handlers/skill.py` | Community-format skill loader (Phase D / D21) |
| `/spawns` | `handlers/spawns.py` | List active bidirectional sub-agent channels (Phase E) |
| `/mcp list\|reload\|enable\|disable` | `handlers/mcp.py` | External MCP server control (Phase F / D46) |
| `/a2a serve\|stop\|status\|token\|peers\|peer` | `handlers/a2a/<sub>.py` | A2A server + peers (one file per subcommand) |

Registration: import handlers in `jac/cli/slash/handlers/__init__.py`. Completer: `command_names()` in `repl.py`.

## Gru tools (feature inventory)

| Tool | Capability | Approval |
| --- | --- | --- |
| `read_file` | filesystem | No |
| `write_file` | filesystem | Yes |
| `edit_file` | filesystem | Yes |
| `list_dir` | filesystem | No |
| `grep` | search | No |
| `glob` | search | No |
| `run_shell` | shell | Yes |
| `remember` | memory | Yes |
| `forget` | memory | Yes |
| `web_search` | web | No |
| `fetch_url` | web | No |
| `plan` | plan | No |
| `update_plan` | plan | No |
| `get_plan` | plan | No |
| `start_process` | process | Yes |
| `tail_process` | process | No |
| `kill_process` | process | Yes |
| `list_processes` | process | No |
| `clarify` | clarify | No (user picker) |
| `a2a_discover` | a2a | No (outbound SSRF-guarded, R1) |
| `a2a_call` | a2a | No (outbound SSRF-guarded, R1) |
| `load_skill` | skills | No |
| `spawn_sub_agent` | sub_agent | Yes (tier + task packet shown) |
| `spawn_sub_agents` | sub_agent | Yes (one panel per spawn) |
| `ask_supervisor` | sub_agent | No (sub-agent side; **external** tool — suspends the run; `sub_agent_bidirectional`, default on) |
| `respond_to_sub_agent` | sub_agent | No (main-agent side; resumes the worker; flag-gated) |
| *MCP tools* | mcp | Yes by default (per-server `requires_approval`); deferred-loaded + `ToolSearch` (D46) |

**Guest Gru (inbound A2A only):** `read_file`, `list_dir`, `grep`, `glob` — write/edit are filtered out of the toolset entirely (R3).

History compaction is not a tool — `make_history_capability` registers a `ProcessHistory` processor that applies **token-budget-aware** compaction (D20: `auto` / `sliding` / `manual` strategies against the resolved context budget). Interaction modes (Plan / Accept-Edits) are a `ModeCapability` policy consulted by the approval handler (D23), not a tool. System-prompt context (AGENTS.md + memory.md) is injected dynamically by `ContextCapability.get_instructions()`. Tracing comes from PAI's `Instrumentation` capability.

## Typer CLI commands

| Invocation | Module |
| --- | --- |
| `jac` (REPL) | `cli/app.py` → `repl.run_repl` |
| `jac init` | `cli/init.py` |
| `jac sessions list\|delete\|prune` | `cli/sessions_cmd.py` |
| `jac profiles` / `list` / `use` / `remove` / `edit` | `cli/profiles_cmd.py` |
| `jac keys` / `list` / `set` / `unset` | `cli/keys_cmd.py` |
| `jac a2a serve` | `cli/a2a.py` |
| `jac web serve` | `web/app.py` → `web/server.py:create_app` |

## On-disk artifacts (project)

Paths below show the in-project case (`<repo>/.agents/`). In **loose mode** (no `.git`/`.agents/`), the *state writers* — sessions, `usage.jsonl`, tool-result cache, A2A, MCP logs — anchor under `~/.jac/` instead via `paths.project_state_root()`; `memory.md` (project scope) and `AGENTS.md` have no loose-mode equivalent.

| Path | Writer |
| --- | --- |
| `<repo>/.agents/sessions/<id>/messages.json` | `Session.save` |
| `<repo>/.agents/sessions/<id>/plan.json` | `PlanCapability` |
| `<repo>/.agents/sessions/<id>/compacted/*.json` | `capabilities/history.py` |
| `<repo>/.agents/memory.md` | `remember` / `forget` |
| `<repo>/.agents/usage.jsonl` | `UsageTracker` |
| `<repo>/.agents/cache/tool-results/<id>/*.txt` | `tool_summarize` |
| `<repo>/.agents/cache/mcp/logs/<name>.log` | `MCPCapability` (stdio server stderr) |
| `<repo>/.agents/a2a/contexts/*.json` | A2A storage |
| `<repo>/.agents/a2a/inbound.jsonl` | A2A audit |
| `<repo>/AGENTS.md` | User only (loaded, never written by JAC) |

User workspace: `~/.jac/config.yaml`, `memory.md`, `AGENTS.md`, `mcp.json`, `history`, optional `providers.yaml` overlay.

## Tests (orientation)

Run `uv run pytest --collect-only -q` for the live count (**697** at v0.8.0). `just check` runs format + lint + `ty check src` + `just drift` + pytest.

| Area | Files |
| --- | --- |
| Compaction / history | `test_history.py` |
| Interaction modes (Plan / Accept-Edits) | `test_modes.py` |
| Status bar | `test_statusbar.py` |
| Usage / budgets | `test_usage.py`, `test_budget_slash.py` |
| Plan persistence | `test_plan_persistence.py` |
| HITL feedback / turn recovery | `test_hitl_feedback.py`, `test_turn_recovery.py` |
| Web backends | `test_web_backends.py` |
| Context capability | `test_context_capability.py` |
| Profiles / secrets / editor | `test_profiles.py`, `test_secrets.py`, `test_editor.py` |
| Provider registry | `test_provider_registry.py` |
| Prompt cache stability | `test_prompt_cache_stability.py` |
| Slash | `test_slash.py` |
| Memory / sessions | `test_memory.py`, `test_session.py` |
| Workspace paths / loose mode | `test_workspace_paths.py` |
| Skills (Phase D) | `test_skills.py` |
| MCP (Phase F) | `test_mcp.py` |
| Terminal hardening | `test_terminal.py` |
| Tool decorator / summarizer (Phase A) | `test_tool_decorator.py`, `test_tool_summarize.py` |
| Sub-agents (Phase B/E) | `test_sub_agent.py` |
| A2A (11 files) | `test_a2a_auth.py`, `test_a2a_card.py`, `test_a2a_audit.py`, `test_a2a_storage.py`, `test_a2a_guest.py`, `test_a2a_guest_files.py`, `test_a2a_slash.py`, `test_a2a_server.py`, `test_a2a_client.py`, `test_a2a_auth_strategies.py` |

## Related docs

- [Module strategy](module-strategy.md) — where things go and why (the rulebook).
- [Capabilities & hooks](capabilities.md) — patterns for extending the stack.
- [Contributing](contributing.md) — `just` workflow and conventions.
- [Architecture](../architecture.md) — design intent and roadmap.
