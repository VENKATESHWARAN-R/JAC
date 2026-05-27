# Cost controls

> **Audience:** anyone running JAC against a paid model who wants to keep the bill predictable.

JAC's biggest cost driver is **how many tokens flow through the main agent's context window each turn**. The tool-result post-processor (Phase A.1) is the first of several levers that attack this directly — it stops large tool outputs from polluting Gru's history when a cheaper model can extract the gist.

## What runs when

When a tool that opted in via `@jac_tool(summarizable=True)` returns more than `cost.tool_result_threshold_tokens` (default `8000`), JAC routes the raw output through the active profile's **small-tier** model and returns the summary in place of the raw bytes. The original is always saved to disk so Gru can re-read it via the existing `read_file` tool when it needs the exact content.

Tools opted in today:

| Tool | Why summarize |
| --- | --- |
| `run_shell` | Build / test / install logs can run to tens of thousands of lines; the agent rarely needs more than the failing assertion and exit code |
| `web_search` | Result lists are noisy and quote-padded |
| `fetch_url` | Full page text can be ~50 KB of mostly-irrelevant chrome |

Tools deliberately **not** opted in:

| Tool | Why not |
| --- | --- |
| `read_file` | The agent needs exact code and line numbers — summarization would destroy both |
| `list_dir`, `grep_files`, plan / clarify / memory tools | Output is structured or already short |

## The skip rules

Summarization runs only when **all** of these hold. Any miss falls back to raw passthrough — JAC never guesses when the answer is uncertain.

1. The tool either opted in via `@jac_tool(summarizable=True)` **or** appears in `cost.summarize_tools`.
2. The tool does **not** appear in `cost.no_summarize_tools`.
3. The output exceeds `cost.tool_result_threshold_tokens` (estimated as chars / 4).
4. The active profile has a `small` tier configured.
5. The small-tier model is **strictly cheaper per output token** than the current tier, per `providers.yaml`. Unknown pricing on either side counts as "skip".

When summarization runs, the model sees a result block shaped like:

```
[AI-summarized via anthropic:claude-haiku-4-5 — original 14823 tokens, full output at .agents/cache/tool-results/2026-05-27T14-22-01/abc123.txt]

<summary body>
```

The path is relative to your project root. To get the original back, the agent (or you) can call `read_file` on it.

## Settings

All under `cost:` in `~/.jac/config.yaml` or `<repo>/.agents/config.yaml`:

```yaml
cost:
  tool_result_threshold_tokens: 8000
  no_summarize_tools: []   # e.g. ["run_shell"] to force-off
  summarize_tools: []      # e.g. ["fetch_url"] to force-on
```

Environment overrides use the standard nested form: `JAC_COST__TOOL_RESULT_THRESHOLD_TOKENS=4000`, `JAC_COST__NO_SUMMARIZE_TOOLS='["run_shell"]'`.

The full summarizer prompt template lives at `cost.summarize_prompt_template` — override it the same way if you want different shape (e.g. structured JSON output).

## Where the originals live

```
<repo>/.agents/cache/tool-results/<session-id>/<call-id>.txt
```

The directory is created on first use. JAC does not currently auto-prune this directory — it's part of the session artifact set, and a single tool call rarely exceeds a few hundred KB. Delete the session folder when you delete the session.

## Visibility

`/tokens` shows a `summarize:` line whenever at least one summarization has fired this session:

```
summarize: calls=4  original=42,100  summary=3,800  saved=38,300
           (small-tier spent in=42,400 out=3,800)
```

- **`saved`** is the headline number — tokens removed from Gru's context window
- **`small-tier spent`** is what you actually paid the small model for the round trip

If your provider reports prompt cache stats (Anthropic does), `/tokens` also shows a `cache:` line with read / write / hit-rate.

Every summarization emits a Logfire span (`tool_summarize`) with `tool_name`, `original_tokens`, `summary_tokens`, `saved_tokens`, `summarizer_model`, and `cache_path` — useful for cross-session analysis.

## Sub-agents (delegation)

The next lever past the post-processor is **delegating context-heavy work to a sub-agent**. Gru calls `spawn_sub_agent(reason, task_summary, tier, task_packet)`; a fresh agent runs in its own isolated loop with its own message history and returns a short final answer. The 50k–200k tokens of intermediate file reads / shell output / web fetches stay over there — Gru's context never sees them.

**Cost model.** The sub-agent's tokens roll into your session totals via `UsageTracker.add_sub_agent` — same budget envelope as the main loop, just attributed differently. `/tokens` shows a `sub_agents:` line with per-tier breakdown so you can see what delegation actually cost.

**Tier cascade.** The main agent asks for `"small"` / `"medium"` / `"large"`. If your profile lacks the requested tier, JAC cascades **up** (small → medium → large) — never down, since that would silently exceed budget. The cascade is shown in the approval prompt and again in the result header.

**Depth cap = 1.** Sub-agents do not get the `spawn_sub_agent` tool in their own toolset. Enforced structurally — the recursion is impossible, not just disallowed.

**Always HITL-approved.** Every spawn surfaces a prompt with the resolved tier, the cascade note (if any), the packet, and the tool allowlist. Approve / deny with feedback / counter-tier (planned Phase E).

**Hooks (Phase C, queued).** The packet accepts a `hooks: list[HookSpec]` field today but the runner is a stub — Phase C lands the post-flight validators (`ruff_check`, `pytest_run`, etc.) plus the "all-pass → return verbatim, no extra LLM turn" optimization.

### Bidirectional comms (opt-in, D41)

Off by default. Flip `cost.sub_agent_bidirectional: true` in your config to enable. When on:

- The sub-agent gets `ask_main_agent(reason, question, context)` — it can pause once or twice mid-run to ask the main agent a focused clarifying question.
- The main agent gets `respond_to_sub_agent(reason, spawn_id, answer)` — its reply tool. Not approval-gated; you already approved the parent spawn.
- Hard cap = **5 round-trips per spawn**. The 6th `ask_main_agent` call doesn't error — it returns a "finalize with what you have, list any open uncertainties as discrepancies" directive directly to the sub-agent. The spawn always produces a coherent final answer, even if the conversation runs long.
- Visibility: the question lands in the main agent's tool result as a `[sub-agent → main: question pending] spawn_id=...` block; the answer comes back wrapped in `[main → sub-agent: ...]`. You see both as standard tool calls in the scroll-back.

**Cost note.** Every round-trip costs an extra main-agent turn (full context + toolset). A sub-agent asking five questions costs roughly as much as five extra main-agent turns. Worth it for genuinely ambiguous tasks; usually a sign the task packet should have been more specific.

### When to spawn vs. when not to

| Spawn | Don't spawn |
| --- | --- |
| Summarize a sweep of files for the user | A one-shot `read_file` |
| Explore an unfamiliar module + return findings | Anything you need exact text back from |
| Fetch + digest several web pages | A single `fetch_url` call |
| Run a batch of validation commands + report | A single `run_shell` you'll reason over |

The cost lever only fires when the sub-agent saves you *more tokens than its own run consumes*. A spawn that does one tool call and returns is pure overhead — three new model requests just to do what one tool call would have done inline.

## When to opt a new tool in

Use `@jac_tool(summarizable=True)` when **both** are true:

1. The output is often large (≥ several KB of mostly prose / logs / HTML).
2. A bullet-point summary preserves what the agent needs to keep working.

Use the decorator default (`False`) — never opt in — when:

- The agent needs exact text, line numbers, or structure (any "show me the code" tool)
- The output is already small and structured (`list_dir`, status, counts)
- The output is *already* a curated answer from another LLM (e.g. an A2A peer response — summarizing a summary throws away signal)

When in doubt, leave it off. The user can always force-on via `cost.summarize_tools` for a specific case.
