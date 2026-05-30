# JAC

> **Just Another Companion/CLI — built on Pydantic AI.**

JAC is a Python CLI that wraps an LLM with persistent memory, tools,
human-in-the-loop gates, multi-provider credentials, and session continuity.
It runs on your machine — your keys, your files, your context.

> **Status:** **v0.8.0** (pre-release). Latest: an end-stage review hardening pass — sub-agent `allowed_tools` now genuinely sandboxes a worker, bidirectional comms redesigned to a suspend/resume `ask_supervisor` flow, and a surface-agnostic `SessionDriver` + `jac.sdk` facade that unlocks non-CLI surfaces. Earlier: interaction modes (`/mode plan|accept-edits`), compaction control (`/compact`, `/context`), the MCP loader (Phase F), parallel sub-agents (Phase E), the skill loader (Phase D), context-cost controls + sequential sub-agents (Phases A/B). Phase 4 A2A is feature-complete for v1 scope.
> See [implementation progress](https://venkateshwaran-r.github.io/JAC/progress/) for the live state.

## What it does

- Interactive REPL with a coworker persona ("Gru") — thinks, calls tools, waits for your approval.
- File, search, shell, web, and background process tools — all approval-gated with a stated reason.
- Two-scope memory (`remember` / `forget`) writing to `~/.jac/memory.md` and `<repo>/.agents/memory.md`.
- Session persistence with `jac --resume`. Multi-provider profiles via `jac profiles`.
- Token-aware history compaction; token budgets; `/` slash commands in-REPL.
- A2A interop — expose this Gru to peer agents or call other A2A-compatible agents, with bidirectional file transfer and pluggable peer auth (bearer / API key / OAuth2). See [`examples/data-analyst-a2a/`](examples/data-analyst-a2a/) for a working reference peer.
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

Full user guide: **[Getting started](https://venkateshwaran-r.github.io/JAC/user-guide/getting-started/)**

## Documentation

**Published site:** [https://venkateshwaran-r.github.io/JAC/](https://venkateshwaran-r.github.io/JAC/)

| | |
| --- | --- |
| **User guide** | [Getting started](https://venkateshwaran-r.github.io/JAC/user-guide/getting-started/) · [CLI reference](https://venkateshwaran-r.github.io/JAC/user-guide/cli-reference/) · [Configuration](https://venkateshwaran-r.github.io/JAC/user-guide/configuration/) · [Sessions & memory](https://venkateshwaran-r.github.io/JAC/user-guide/sessions-and-memory/) · [Examples](https://venkateshwaran-r.github.io/JAC/user-guide/examples/) · [A2A operator](https://venkateshwaran-r.github.io/JAC/user-guide/a2a-operator/) |
| **Developer** | [Contributing](https://venkateshwaran-r.github.io/JAC/developer/contributing/) · [Module strategy](https://venkateshwaran-r.github.io/JAC/developer/module-strategy/) · [Codebase map](https://venkateshwaran-r.github.io/JAC/developer/codebase-map/) · [Capabilities & hooks](https://venkateshwaran-r.github.io/JAC/developer/capabilities/) |
| **Design** | [Idea](https://venkateshwaran-r.github.io/JAC/idea/) · [Architecture](https://venkateshwaran-r.github.io/JAC/architecture/) · [Progress dashboard](https://venkateshwaran-r.github.io/JAC/progress/) · [History](https://venkateshwaran-r.github.io/JAC/progress-history/) · [A2A log](https://venkateshwaran-r.github.io/JAC/progress-a2a/) · [Roadmap](https://venkateshwaran-r.github.io/JAC/progress-roadmap/) |

To edit docs locally: `just docs-serve` → http://127.0.0.1:8000 (sources live under `docs/`).

## Development

```bash
just check       # format + lint + typecheck + tests (same gates as CI)
just fix         # auto-format + lint fix
just docs-serve  # live-reload docs site
```

Contributions welcome — fork, branch, open a PR; CI must pass before merge. See [Contributing](https://venkateshwaran-r.github.io/JAC/developer/contributing/) for the open-source workflow, required reading, and GitHub Actions details.

## Built on

**Core**

- **[Pydantic AI](https://github.com/pydantic/pydantic-ai)** — the agent framework JAC is built on.
- **[fasta2a](https://github.com/pydantic/fasta2a)** — A2A server integration (Phase 4).

**Design inspiration** (not vendored)

- **[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)** — capability patterns JAC adopts selectively (`ApprovalRequiredToolset`, `Instrumentation`, …).
- **[pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents)** — overall agent-harness design reference.

**On the roadmap** (considered, not integrated yet)

- **[memv](https://github.com/vstorm-co/memv)** — predict-calibrate memory extraction (v2).
- **[Monty](https://github.com/pydantic/monty)** — Rust-based Python sandbox for YOLO mode (v2).

## License

[MIT](LICENSE) © 2026 Venkateshwaran R
