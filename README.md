# JAC

> **Just Another Companion/CLI — built on Pydantic AI.**

JAC is a Python CLI that wraps an LLM with persistent memory, tools,
human-in-the-loop gates, multi-provider credentials, and session continuity.
It runs on your machine — your keys, your files, your context.

> **Status:** **v0.1.0 alpha** (pre-release). Phase 2a.1 of the roadmap is
> shipped — chat, file/search/shell tools, HITL approval, session persistence
> + resume, multi-provider profile management, and a two-scope memory system
> (`remember` / `forget` writing to `~/.jac/memory.md` or
> `<repo>/.agents/memory.md` under HITL approval). The summarizer minion and
> the wider minion factory are next. See [`docs/progress.md`](docs/progress.md)
> for the current state.

## What it does today

- Interactive REPL with rich rendering and a status spinner that knows what
  Gru (the coworker) is doing — thinking, calling a tool, waiting for approval.
- **File tools**: `read_file`, `write_file`, `edit_file`, `list_dir`. Paths are
  anchored to the project's git root unless absolute.
- **Search tools**: `grep` (regex), `glob` (with `**` support).
- **Shell tool**: `run_shell` — always gated on human approval.
- Mutating file tools (`write_file`, `edit_file`) are also approval-gated.
- Every tool call must include a `reason: str` that the user sees in the
  approval prompt — soft alignment + audit trail in one move.
- **Session persistence** per project at `<repo>/.agents/sessions/<timestamp>/`.
  Resume the latest with `jac --resume`.
- **Multi-provider profiles**: configure Anthropic, OpenAI, Google, Ollama,
  OpenRouter, Mistral, or the Pydantic AI Gateway. Switch with `--profile`.
- **Three secrets backends**: OS keychain (default), `~/.jac/.env` (`chmod 600`),
  or env-only (read-through, JAC stores nothing).
- **AGENTS.md auto-loading** from the repo root and `~/.jac/AGENTS.md` —
  follows the community context-file convention.
- **Two-scope memory**: Gru saves durable facts via the approval-gated
  `remember` and `forget` tools. Project-scoped facts land in
  `<repo>/.agents/memory.md`; cross-project facts (preferences, language-
  level habits) land in `~/.jac/memory.md`. Both files auto-load into
  every future session.
- **Logfire tracing** out of the box. Every model call, tool call, and
  lifecycle event is instrumented. Ships to Logfire cloud if `LOGFIRE_TOKEN`
  is set, stays local otherwise.

## Requirements

- **Python 3.13+**
- **macOS or Linux** (Windows untested; OS keyring backend availability varies)
- [`uv`](https://docs.astral.sh/uv/) — the recommended installer

## Installation

### As a tool (persistent install)

Installs `jac` on your PATH:

```bash
uv tool install "git+https://github.com/VENKATESHWARAN-R/JAC.git"
jac --help
```

To upgrade later:

```bash
uv tool upgrade jac
```

### One-off, no install

```bash
uv run --from "git+https://github.com/VENKATESHWARAN-R/JAC.git" jac --help
```

### From a local clone (development)

```bash
git clone https://github.com/VENKATESHWARAN-R/JAC.git
cd jac
uv sync
uv run jac --help
```

## First-time setup

```bash
jac init
```

The wizard walks through three things:

**1. Where to store credentials (asked once).**

| Backend | What it is | When to pick it |
| --- | --- | --- |
| `keyring` *(default)* | OS-native keychain (macOS Keychain, Linux libsecret, Windows Credential Manager) via the [`keyring`](https://pypi.org/project/keyring/) library | Almost always. OS-level encryption, no master-password ceremony. |
| `dotenv` | `~/.jac/.env`, plaintext, `chmod 600` | Headless Linux without libsecret; or you prefer file-based config. |
| `env-only` | JAC stores nothing; reads process env only | You already manage credentials with direnv / 1Password CLI / a secrets manager. |

**2. Provider + model + profile name.**

Profile names must be lowercase letters, digits, and hyphens (e.g. `claude`,
`ollama-local`, `gemini-fast`). You can have as many as you want — re-run
`jac init` to add another.

**3. Credentials for the chosen provider.**

If a required key (like `ANTHROPIC_API_KEY`) is already in your environment,
the wizard offers to import it into your chosen backend — with an **explicit
prompt**, never silent. You might be on a machine with your employer's key in
env and want to keep your personal key separate. Pasted input is hidden.

## Usage

### Start a session

```bash
jac                                # uses default profile
jac --profile ollama-local         # one-shot profile selection
jac --model anthropic:claude-opus-4-6   # raw model override (still resolves keys)
```

### Resume work

```bash
jac --resume                       # resume the latest session for this project
jac --session 2026-05-20T15-30-00  # resume a specific session by id
jac sessions                       # list available session ids
```

Sessions live under `<repo>/.agents/sessions/<timestamp>/messages.json` —
folder-per-session, timestamps sort chronologically.

### Manage profiles

```bash
jac profiles                       # list all profiles, mark default
jac profiles use claude            # set default
jac profiles remove ollama-local   # delete profile (stored keys are kept)
```

### Manage stored credentials

```bash
jac keys                           # status of required keys (env / keyring / dotenv / missing)
jac keys set ANTHROPIC_API_KEY     # prompt and store (input hidden)
jac keys unset ANTHROPIC_API_KEY   # delete from backend
```

### In-session

- Type `exit`, `quit`, `:q`, or hit Ctrl-D to leave.
- Mutating tools (`write_file`, `edit_file`, `run_shell`, `remember`) trigger
  an approval panel showing the tool name, the reason Gru gave, and the args.
  Approve with `y`, deny with `n`.
- The status spinner reports per event — when Gru is thinking, when it's
  calling a tool, etc.

### Project + user memory

Gru saves durable facts via two HITL-gated tools — `remember(reason, content,
category, scope)` and the symmetric `forget(reason, content, scope)`. The
files JAC writes to are separate from `AGENTS.md` (which JAC never touches)
and auto-load into every future session at the matching scope.

**Two scopes:**

| Scope     | File                            | What goes here                                   |
| --------- | ------------------------------- | ------------------------------------------------ |
| `user`    | `~/.jac/memory.md`              | Cross-project facts — preferences, habits, language-level conventions. Follows you everywhere. |
| `project` | `<repo>/.agents/memory.md`      | Repo-specific facts — local conventions, structure, decisions, gotchas. Fails fast outside a git repo. |

**Five categories** (same at both scopes): `convention`, `fact`, `preference`,
`gotcha`, `decision`. Each entry lands in the matching `##` section.

**Discipline:**

- Every write is approval-gated — you see the proposed line, scope, and
  reason before it lands. Deny `n` and the entry is dropped.
- Each entry gets a timestamp **and** the originating session id in an HTML
  comment, so "where did this fact come from?" is always answerable.
- Exact-normalized duplicates are rejected loudly — Gru gets told, so it can
  choose a more specific phrasing.
- Past ~25 entries in any one section, `remember` tacks a "consider pruning"
  hint onto its return — loud, no automation.
- `forget` removes by exact-normalized match. If multiple lines match,
  it asks for a more specific phrasing rather than guessing.

Want to prune or rewrite an entry by hand? Just edit the relevant
`memory.md` directly — JAC preserves manual edits. Delete the file entirely
and JAC will bootstrap a fresh template on the next `remember` call.

## How JAC is organized on disk

```text
~/.jac/                            # user workspace (cross-project)
├── config.yaml                    # profiles + secrets backend choice
├── AGENTS.md                      # user-level context (user-authored), auto-loaded
├── memory.md                      # user-level JAC-managed memory, written via `remember`
├── .env                           # if `dotenv` backend (chmod 600)
├── prompts/                       # overrides for shipped prompts
└── history                        # prompt-toolkit input history

<repo>/AGENTS.md                   # project context (community convention, optional)
<repo>/.agents/                    # JAC project workspace
├── config.yaml                    # per-project profile/backend overrides (optional)
├── memory.md                      # JAC-managed project memory, written via `remember`
└── sessions/<timestamp>/
    └── messages.json
```

Config precedence (highest → lowest): **CLI args → env vars → project YAML →
user YAML → package defaults**. Required values without an override are
fail-first with an actionable error — JAC never silently picks a model or
provider for you.

## Environment variables

All optional once you've run `jac init`. See [`.env.template`](.env.template).

| Variable | Purpose |
| --- | --- |
| `JAC_MODEL` | Override the active model (`provider:model-id`). |
| `JAC_SECRETS__BACKEND` | Override the secrets backend (`keyring` / `dotenv` / `env-only`). |
| `LOGFIRE_TOKEN` | Ship traces to Logfire cloud. Absent = local-only. |
| `ANTHROPIC_API_KEY` | Anthropic provider key. |
| `OPENAI_API_KEY` | OpenAI provider key. |
| `GEMINI_API_KEY` | Google (`google-gla`) provider key. |
| `MISTRAL_API_KEY` | Mistral provider key. |
| `OPENROUTER_API_KEY` | OpenRouter provider key. |
| `PYDANTIC_AI_GATEWAY_API_KEY` | Pydantic AI Gateway key. |
| `OLLAMA_BASE_URL` | Ollama base URL (set per-profile via `jac init`, but env wins). |

Process env always wins over stored values, so direnv / 1Password CLI / CI
overrides keep working unchanged.

## Design

The architecture is documented in detail:

- **[`docs/idea.md`](docs/idea.md)** — why JAC exists, what it is and what it isn't.
- **[`docs/architecture.md`](docs/architecture.md)** — system design with
  diagrams, locked decisions, the phased roadmap.
- **[`CLAUDE.md`](CLAUDE.md)** — guidance for Claude Code working in this
  repo, and a good developer reference for contributors.
- **[`docs/progress.md`](docs/progress.md)** — current implementation state,
  phase by phase.

The same documents are also published as a static site (Zensical) — see
[Documentation site](#documentation-site) below.

### Core ideas

- **Capabilities are the atom.** Almost every cross-cutting concern (tools,
  memory, telemetry, approval handling, message-history window) is a Pydantic
  AI `Capability`.
- **Hooks are the event bus.** Pydantic AI lifecycle hooks emit typed events
  onto an `asyncio.Queue`; the CLI is a pure renderer reading from it.
  Approval flow is bidirectional via `asyncio.Future`s embedded in
  `ApprovalRequest` events.
- **Fail-first, no hardcoding.** Required values that aren't configured raise
  actionable errors instead of silently defaulting. Every path / model /
  prompt is overridable through the layered config.
- **One visible coworker (Gru), disposable workers (minions).** Multi-agent
  without permanent specialization — the minion factory lands in Phase 3.

## What's coming

- **Phase 2b** — Summarizer minion. Proposes additional memory deltas at
  session close, routed through the same `remember` HITL approval path so
  it can never trample the file directly. Built on Phase 3 infra.
- **Phase 3** — Minion factory. Researcher, builder, reviewer, tester
  templates as `Agent.from_spec()` YAML files.
- **Phase 4** — Quality. CodeMode (single sandboxed `run_code` tool),
  stuck-loop detection, tests, ruff + mypy.
- **v2** — A2A cross-repo coworking via `fasta2a`, Night Shift cron runs,
  YOLO sandbox (Monty + `sandbox-exec` / `bwrap`), user-tier predict-calibrate
  memory.

Full roadmap in [`docs/architecture.md`](docs/architecture.md) §9 and
[`docs/progress.md`](docs/progress.md).

## Development

Day-to-day commands are wrapped in a [`justfile`](justfile). Install
[`just`](https://just.systems) (e.g. `brew install just`) and run:

```bash
just                 # list recipes
just sync            # uv sync (mirror of `uv sync`)
just run --help      # uv run --env-file .env jac --help — pass args after `run`
just check           # ruff format + ruff lint + ty typecheck (no fixes)
just fix             # ruff format + ruff lint --fix (no typecheck)
just lint            # ruff check
just lint-fix        # ruff check --fix
just fmt             # ruff format --check
just fmt-fix         # ruff format
just typecheck       # ty check src/
just docs-serve      # zensical serve (live-reload)
just docs-build      # zensical build (writes ./site)
```

All recipes go through `uv run`, so they work without an activated venv.

## Documentation site

The Markdown under [`docs/`](docs/) is published with
[Zensical](https://zensical.org/) (a modern static site generator by the
Material for MkDocs team). Configuration lives in [`zensical.toml`](zensical.toml).

```bash
just docs-serve    # http://127.0.0.1:8000 with live reload
just docs-build    # static output under ./site
```

## Built on / inspired by

- **[Pydantic AI](https://github.com/pydantic/pydantic-ai)** — the agent
  framework JAC sits on top of.
- **[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)** —
  official capability library (CodeMode, WebSearch, etc.).
- **[pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents)** —
  closest analog; stuck-loop detection and orphan-repair patterns.
- **[memv](https://github.com/vstorm-co/memv)** — predict-calibrate memory
  (target for v2 user-tier memory).
- **[Monty](https://github.com/pydantic/monty)** — Pydantic's Rust-based
  Python sandbox (target for v2 YOLO mode).
- **Anthropic's harness writeups** —
  [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents),
  [Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps).

## License

[MIT](LICENSE) © 2026 Venkateshwaran R
