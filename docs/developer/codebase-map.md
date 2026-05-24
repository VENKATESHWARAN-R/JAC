# Codebase map

> **Audience:** contributors navigating `src/jac/` as built in **v0.1.2**.

Package root: `src/jac/`. Console entry: `jac.cli.app:main` (`pyproject.toml`).

## Module tree (as-built)

```text
src/jac/
├── __init__.py              # __version__ = "0.1.2"
├── __main__.py              # python -m jac
├── config.py                # Settings, CompactionSettings, BudgetSettings
├── errors.py                # JacConfigError
├── profiles.py              # Profile, tiers, A2A peer config, YAML load/save
├── secrets.py               # keyring / dotenv / env-only backends
├── cli/
│   ├── app.py               # Typer root: jac, init, sessions; sub-apps profiles/keys/a2a
│   ├── repl.py              # Interactive loop, build_gru wiring, slash dispatch
│   ├── renderer.py          # Rich UI, approval/clarify panels, compaction notices
│   ├── statusbar.py         # prompt-toolkit bottom toolbar
│   ├── init.py              # jac init wizard
│   ├── profiles_cmd.py      # jac profiles *
│   ├── keys_cmd.py          # jac keys *
│   ├── a2a.py               # jac a2a serve (headless)
│   ├── session_view.py      # jac sessions / /sessions listing
│   ├── profile_view.py      # Shared profile table for CLI + /profile
│   ├── editor.py            # $EDITOR helper for profiles edit
│   └── slash/
│       ├── registry.py      # SLASH_COMMANDS, parse, dispatch
│       ├── context.py       # SlashContext
│       ├── result.py        # Handled, RebuildGru, SwitchSession, StartA2AServer, …
│       └── handlers/
│           ├── help.py      # /help
│           ├── exit.py      # /exit
│           ├── session.py   # /sessions, /resume, /clear
│           ├── profile.py   # /profile
│           ├── model.py     # /model
│           ├── budget.py    # /budget, /tokens
│           └── a2a.py       # /a2a *
├── runtime/
│   ├── gru.py               # build_gru, _default_tool_capabilities
│   ├── session.py           # Session persistence, plan.json
│   ├── session_ctx.py       # ContextVar session id for remember audit
│   ├── bus.py               # EventBus
│   ├── events.py            # Typed JacEvent union
│   └── usage.py             # UsageTracker, BudgetLimits, usage.jsonl
├── capabilities/
│   ├── hooks.py             # make_hooks → EventBus
│   ├── approval.py          # make_approval_handler
│   ├── filesystem.py        # read/write/edit/list
│   ├── search.py            # grep, glob
│   ├── shell.py             # run_shell
│   ├── memory.py            # remember, forget
│   ├── web.py               # web_search, fetch_url
│   ├── history.py           # make_history_capability (D20 compaction)
│   ├── plan.py              # plan, update_plan, get_plan
│   ├── process.py           # background processes
│   ├── clarify.py           # clarify
│   ├── observability.py     # Logfire setup
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
│   ├── paths.py             # All path constants (SSOT)
│   ├── config_loader.py     # YAML layering for Settings
│   ├── bootstrap.py         # ensure_user_workspace on first run
│   ├── context.py           # AGENTS.md + memory.md loaders
│   └── prompts.py           # Layered prompt files
├── tools/
│   ├── decorator.py         # @jac_tool
│   └── toolset.py           # jac_function_toolset enforcement
├── providers/
│   └── registry.py          # providers.yaml catalog, prefix → env vars
├── data/
│   ├── defaults.yaml        # Non-required tunables (secrets.backend, compaction)
│   └── providers.yaml       # Provider catalog for init + key inference
└── prompts/
    └── gru_system.md        # Core Gru instructions
```

## Slash commands (registered)

| Command | Handler | Purpose |
| --- | --- | --- |
| `/help` | `handlers/help.py` | List slash commands |
| `/exit` | `handlers/exit.py` | Leave REPL (same as `exit` / Ctrl-D) |
| `/sessions` | `handlers/session.py` | List project sessions |
| `/resume [ID]` | `handlers/session.py` | Switch session (latest if no ID) |
| `/clear` | `handlers/session.py` | New session in place |
| `/profile [NAME]` | `handlers/profile.py` | List or switch profile |
| `/model [PROVIDER:ID]` | `handlers/model.py` | Picker or ad-hoc model switch |
| `/budget [extend …]` | `handlers/budget.py` | Show limits or extend session budget |
| `/tokens` | `handlers/budget.py` | Detailed usage counters |
| `/a2a …` | `handlers/a2a.py` | A2A server + peers (see user guide) |

Registration: import handlers in `jac/cli/slash/__init__.py`. Completer: `command_names()` in `repl.py`.

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

**Guest Gru (inbound A2A only):** `read_file`, `list_dir`, `grep`, `glob`.

History compaction is not a tool — it runs inside `ProcessHistory` on each model turn.

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

| Area | Typical files |
| --- | --- |
| Tools / paths | `tests/test_tools.py`, `tests/test_paths.py` |
| Sessions / memory | `tests/test_session.py`, `tests/test_memory.py` |
| Profiles / secrets | `tests/test_profiles.py`, `tests/test_secrets.py` |
| A2A | `tests/test_a2a_*.py` |
| Slash / budget | `tests/test_slash_*.py`, `tests/test_usage.py` |

Run: `just check` or `uv run pytest tests/ -q`.

## Related docs

- [Capabilities & hooks](capabilities.md) — patterns for extending the stack
- [Contributing](contributing.md) — `just` workflow and conventions
- [Architecture](../architecture.md) — design intent and roadmap
