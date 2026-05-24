# JAC

> **Just Another Companion/CLI — built on Pydantic AI.**

JAC is a Python CLI that wraps an LLM with persistent memory, tools,
human-in-the-loop gates, multi-provider credentials, and session continuity.
It runs on your machine — your keys, your files, your context.

> **Status:** **v0.1.2 alpha** (pre-release). Phase 1.7 is complete — compaction,
> tiered profiles, slash commands, token budgets, plan persistence on resume.
> Phase 4 A2A is in flight (server, guest-Gru, outbound tools). See
> [`docs/progress.md`](docs/progress.md) for the current state.

## What it does

- Interactive REPL with a coworker persona ("Gru") — thinks, calls tools, waits for your approval.
- File, search, shell, web, and background process tools — all approval-gated with a stated reason.
- Two-scope memory (`remember` / `forget`) writing to `~/.jac/memory.md` and `<repo>/.agents/memory.md`.
- Session persistence with `jac --resume`. Multi-provider profiles via `jac profiles`.
- Token-aware history compaction; token budgets; `/` slash commands in-REPL.
- A2A interop — expose this Gru to peer agents or call other A2A-compatible agents.
- Logfire tracing out of the box.

## Requirements

- **Python 3.13+**
- **macOS or Linux** (Windows untested)
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
# Persistent install (adds `jac` to PATH)
uv tool install "git+https://github.com/VENKATESHWARAN-R/JAC.git"

# One-off, no install
uv run --from "git+https://github.com/VENKATESHWARAN-R/JAC.git" jac --help

# From a local clone
git clone https://github.com/VENKATESHWARAN-R/JAC.git && cd JAC && uv sync
```

## Quick start

```bash
jac init        # set secrets backend, create first profile, store API key
jac             # start a session
jac --resume    # continue the last session
```

Full user guide: **[docs/user-guide/getting-started.md](docs/user-guide/getting-started.md)**

## Documentation

| | |
| --- | --- |
| **User guide** | [Getting started](docs/user-guide/getting-started.md) · [CLI reference](docs/user-guide/cli-reference.md) · [Configuration](docs/user-guide/configuration.md) · [Sessions & memory](docs/user-guide/sessions-and-memory.md) · [Examples](docs/user-guide/examples.md) · [A2A operator](docs/user-guide/a2a-operator.md) |
| **Developer** | [Contributing](docs/developer/contributing.md) · [Codebase map](docs/developer/codebase-map.md) · [Capabilities](docs/developer/capabilities.md) |
| **Design** | [Idea](docs/idea.md) · [Architecture](docs/architecture.md) · [Progress](docs/progress.md) |

Published as a static site: `just docs-serve` → http://127.0.0.1:8000

## Development

```bash
just check       # format + lint + typecheck
just fix         # auto-format + lint fix
just docs-serve  # live-reload docs site
```

See [docs/developer/contributing.md](docs/developer/contributing.md) for the full recipe list.

## Built on

- **[Pydantic AI](https://github.com/pydantic/pydantic-ai)** — the agent framework JAC sits on top of.
- **[fasta2a](https://github.com/pydantic/fasta2a)** — A2A server integration.
- **[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)** — official capability library.
- **[pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents)** — design inspiration.
- **[memv](https://github.com/vstorm-co/memv)** — predict-calibrate memory pattern (target for v2).
- **[Monty](https://github.com/pydantic/monty)** — Rust-based Python sandbox (target for v2 YOLO).

## License

[MIT](LICENSE) © 2026 Venkateshwaran R
