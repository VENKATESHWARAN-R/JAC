# Codebase map

> **Audience:** contributors navigating `src/jac/` as built in **v0.2.0**.

Package root: `src/jac/`. Console entry: `jac.cli.app:main` (`pyproject.toml`).

For the rules behind this layout — what goes where, slash-vs-capability, when to split a file — read [`module-strategy.md`](module-strategy.md). This page is the *as-built* tree; that page is the *why*.

## Module tree (as-built)

```text
src/jac/
├── __init__.py              # __version__ = "0.2.0"
├── __main__.py              # python -m jac
├── config.py                # Settings, CompactionSettings, BudgetSettings
├── errors.py                # JacConfigError
├── profiles.py              # Profile, A2APeerConfig, A2AProfileConfig, auth models (schema only)
├── profiles_io.py           # YAML codec + pre-D22 migration helpers
├── profiles_crud.py         # list/get/add/remove + resolve_active_profile_name
├── secrets.py               # keyring / dotenv / env-only backends
├── cli/
│   ├── app.py               # Typer root: jac, init, sessions; sub-apps profiles/keys/a2a
│   ├── repl.py              # Interactive loop, build_gru wiring, slash dispatch
│   ├── renderer.py          # Rich UI, approval/clarify panels, compaction notices
│   ├── statusbar.py         # prompt-toolkit bottom toolbar (inline branch debounce)
│   ├── init.py              # jac init wizard
│   ├── profiles_cmd.py      # jac profiles *
│   ├── keys_cmd.py          # jac keys *
│   ├── a2a.py               # jac a2a serve (headless)
│   ├── session_view.py      # jac sessions / /sessions listing
│   ├── profile_view.py      # Shared profile table for CLI + /profile
│   ├── editor.py            # $EDITOR helper for profiles edit
│   ├── _a2a_banner.py       # Shared server-started banner (REPL + headless)
│   └── slash/
│       ├── registry.py      # SLASH_COMMANDS, parse, dispatch
│       ├── context.py       # SlashContext
│       ├── result.py        # Handled, RebuildGru, SwitchSession, StartA2AServer, …
│       └── handlers/        # ONE FILE PER COMMAND (see module-strategy.md)
│           ├── meta.py      # /help + /exit
│           ├── sessions.py  # /sessions
│           ├── resume.py    # /resume
│           ├── clear.py     # /clear
│           ├── profile.py   # /profile
│           ├── model.py     # /model
│           ├── budget.py    # /budget
│           ├── tokens.py    # /tokens
│           ├── skill.py     # /skill list|use|reload
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
├── runtime/
│   ├── gru.py               # build_gru, _default_tool_capabilities (Instrumentation + ContextCapability + tools)
│   ├── session.py           # Session persistence, plan.json
│   ├── events.py            # Typed JacEvent union + EventBus (merged here in v0.2)
│   ├── hooks.py             # make_hooks → EventBus (PAI Hooks wiring)
│   ├── approval.py          # make_approval_handler (HandleDeferredToolCalls wiring)
│   ├── observability.py     # logfire.configure() — global pipeline
│   └── usage.py             # UsageTracker, BudgetLimits, usage.jsonl
├── capabilities/
│   ├── context.py           # ContextCapability — dynamic AGENTS.md/memory.md (get_instructions)
│   ├── filesystem.py        # read/write/edit/list
│   ├── search.py            # grep, glob
│   ├── shell.py             # run_shell
│   ├── memory.py            # remember, forget
│   ├── web.py               # web_search, fetch_url
│   ├── history.py           # make_history_capability (D20 compaction, wraps ProcessHistory)
│   ├── plan.py              # plan, update_plan, get_plan
│   ├── process.py           # background processes (state on capability directly)
│   ├── clarify.py           # clarify
│   ├── skills.py            # SkillsCapability + load_skill (Phase D / D21) — three-source layered loader
│   └── a2a/
│       ├── __init__.py      # A2ACapability, make_a2a_capability
│       ├── server.py        # A2AServer, uvicorn lifecycle
│       ├── guest.py         # build_guest_gru (inbound)
│       ├── client.py        # a2a_discover, a2a_call
│       ├── card.py          # Agent card JSON
│       ├── auth.py          # Bearer middleware, token generation
│       ├── auth_strategies.py  # bearer / api_key / oauth2_client_credentials
│       ├── storage.py       # Per-context message history on disk
│       └── audit.py         # inbound.jsonl, context retention cleanup
├── workspace/
│   ├── paths.py             # All path constants (SSOT) + load_prompt
│   ├── config_loader.py     # YAML layering for Settings
│   ├── bootstrap.py         # ensure_user_workspace on first run
│   ├── context.py           # AGENTS.md + memory.md loaders
│   └── session_ctx.py       # ContextVar session id (consumed by memory + history)
├── tools/
│   ├── decorator.py         # @jac_tool
│   └── toolset.py           # jac_function_toolset enforcement
├── providers/
│   └── registry.py          # providers.yaml catalog, prefix → env vars
├── data/
│   ├── defaults.yaml        # Non-required tunables (secrets.backend, compaction)
│   ├── providers.yaml       # Provider catalog for init + key inference
│   └── skills/              # Shipped reference skills (Phase D)
│       ├── code-review/SKILL.md
│       ├── summarize-large-files/SKILL.md
│       └── verify-change/SKILL.md
└── prompts/
    └── gru_system.md        # Core Gru instructions
```

## Slash commands (registered)

Filename = command. Open `cli/slash/handlers/` to see the catalog.

| Command | File | Purpose |
| --- | --- | --- |
| `/help` | `handlers/meta.py` | List slash commands |
| `/exit` | `handlers/meta.py` | Leave REPL (same as `exit` / Ctrl-D) |
| `/sessions` | `handlers/sessions.py` | List project sessions |
| `/resume [ID]` | `handlers/resume.py` | Switch session (latest if no ID) |
| `/clear` | `handlers/clear.py` | New session in place |
| `/profile [NAME]` | `handlers/profile.py` | List or switch profile |
| `/model [PROVIDER:ID]` | `handlers/model.py` | Picker or ad-hoc model switch |
| `/budget [extend …]` | `handlers/budget.py` | Show limits or extend session budget |
| `/tokens` | `handlers/tokens.py` | Detailed usage counters |
| `/a2a serve|stop|status|token|peers|peer` | `handlers/a2a/<sub>.py` | A2A server + peers (one file per subcommand) |
| `/skill list|use|reload` | `handlers/skill.py` | Community-format skill loader (Phase D / D21) |

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
| `a2a_discover` | a2a | No |
| `a2a_call` | a2a | No |
| `load_skill` | skills | No |

**Guest Gru (inbound A2A only):** `read_file`, `list_dir`, `grep`, `glob`.

History compaction is not a tool — `make_history_capability` registers a `ProcessHistory` processor that applies **token-budget-aware** compaction (D20: warn / auto-compact / refuse ladder against `compaction.max_context_tokens`). System-prompt context (AGENTS.md + memory.md) is injected dynamically by `ContextCapability.get_instructions()` so mid-session `remember` writes are visible immediately. Tracing comes from PAI's `Instrumentation` capability (per-agent, in every default capability set).

## Typer CLI commands

| Invocation | Module |
| --- | --- |
| `jac` (REPL) | `cli/app.py` → `repl.run_repl` |
| `jac init` | `cli/init.py` |
| `jac sessions` | `cli/session_view.py` |
| `jac profiles` / `list` / `use` / `remove` / `edit` | `cli/profiles_cmd.py` |
| `jac keys` / `list` / `set` / `unset` | `cli/keys_cmd.py` |
| `jac a2a serve` | `cli/a2a.py` |

## On-disk artifacts (project)

| Path | Writer |
| --- | --- |
| `<repo>/.agents/sessions/<id>/messages.json` | `Session.save` |
| `<repo>/.agents/sessions/<id>/plan.json` | `PlanCapability` |
| `<repo>/.agents/sessions/<id>/compacted/*.json` | `capabilities/history.py` |
| `<repo>/.agents/memory.md` | `remember` / `forget` |
| `<repo>/.agents/usage.jsonl` | `UsageTracker` |
| `<repo>/.agents/a2a/contexts/*.json` | A2A storage |
| `<repo>/.agents/a2a/inbound.jsonl` | A2A audit |
| `<repo>/AGENTS.md` | User only (loaded, never written by JAC) |

User workspace: `~/.jac/config.yaml`, `memory.md`, `AGENTS.md`, `history`, optional `providers.yaml` overlay.

## Tests (orientation)

**296 tests** at last count (`uv run pytest --collect-only -q`). No dedicated `test_memory.py` or `test_session.py` yet — see Phase 7 in `progress.md`.

| Area | Files |
| --- | --- |
| Compaction / history | `test_history.py` |
| Status bar | `test_statusbar.py` |
| Usage / budgets | `test_usage.py`, `test_budget_slash.py` |
| Plan persistence | `test_plan_persistence.py` |
| HITL feedback | `test_hitl_feedback.py` |
| Web backends | `test_web_backends.py` |
| Context capability | `test_context_capability.py` |
| Profiles / secrets / editor | `test_profiles.py`, `test_secrets.py`, `test_editor.py` |
| Provider registry | `test_provider_registry.py` |
| Slash | `test_slash.py` |
| A2A (9 files) | `test_a2a_auth.py`, `test_a2a_card.py`, `test_a2a_audit.py`, `test_a2a_storage.py`, `test_a2a_guest.py`, `test_a2a_slash.py`, `test_a2a_server.py`, `test_a2a_client.py`, `test_a2a_auth_strategies.py` |

Run: `just check` (ruff format + lint + `ty check src` + pytest) or `uv run pytest tests/ -q`.

## Related docs

- [Module strategy](module-strategy.md) — where things go and why (the rulebook).
- [Capabilities & hooks](capabilities.md) — patterns for extending the stack.
- [Contributing](contributing.md) — `just` workflow and conventions.
- [Architecture](../architecture.md) — design intent and roadmap.
