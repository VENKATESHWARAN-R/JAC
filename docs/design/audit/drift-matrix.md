# Doc / code drift matrix

> **Audience:** maintainers auditing alignment between documentation and `src/jac/`.
>
> **Last audited:** 2026-05-25 · **Release:** v0.2.0 · **Phase:** 1.7 complete, 4 partial (PR1–PR3)

Legend: **OK** aligned · **GAP** doc missing or stale · **PARTIAL** shipped but incomplete vs roadmap · **N/A** not claimed yet

## CLI & REPL

| Claim | Doc location | Code location | Status | Notes |
| --- | --- | --- | --- | --- |
| `jac` REPL with `--profile`, `--model`, `--resume`, `--session` | user-guide/cli-reference | `jac.cli.app` | OK | |
| `jac init` wizard | getting-started | `jac.cli.init` | OK | |
| `jac profiles` list/use/remove/edit | cli-reference, configuration | `jac.cli.profiles_cmd` | OK | |
| `jac keys` list/set/unset | cli-reference, configuration | `jac.cli.keys_cmd` | OK | |
| `jac sessions` | cli-reference | `jac.cli.session_view` | OK | |
| `jac a2a serve` headless | a2a-operator | `jac.cli.a2a` | OK | |
| Slash: `/help`, `/exit` | cli-reference | `slash/handlers/help.py`, `exit.py` | OK | |
| Slash: `/sessions`, `/resume`, `/clear` | cli-reference | `slash/handlers/session.py` | OK | |
| Slash: `/profile`, `/model` | cli-reference | `slash/handlers/profile.py`, `model.py` | OK | |
| Slash: `/budget`, `/tokens` | cli-reference, configuration | `slash/handlers/budget.py`, `tokens.py` | OK | |
| Slash: `/a2a` serve/stop/status/token/peers/peer | a2a-operator | `slash/handlers/a2a/` | OK | |
| Slash: `/compact` | progress (deferred) | — | N/A | Compaction is automatic (D20); no slash registered |
| Status bar (profile, tier, branch, ctx%, session, bud%) | progress 1.7.b | `jac.cli.statusbar` | OK | Not a separate user page |

## Tools (Gru)

| Tool | Doc | Code | HITL | Status |
| --- | --- | --- | --- | --- |
| `read_file` | cli-reference | `capabilities/filesystem.py` | No | OK |
| `write_file` | cli-reference | filesystem | Yes | OK |
| `edit_file` | cli-reference | filesystem | Yes | OK |
| `list_dir` | cli-reference | filesystem | No | OK |
| `grep`, `glob` | cli-reference | `capabilities/search.py` | No | OK |
| `run_shell` | cli-reference | `capabilities/shell.py` | Yes | OK |
| `web_search`, `fetch_url` | cli-reference | `capabilities/web.py` | No | OK (Tavily if `TAVILY_API_KEY`) |
| `remember`, `forget` | sessions-and-memory | `capabilities/memory.py` | Yes | OK |
| `plan`, `update_plan`, `get_plan` | cli-reference, examples | `capabilities/plan.py` | No | OK |
| `start_process`, `tail_process`, `kill_process`, `list_processes` | cli-reference | `capabilities/process.py` | start/kill Yes | OK |
| `clarify` | cli-reference | `capabilities/clarify.py` | No (is the prompt) | OK |
| `a2a_discover`, `a2a_call` | a2a-operator | `capabilities/a2a/client.py` | No | OK |
| `@jac_tool` + `reason:` | capabilities.md | `tools/decorator.py` | — | OK |

## Runtime modules (v0.2 paths)

| Topic | progress.md / CLAUDE | Code | Status |
| --- | --- | --- | --- |
| Hooks | `jac.runtime.hooks` | `runtime/hooks.py` | OK |
| EventBus | `jac.runtime.events` | `runtime/events.py` | OK |
| Approval handler | `jac.runtime.approval` | `runtime/approval.py` | OK |
| Session context CV | `jac.workspace.session_ctx` | `workspace/session_ctx.py` | OK |
| Prompt loader | `jac.workspace.paths.load_prompt` | `workspace/paths.py` | OK |
| Logfire | `observability` + `Instrumentation` on Gru | `runtime/observability.py`, `runtime/gru.py` | OK |
| Mid-session context refresh | v0.2 restructuring note | `capabilities/context.py` | OK |

## Configuration & workspace

| Topic | Doc | Code | Status |
| --- | --- | --- | --- |
| Layered config precedence | configuration, CLAUDE.md | `workspace/config_loader.py`, `config.py` | OK |
| `compaction.*` thresholds | configuration | `CompactionSettings` | OK |
| `budget.*` opt-in knobs | configuration | `BudgetSettings`, `runtime/usage.py` | OK |
| Profile tiers + `active_tier` | configuration | `profiles.py` | OK |
| Secrets backends keyring/dotenv/env-only | configuration | `secrets.py` | OK |
| Path layout `~/.jac`, `<repo>/.agents` | sessions-and-memory, getting-started | `workspace/paths.py` | OK |
| `usage.jsonl` per project | configuration | `paths.project_usage_file` | OK |

## A2A (Phase 4 — partial)

| Feature | Doc | Code | Status | Notes |
| --- | --- | --- | --- | --- |
| Inbound guest server + bearer auth | a2a-operator | `capabilities/a2a/server.py` | OK | |
| Guest Gru read-only toolset | a2a-operator | `capabilities/a2a/guest.py` | OK | write tools in capability but no approval handler → blocked |
| Outbound `a2a_call` / `a2a_discover` | a2a-operator | `capabilities/a2a/client.py` | OK | |
| Peer auth: bearer, api_key, oauth2_client_credentials | a2a-operator | `profiles.py`, `auth_strategies.py` | OK | D31 |
| Session peers `/a2a peer add\|remove` | a2a-operator | `A2ACapability.session_peers` | OK | |
| Context retention cleanup on serve | a2a-operator | `audit.cleanup_old_contexts` | PARTIAL | Startup only; hourly timer is PR4 |
| Guest token budget integration | progress PR4 | `server.py` `tokens_used = 0` | GAP | PR4 queued |
| PR5 OIDC / GCP ID tokens | progress.md | — | N/A | Phase 4.e |

## Quality (Phase 7)

| Feature | progress.md | Code | Status |
| --- | --- | --- | --- |
| Ruff + ty in `just check` | `[x]` | `pyproject.toml`, justfile | OK |
| User guide on Zensical | `[x]` | `docs/user-guide/*.md`, `zensical.toml` nav | OK |
| Phase 1.7 pytest coverage | `[x]` | `tests/test_history.py`, etc. | OK |
| Memory tool unit tests | `[ ]` | — | GAP | No `test_memory.py` |
| Phase 1 session/fs integration tests | `[ ]` | partial via other tests | GAP |

## Deferred (must not appear as shipped)

| Feature | Doc | Status |
| --- | --- | --- |
| Skills loader (D21) | progress only | N/A |
| Minion runtime (Phase 5) | progress only | N/A |
| Plan Mode / `ModeCapability` (D23) | architecture §11, v2 | N/A |
| YOLO / Monty sandbox | v2 | N/A |
| MCP servers | Phase 6 | N/A |
| `/cost` slash | — | N/A (explicitly not built per D25) |

## How to use this matrix

1. When you change CLI, tools, or paths, find the row and set **OK** or fix the doc in the same PR.
2. When `progress.md` marks a phase complete, audit related rows before tagging a release.
3. Add a row for any new user-visible surface; do not rely on prose-only mentions in README.
