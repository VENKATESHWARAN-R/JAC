# CLI reference

> **Audience:** users looking up Typer commands, REPL slash commands, and Gru tools.

## Typer commands

| Command | Description |
| --- | --- |
| `jac` | Start interactive REPL (default profile unless flags set) |
| `jac init` | Interactive setup: secrets backend, profile, credentials |
| `jac sessions` | List sessions in current project (oldest → newest, marks latest) |
| `jac profiles` | List profiles (same as `jac profiles list`) |
| `jac profiles list` | List profiles, mark `default_profile` |
| `jac profiles use NAME` | Set default profile |
| `jac profiles edit NAME` | Edit profile YAML in `$EDITOR`, validate on save |
| `jac profiles remove NAME` | Remove profile from config (stored keys kept) |
| `jac keys` | Show credential status for configured profiles |
| `jac keys list` | Same as `jac keys` |
| `jac keys set KEY` | Prompt and store secret (not with `env-only` backend) |
| `jac keys unset KEY` | Remove secret from backend |
| `jac a2a serve` | Headless A2A guest server (foreground until Ctrl-C) |

### Root flags (`jac`)

| Flag | Short | Description |
| --- | --- | --- |
| `--model` | `-m` | Raw model id; bypasses profile (e.g. `anthropic:claude-opus-4-6`) |
| `--profile` | `-p` | Profile for this REPL session |
| `--resume` | `-r` | Resume latest session in this project |
| `--session` | `-s` | Resume specific session id |

Subcommands (`init`, `sessions`, `profiles`, `keys`, `a2a`) do not activate a profile or require a model.

### `jac a2a serve` flags

| Flag | Description |
| --- | --- |
| `--host` | Bind address (default: profile `a2a.host`, else `127.0.0.1`) |
| `--port` / `-p` | Port (default: profile `a2a.port`, else `8001`) |
| `--unsafe` | Disable bearer auth — any client on the port can call guest Gru |
| `--profile` | Profile for model/credentials (default: `default_profile`) |

## REPL exit

| Input | Action |
| --- | --- |
| `exit`, `quit`, `:q`, `:quit` | Quit |
| Ctrl-D | Quit |
| `/exit` | Quit |

## Slash commands

Slash lines are handled locally — they are **not** sent to the model.

| Command | Usage | Description |
| --- | --- | --- |
| `/help` | `/help` | List slash commands |
| `/exit` | `/exit` | Leave REPL |
| `/sessions` | `/sessions` | List project sessions |
| `/resume` | `/resume [ID]` | Switch session (latest if omitted) |
| `/clear` | `/clear` | New session (prior session kept on disk) |
| `/profile` | `/profile [NAME]` | List profiles or switch |
| `/model` | `/model [PROVIDER:ID]` | Numbered picker or explicit model |
| `/budget` | `/budget` | Show token budgets vs usage |
| `/budget extend` | `/budget extend [KIND] N` | Raise limit for this session (`KIND`: `session_input`, `session_total`, `project_total`; default `session_total`) |
| `/tokens` | `/tokens` | Detailed token counters; shows a separate `a2a guest` line when inbound calls have consumed model tokens (counts toward `project_total` only) |
| `/a2a` | see [A2A operator](a2a-operator.md) | Server + peers; `/a2a status` shows server state, peer count, and last 5 inbound calls from `inbound.jsonl` |

Tab completion: type `/` and start a command name (first word only).

## Approval flow

These tools require **y** / **n** at a panel showing **reason** and arguments:

- `write_file`, `edit_file`
- `run_shell`
- `remember`, `forget`
- `start_process`, `kill_process`

## Gru tools

Every tool requires `reason: str` as the first argument (shown in the approval panel).

### Filesystem

| Tool | Description |
| --- | --- |
| `read_file(reason, path, start_line?, end_line?)` | Read file or 1-indexed line range (max 1000 lines / 1 MB whole-file) |
| `write_file(reason, path, content)` | Write file (approval) |
| `edit_file(reason, path, patches)` | Patch file: list of `{old, new}` hunks (approval) |
| `list_dir(reason, path?, show_hidden?)` | List directory entries |

Paths: absolute, or relative to **git project root**.

### Search

| Tool | Description |
| --- | --- |
| `grep(reason, pattern, path?, glob?, case_sensitive?)` | Regex search (ripgrep if available) |
| `glob(reason, pattern)` | Find files (`**` supported) |

### Shell & processes

| Tool | Description |
| --- | --- |
| `run_shell(reason, command, timeout_s?)` | Run command synchronously (approval; default timeout 120s) |
| `start_process(reason, command, name?)` | Background process, ring-buffer output (approval) |
| `tail_process(reason, task_id, lines?)` | Read recent output |
| `kill_process(reason, task_id, signal?)` | Stop background process (approval) |
| `list_processes(reason)` | List running background tasks |

### Web

| Tool | Description |
| --- | --- |
| `web_search(reason, query, max_results?)` | Web search (Tavily if `TAVILY_API_KEY`, else DuckDuckGo) |
| `fetch_url(reason, url)` | Fetch URL body as text |

### Memory

| Tool | Description |
| --- | --- |
| `remember(reason, content, category, scope)` | Append durable fact (`scope`: `user` \| `project`) |
| `forget(reason, content, scope)` | Remove one exact-normalized match |

Categories: `convention`, `fact`, `preference`, `gotcha`, `decision`. See [Sessions & memory](sessions-and-memory.md).

### Plan

| Tool | Description |
| --- | --- |
| `plan(reason, steps)` | Replace checklist (max 25 steps) |
| `update_plan(reason, step, status)` | `status`: `pending` \| `in_progress` \| `completed` |
| `get_plan(reason)` | Return current plan text |

### Clarify

| Tool | Description |
| --- | --- |
| `clarify(reason, question, options)` | User picks one of 2–8 options (numbered prompt) |

### A2A (outbound)

| Tool | Description |
| --- | --- |
| `a2a_discover(reason, url)` | Fetch peer agent card |
| `a2a_call(reason, peer_or_url, message, context_id?, files?)` | Send message and **block until the peer's task reaches a terminal state** (polls `tasks/get` under the hood). `peer_or_url` is a profile peer name, session peer name, or `https://…` URL — a raw URL matching a configured peer's URL is auto-promoted so auth is applied. `files=[paths]` attaches each (5 MB cap, base64-encoded `FilePart`). Returned task's `_jac_saved_files` lists paths under `.agents/a2a/inbound-files/<task_id>/` for any inline file artifacts the peer sent back. |

Inbound guest server, file transfer behavior, demo peer: [A2A operator](a2a-operator.md).

## Status bar

Bottom line while typing (example):

```text
profile:claude  tier:medium (anthropic:claude-sonnet-4-5)  branch:main*  ctx:34%/200k  session:2026-05-24T14-30-00
```

- **ctx** — estimated history tokens vs `compaction.max_context_tokens` (color follows warn/auto/refuse thresholds)
- **bud** — appears when token budgets are configured

## Related

- [Getting started](getting-started.md)
- [Configuration](configuration.md)
- [Examples](examples.md)
