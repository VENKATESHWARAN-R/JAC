# Configuration

> **Audience:** operators tuning profiles, cost guardrails, and secrets.

JAC layers configuration from several sources. **Higher precedence wins.**

| Priority | Source | Examples |
| --- | --- | --- |
| 1 (highest) | CLI flags | `--model`, `--profile` |
| 2 | Environment variables | `JAC_MODEL`, `JAC_SECRETS__BACKEND`, `JAC_COMPACTION__MAX_CONTEXT_TOKENS` |
| 3 | `.env` in current working directory | Provider API keys |
| 4 | Project YAML | `<repo>/.agents/config.yaml` |
| 5 | User YAML | `~/.jac/config.yaml` |
| 6 (lowest) | Package defaults | `src/jac/data/defaults.yaml` (non-required tunables only) |

Required values (model, credentials) have **no code default**. Missing config raises `JacConfigError` with fix instructions.

Nested env keys use `__`: e.g. `JAC_BUDGET__SESSION_TOTAL_TOKENS=500000`.

## Profiles

Profiles live in `~/.jac/config.yaml`:

```yaml
default_profile: claude
secrets:
  backend: keyring
profiles:
  claude:
    active_tier: medium
    tiers:
      small:
        - anthropic:claude-haiku-4-5
      medium:
        - anthropic:claude-sonnet-4-5
      large:
        - anthropic:claude-opus-4-6
    env: {}
```

- **`tiers`** — map of tier name → ordered model list; **first entry is the tier default**.
- **`active_tier`** — which tier Gru uses on REPL start (`JAC_MODEL` = that tier's first model).
- **`env`** — non-secret vars injected on profile activation (e.g. `OLLAMA_BASE_URL`).
- **`requires_env`** — optional explicit list of secret env vars; if omitted, inferred from all tier models via `providers.yaml`.

### Managing profiles

```bash
jac profiles              # list (marks default)
jac profiles use NAME     # set default_profile
jac profiles edit NAME    # $EDITOR with validate-on-save
jac profiles remove NAME  # removes profile; stored keys kept
```

In the REPL: `/profile` lists; `/profile NAME` switches (rebuilds Gru with env snapshot/rollback on failure).

`/model` switches the concrete model for the session without changing the default profile — see [CLI reference](cli-reference.md).

### Provider catalog

Shipped: `src/jac/data/providers.yaml`. Optional overlay: `~/.jac/providers.yaml` (bootstrap writes `providers.yaml.example`).

## Secrets

Backend: `secrets.backend` in user YAML (`keyring` | `dotenv` | `env-only`).

```bash
jac keys                  # status for all profiles' required keys
jac keys set ANTHROPIC_API_KEY   # interactive prompt (hidden)
jac keys unset ANTHROPIC_API_KEY
```

Resolution order at runtime: **process environment** → configured backend → fail-first.

`--model PROVIDER:ID` still resolves that provider's keys best-effort without selecting a profile.

Optional feature keys (not fail-first): e.g. `TAVILY_API_KEY` upgrades `web_search` to Tavily when present.

## Compaction (context budget)

Compaction uses a **user-configurable token budget**, not the model's advertised context window (`compaction` in YAML or `JAC_COMPACTION__*`).

Defaults from `defaults.yaml`:

| Key | Default | Meaning |
| --- | --- | --- |
| `max_context_tokens` | `200000` | Budget Gru measures against |
| `warn_pct` | `60` | Status bar / warning event |
| `auto_compact_pct` | `70` | Auto-summarize oldest history (profile's **small** tier model) |
| `refuse_pct` | `85` | Block next user turn until space freed (`/clear` or config change) |
| `target_pct_after_compact` | `50` | Target size after auto-compaction |

Example project override in `<repo>/.agents/config.yaml`:

```yaml
compaction:
  max_context_tokens: 150000
  refuse_pct: 80
```

Dropped slices are archived under `<session>/compacted/<n>.json` for debugging.

There is **no** `/compact` slash command — compaction runs automatically inside the history processor.

## Token budgets (D25)

All budget knobs are **opt-in** (`null` = disabled). Configure under `budget:` in YAML or via env.

| Key | Env | Measures |
| --- | --- | --- |
| `session_input_tokens` | `JAC_BUDGET__SESSION_INPUT_TOKENS` | Cumulative input tokens this session |
| `session_total_tokens` | `JAC_BUDGET__SESSION_TOTAL_TOKENS` | Input + output this session |
| `project_total_tokens` | `JAC_BUDGET__PROJECT_TOTAL_TOKENS` | Input + output across all sessions in repo (`usage.jsonl` + live session) |

Shared thresholds:

- `warn_pct` (default `80`) — one-time warning event
- `hardstop_pct` (default `100`) — refuse next turn pre-flight

REPL:

- `/budget` — table of limits vs usage
- `/budget extend N` — add tokens to `session_total` for this session only
- `/budget extend project_total 1000000` — extend a specific kind
- `/tokens` — raw counters

Status bar shows `bud:` only when at least one limit is set.

## Cost controls (tool result post-processor)

Large outputs from opted-in tools (today: `run_shell`, `web_search`, `fetch_url`) are routed through the active profile's `small`-tier model and replaced with a summary before they enter Gru's context. Original output is always saved to `<repo>/.agents/cache/tool-results/<session>/<call-id>.txt` so the agent can re-read via `read_file` when needed. See [Cost controls](cost-controls.md) for the full story.

```yaml
cost:
  tool_result_threshold_tokens: 8000
  no_summarize_tools: []         # force-off list (overrides decorator opt-in)
  summarize_tools: []            # force-on list (overrides decorator default)
  sub_agent_bidirectional: true  # D41: ask_main_agent / respond_to_sub_agent (on since v0.4.x)
```

Summarization is skipped (raw passthrough) when no `small` tier is configured, the small model isn't strictly cheaper than the current tier, or the tool didn't opt in. Pricing lookup uses `providers.yaml`.

`sub_agent_bidirectional` is the D41 feature flag — on by default since v0.4.x. Set to `false` if you'd rather sub-agents finalize with what they have than pause to ask the main agent a clarifying question. See [Cost controls → Bidirectional comms](cost-controls.md) for the full semantics, including the 5-round-trip cap and the "finalize with what you have" directive on the 6th ask.

## A2A block (per profile)

Optional `a2a:` section on each profile — see [A2A operator](a2a-operator.md). Defaults: `host: 127.0.0.1`, `port: 8001`, `context_retention_days: 3`.

## File layout reference

| File | Purpose |
| --- | --- |
| `~/.jac/config.yaml` | Profiles, default profile, secrets backend |
| `~/.jac/.env` | Secrets if `dotenv` backend |
| `<repo>/.agents/config.yaml` | Project overrides (compaction, budget) |
| `<repo>/.agents/usage.jsonl` | Per-turn token log for project budgets |

Paths are defined in `jac.workspace.paths` — see [Sessions & memory](sessions-and-memory.md) for memory and session paths.

## Environment template

Copy `.env.template` at the repo root for variable names. Common entries:

| Variable | Purpose |
| --- | --- |
| `JAC_MODEL` | Active model override |
| `JAC_SECRETS__BACKEND` | Override secrets backend |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, … | Provider keys |
| `TAVILY_API_KEY` | Optional Tavily web search |
| `LOGFIRE_TOKEN` | Cloud tracing |

Process env always wins over stored secrets.
