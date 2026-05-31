# Getting started

> **Audience:** anyone installing and running JAC for the first time.

JAC (**J**ust **A**nother **C**ompanion/CLI) is a Python CLI agent built on [Pydantic AI](https://ai.pydantic.dev/). It runs on your machine with your API keys, your files, and persistent sessions per git project.

**Current release:** v0.9.0. Adds the **local-first web UI** (`jac web serve`) — a browser chat plus a full control panel over the *same* engine, tools, and approvals as the CLI — and the **SDK control plane** (`SessionController`) that lets every surface share one set of runtime mutations instead of each re-implementing them. See the [Web UI guide](web-ui.md). Earlier releases: v0.8.0 (end-stage review hardening — sub-agent `allowed_tools` sandboxing, suspend/resume sub-agent comms, the `SessionDriver` + `jac.sdk` facade), v0.7.0 (interaction modes, compaction control, the MCP loader / Phase F), v0.6.0 (workspace loose-mode, session management, memory slash commands), v0.5.0 (parallel + bidirectional sub-agents), v0.4.0 (skill loader), v0.3.0 (context-cost controls + sub-agents) — see [Implementation progress](../progress.md) for the full history.

## Requirements

- **Python 3.13+**
- **macOS or Linux** (Windows untested)
- [`uv`](https://docs.astral.sh/uv/) recommended for install and runs

## Install

### Persistent install (recommended)

```bash
uv tool install "git+https://github.com/VENKATESHWARAN-R/JAC.git"
jac --help
```

Upgrade later:

```bash
uv tool upgrade jac
```

### One-off (no install)

```bash
uv run --from "git+https://github.com/VENKATESHWARAN-R/JAC.git" jac --help
```

### From a clone (development)

```bash
git clone https://github.com/VENKATESHWARAN-R/JAC.git
cd JAC
uv sync
uv run jac --help
```

## First-time setup: `jac init`

Run the wizard once (or again to add profiles):

```bash
jac init
```

It configures:

1. **Secrets backend** (stored in `~/.jac/config.yaml` under `secrets.backend`):
   - `keyring` — OS keychain (default)
   - `dotenv` — `~/.jac/.env` (`chmod 600`)
   - `env-only` — JAC stores nothing; reads your shell environment only

2. **A named profile** — provider, model id (`provider:model-name`), tier layout, optional default.

3. **Credentials** — required API keys for that provider. If a key is already in the environment, the wizard asks before importing into your chosen backend.

Profile names must match `[a-z0-9-]+` (e.g. `claude`, `ollama-local`).

!!! tip "No silent defaults"
    JAC does not pick a model or provider for you. If nothing is configured, `jac` exits with a `JacConfigError` pointing at `jac init`, `JAC_MODEL`, or `--model`.

## First session

From a git repository (sessions are per project):

```bash
cd your-project
jac
```

On first launch JAC creates `~/.jac/` skeleton files and suggests running `jac init` if you have not yet.

You will see:

- Model id and session id in the greeting
- A bottom **status bar**: profile, tier, git branch, context %, session id
- Prompt `»` for input

Leave with `exit`, `quit`, `:q`, or Ctrl-D.

### Useful flags

```bash
jac --profile NAME          # one-shot profile (see jac profiles)
jac --model PROVIDER:ID     # bypass profile; still resolves keys best-effort
jac --resume                # latest session in this repo
jac --session 2026-05-24T14-30-00   # specific session id
```

List sessions:

```bash
jac sessions
```

Inside the REPL: `/sessions`, `/resume [ID]`, `/clear` — see [CLI reference](cli-reference.md).

### Prefer a browser?

```bash
jac web serve
```

Opens a local-first web UI — a streaming chat plus a control panel for profiles, keys, config,
MCP, A2A, and skills — over the same engine, tools, and approvals as the CLI. Loopback-bound and
single-user. See the [Web UI guide](web-ui.md).

## What Gru can do in v0.9.0

- Read and search the repo (`read_file`, `grep`, `glob`, `list_dir`)
- Edit files and run shell commands **with your approval**
- Search the web (`web_search`, `fetch_url`) — Tavily if `TAVILY_API_KEY` is set, else DuckDuckGo
- Remember durable facts (`remember` / `forget`) into JAC-managed memory files
- Run long commands in the background (`start_process`, …)
- Ask structured multiple-choice questions (`clarify`)
- Maintain a visible checklist (`plan`, `update_plan`)
- Delegate context-heavy work to isolated **sub-agents** (`spawn_sub_agent`, `spawn_sub_agents`) — see [Cost controls](cost-controls.md)
- Load on-demand **skill playbooks** (`load_skill`) — see [Skills](skills.md)
- Call remote A2A agents when configured (`a2a_call`) — see [A2A operator](a2a-operator.md)

Project context from `<repo>/AGENTS.md` and `~/.jac/AGENTS.md` loads automatically. See [Sessions & memory](sessions-and-memory.md).

## Observability

JAC instruments model and tool calls with [Logfire](https://logfire.pydantic.dev/). Set `LOGFIRE_TOKEN` to ship traces to the cloud; without it, tracing stays local.

## Next steps

| Topic | Page |
| --- | --- |
| All commands and tools | [CLI reference](cli-reference.md) |
| Use the browser UI | [Web UI](web-ui.md) |
| Profiles, budgets, compaction | [Configuration](configuration.md) |
| Tool-result summarization & cost levers | [Cost controls](cost-controls.md) |
| Sessions and memory files | [Sessions & memory](sessions-and-memory.md) |
| Worked scenarios | [Examples](examples.md) |
| Expose JAC to other agents | [A2A operator](a2a-operator.md) |
