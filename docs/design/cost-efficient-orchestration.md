# Cost-efficient orchestration — Phase A-G design

> **Status:** Active design · **Last revised:** 2026-05-26 · **Type:** design spec, implementation-ready
>
> Companion documents: [`../architecture.md`](../architecture.md) §0 (thesis) and §5 (D34–D42); [`../progress.md`](../progress.md) (live tracker). Old Phase 3/5/6 entries archived to [`../progress-archive-2026-05.md`](../progress-archive-2026-05.md).

## 1. Thesis (one paragraph)

JAC's product thesis is that the LLM is the brain and everything around it — what enters the context, when it enters, which tier processes it, what work is delegated elsewhere, how big tool outputs are filtered — is JAC's responsibility. Cost ≈ Σ over turns of `turn_tokens × turn_price`. The price-per-token is exogenous; JAC controls turn tokens. Every architectural decision is judged against that equation.

## 2. The five levers

| Lever | What it does | Phase |
| --- | --- | --- |
| L1. **Sub-agents** | Delegate context-heavy work; intermediate tokens stay in the sub-agent's loop, only the result returns. | B |
| L2. **Tier-aware selection** | Profiles map small/medium/large to ordered model lists (D22). Sub-agent spawn picks a tier (D39). Main agent stays on its profile tier. | Partial (D22 shipped); extended in B |
| L3. **Tool result post-processor** | Above threshold + cheap tier available → route raw output through small model, return summary + disk path (D38). | A |
| L4. **Cache-friendly prompt assembly** | Order system prompt → tools → memory → history so the prompt-cache breakpoint sits at the stable/changing boundary. | A |
| ~~L5. Deterministic post-flight hooks~~ | ~~Dropped.~~ Complexity didn't earn its keep — `success_criteria` in the task packet plus a post-return `run_shell` call from the main agent covers the use case without framework machinery. | — |

## 3. Phase A — Context-cost foundation

**Goal:** stop wasting tokens on raw tool output and prompt cache misses *before* introducing any sub-agents. This is pure plumbing and likely the single biggest cost reduction available today.

### A.1 Tool result post-processor (D38)

**Trigger conditions** (all must hold):

1. `len(tokenize(result)) > settings.cost.tool_result_threshold_tokens` (default `8000`)
2. Profile has a `small` tier with at least one configured model
3. The `small` tier's output price is strictly less than the current agent's tier's output price (use `providers.yaml` pricing metadata)
4. Tool name is not in `settings.cost.no_summarize_tools` opt-out list

**Mechanism:**

```python
# pseudo-code, lives in jac.runtime.tool_summarize
async def maybe_summarize_tool_result(
    *,
    tool_name: str,
    raw: str,
    run_id: str,
    call_id: str,
) -> str:
    if not _should_summarize(tool_name, raw):
        return raw

    cached_path = paths.tool_result_cache(run_id) / f"{call_id}.txt"
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_text(raw)

    small_model = profiles.active().tier("small").first_available()
    summary = await direct.model_request(
        small_model,
        prompt=_TOOL_SUMMARIZE_PROMPT.format(tool=tool_name, raw=raw),
    )
    return (
        f"[AI-summarized via {small_model}: original {token_count(raw)} tokens — "
        f"full output at {cached_path}]\n\n{summary}"
    )
```

**Plumbing:** wrap the `FunctionToolset.call_tool` boundary. Apply to both Gru and any sub-agent (sub-agents *especially* benefit — they often do big greps/reads and would otherwise re-process the same output every internal turn).

**Settings additions:**

```yaml
cost:
  tool_result_threshold_tokens: 8000   # opt-in cap; null disables
  no_summarize_tools: []               # e.g. ["read_file", "bash"]
  summarize_prompt_template: "..."     # optional override
```

**Tests:**

- Above threshold → calls direct.model_request, returns tagged summary.
- Below threshold → passthrough.
- No small tier configured → passthrough (no crash, no warning spam).
- Small tier *not cheaper* than current tier → passthrough.
- Opted-out tool name → passthrough.
- Cache file written and re-readable via `read_file`.

### A.2 Cache-friendly prompt assembly (L4)

**Audit:** read `Gru.build_instructions()` and `jac.workspace.context` to confirm the order is:

1. **Stable header** — JAC system prompt (`gru_system.md`), tool definitions, capability instructions, providers/budgets context
2. **Slowly-changing** — AGENTS.md user + project, memory.md user + project
3. **Per-turn changing** — message history (compacted), current user prompt

Anthropic's prompt cache sets implicit breakpoints; we want the *cumulative* prefix at "end of slowly-changing" to be stable across turns so the cache hit lands there.

**Actions:**

- Order the assembly explicitly in code with a comment marking the cache boundary.
- Move any time-of-day or session-id strings out of the system prompt (they break caching).
- If any capability injects mutable text via `get_instructions()`, audit whether it actually needs to be in the system prompt or could be a tool the agent calls on demand.

### A.3 `/tokens` breakdown improvements

Show:

- Total session input / output
- A2A guest (already there per Phase 4.d)
- **NEW:** tool-result summarization tokens (small-tier calls)
- **NEW:** sub-agent input/output (once Phase B ships) — rolls up into session total
- Cache hit rate if the provider returns it (Anthropic does)

## 4. Phase B — Sub-agent tool

### B.1 The tool

```python
@jac_tool(approval_required=True)
async def spawn_sub_agent(
    ctx: RunContext[JacDeps],
    reason: str,
    task_summary: str,                  # short prose, shown in HITL
    tier: Literal["small", "medium", "large"],
    task_packet: SubAgentTaskPacket,
) -> SubAgentResult:
    """Delegate a context-heavy task to an isolated sub-agent.

    The sub-agent runs with its own context (cold start — no inherited history).
    Use this when the task would otherwise consume >20k tokens of intermediate
    context the main agent doesn't need to keep.
    """
```

**HITL approval line** (rendered by the CLI when this fires):

```
Approve sub-agent spawn?
  reason       : explore matplotlib examples and summarize chart APIs
  task_summary : read matplotlib/examples/api/ and return a 2-paragraph
                 summary of the Figure/Axes API patterns most relevant
                 to time-series plots
  tier         : small  (haiku per profile)
  tools        : read_file, grep, glob, list_dir
  max_turns    : 10
  hooks        : —
[a]pprove  [d]eny  [t]ier:medium  [t]ier:large
```

### B.2 Task packet (D36)

```python
class SubAgentTaskPacket(BaseModel):
    objective: str
    success_criteria: str
    relevant_paths: list[str] = []
    forbidden_actions: list[str] = []
    expected_output: str                 # free-text spec; structured returns
                                         # use `result_type` on the sub-agent
                                         # (out-of-band) — keep packet simple
    allowed_tools: list[str] | None = None  # None = inherit main minus spawn
    max_turns: int = 10


class SubAgentResult(BaseModel):
    ok: bool
    output: str                          # full-fidelity, NOT compressed
    turns_used: int
    tokens_in: int
    tokens_out: int
    cached_artifacts: list[str] = []     # disk paths to large outputs
```

**Note on `expected_output`:** kept as free-text in v1. If a caller needs structured output, the sub-agent's `Agent` is built with `result_type=SomeModel` separately; the packet just describes intent.

### B.3 Sub-agent agent construction

```python
async def build_sub_agent(packet: SubAgentTaskPacket, tier: str) -> Agent:
    model = profiles.active().tier(tier).first_available()
    toolset = main_toolset.filter(
        include=packet.allowed_tools,
        exclude=["spawn_sub_agent"],     # D40 — hard cap depth 1
    )
    return Agent(
        model,
        toolset=toolset,
        system_prompt=_render_sub_agent_system(packet),
        instrument=True,                 # parent_run_id chains via Logfire
    )
```

**System prompt** for the sub-agent is short: objective, success criteria, expected output, forbidden actions, the (often few) tools available. Does *not* include AGENTS.md or memory.md — those are main-agent context.

### B.4 Budget rollup

`UsageTracker.add_sub_agent(input, output, tier)` — counts toward `session_total` (this is the same user-driven turn, unlike A2A external usage which feeds only `project_total`). JSONL row: `{"kind": "sub_agent", "tier": "small", "in": ..., "out": ...}`.

`/tokens` gains a "sub-agents" line when count > 0.

### B.5 Logfire

Sub-agent span nests under the spawning tool-call span. `parent_run_id` chain is automatic via PAI's instrumentation. Span fields: `tier`, `objective` (truncated to 100 chars), `turns_used`, `hook_failures`, `exit_status`.

### B.6 Failure modes

| Failure | Returned to main agent |
| --- | --- |
| max_turns hit without final answer | `ok=False`, `output="sub-agent did not converge in N turns"` |
| Sub-agent tool call raised | `ok=False`, `output="<tool> raised: <exc>"` |
| User denied spawn approval | Standard `denied_with_feedback` flow (D26) |
| Sub-agent budget exceeded | `ok=False`, `output="sub-agent exceeded its turn budget"` |

## 5. Phase D — Skill loader

Skills are **loadable prompts / playbooks** — the main agent reads them when relevant. They are *not* a runtime mode (no `mode: minion`).

### D.1 Format

Anthropic community spec: `~/.jac/skills/<name>/SKILL.md` and `<repo>/.agents/skills/<name>/SKILL.md`. YAML frontmatter + markdown body.

```yaml
---
name: code-review
description: |
  Review a diff for correctness, security, and style. Use when the user asks
  for a review, when finalizing a feature branch, or before a PR.
---
# Code review checklist

When reviewing a diff:

1. Read the full diff first; don't react to the first hunk.
2. ... etc ...

## When to delegate

If the diff touches >10 files, consider `spawn_sub_agent(tier='small')` with
a per-file review, then aggregate yourself.
```

### D.2 Loader

- Walks both locations; project shadows user on name collision.
- Validates frontmatter (Pydantic model: `name`, `description`, optional `tools_required`).
- Hard cap: total skill descriptions injected into system prompt ≤ 2 KB. If installed skills exceed that, only `name + description` is included in-prompt; bodies are loaded on demand.

### D.3 Triggering

Two paths:

1. **Description-based** — the loader publishes skill names + descriptions to a `SkillsCapability.get_instructions()` block that says: "If a user request resembles a skill description, call `load_skill(name)` to read the body." The model decides when to load.
2. **Explicit** — `/skill use NAME` slash, `/skill list` slash.

The `load_skill(reason, name)` is a tool (carries `reason: str` per discipline) that returns the skill body as a user message inserted into the next turn's context.

### D.4 Reference skills

Ship 2–3 in `src/jac/data/skills/`:

- `code-review` — diff review checklist + delegation hint
- `summarize-large-files` — when to spawn small-tier sub-agent for big files
- `verify-change` — typecheck + test recipe + hook suggestions

## 6. Phase E — Parallel sub-agents + HITL multiplexing

**Deferred until B + D are settled.** Outline:

- `spawn_sub_agents([packet1, packet2, ...])` — single tool, takes a list.
- HITL: all approvals batched into one prompt (`Approve N sub-agent spawns? [a]ll [d]eny [r]eview each`).
- Each sub-agent runs in its own `asyncio.Task`; results collected with `asyncio.gather`.
- HITL approvals from *within* sub-agents bubble up serially to the same prompt-toolkit input (only one approval prompt visible at a time).
- Logfire: parallel branches under the same parent span.

## 7. Phase F — MCP loader (promoted 2026-05-27)

Promoted ahead of Plan Mode after external review. Rationale: MCP is the ecosystem surface most users try first; A2A's protocol-design work was the right early bet, but daily-workflow integrations now block on MCP more than on planning. Plan Mode also benefits from MCP tools being available when the plan executes. Scope per D28: `mcp.yaml` layered loader, `MCPServerStdio` / `MCPServerHTTP` wiring, slash surface (`/mcp list` / `/mcp reload`), MCP outputs route through `SummarizingToolset` automatically. MCP tools skip the `reason: str` enforcement per D28.

## 8. Phase G — Plan Mode (demoted to follow MCP)

Originally v2 (D23). Pulled forward because plans are more valuable now that the agent has the option to delegate. A plan step like "explore A/B/C and decide" is a natural sub-agent boundary. Demoted from old Phase F to follow MCP — see Phase F rationale. Implementation per D23: structural toolset swap (read-only + `write_plan`); the bundled `plan`→`tasks` rename moves with it. Builds the `ModeCapability` base, which YOLO mode (v2) will reuse via the `approval_override` knob.

## 8a. Phase H — A2A 4.e + broader tests

Lower priority but still planned. (Old Phase G minus MCP.) A2A 4.e covers OIDC discovery and GCP id-token auth strategies for outbound peers; broader test coverage targets session, fs/shell bus, memory, and slash edge cases. See `progress.md` for the live list.

## 9. ⚠️ Risk areas (proceed with care)

### 9.1 Bidirectional sub-agent ↔ main-agent comms (D41)

**Specced, but ship cautiously. Default-off behind a feature flag in v1.**

The mechanism: a sub-agent can call `ask_main_agent(reason, question, context)`. The question is delivered as the `spawn_sub_agent` tool's *intermediate yield*. Main agent processes the question as a tool result, calls `respond_to_sub_agent(answer)` (or any other tool — including more thinking), and the sub-agent resumes.

**Why it's risky:**

| Risk | Mitigation |
| --- | --- |
| Doubled per-question cost (each round-trip = an extra main-agent turn) | Hard cap: 5 round-trips per spawn. Logfire warning at 3. |
| Confusing transcript UX (interleaved sub-agent and main-agent turns) | Renderer must paint explicit `[sub-agent → main: question]` / `[main → sub-agent: answer]` markers; collapse by default. |
| HITL multiplexing (sub-agent's tool wants approval *and* sub-agent is asking main agent something) | Same renderer slot; serialize prompts; don't lose state across approvals. |
| Loops (sub-agent keeps asking, main keeps answering, no progress) | Round-trip cap + Logfire metric `ask_main_agent_count` per spawn. |
| Deadlock if main is waiting on user input | Cannot happen by construction — main is already blocked in the spawn tool call; it's not awaiting user input. |

**v1 implementation gate:** the tool isn't registered into the sub-agent toolset unless `settings.cost.sub_agent_bidirectional = true`. Default `false`. Flip when UX has been validated.

### 9.2 Tier cascading

If a profile has only `medium` configured and someone spawns with `tier="small"`, the cascade picks `medium` and the HITL line shows `tier: small (resolved to medium — no small tier in profile)`. Don't silently use a more expensive tier without telling the user. Same logic for `medium → large`.

### 9.3 Cache invalidation footguns

Anything that goes into the system prompt becomes part of the cached prefix. If we accidentally include a timestamp, session-id, or other per-turn-changing value in the prefix, every turn is a cache miss. Phase A.2 audit is *the* defense.

## 10. Implementation order (firm)

1. **Phase A.1** (post-processor) + **A.3** (`/tokens`) — biggest immediate cost win, no model behavior changes.
2. **Phase A.2** (cache-friendly assembly audit) — must precede sub-agent work because sub-agent system prompts go through the same plumbing.
3. **Phase B** (sub-agent tool) — sequential only, no bidirectional, no parallel.
4. **Phase D** (skill loader) — gives the main agent the playbooks it needs to use sub-agents well.
5. **Phase E** (parallel + bidirectional flag-flip after validation).
6. **Phase F** (MCP loader) — promoted from old Phase G; ecosystem surface that unblocks daily-workflow integrations.
7. **Phase G** (Plan Mode) — demoted from old Phase F to follow MCP; benefits from MCP tools being available when the plan executes.
8. **Phase H** (A2A 4.e + broader tests).

## 11. Open questions tracked elsewhere

The gaps I called out in design discussion are tracked under `architecture.md §5 "Still open"`:

1. **Sub-agent file system semantics** — confirmed lean: same toolset, same filesystem view, HITL per-tool (Phase B grooming).
2. **Logfire UX for nested traces** — depth cap of 1 (D40) is the v1 answer; richer nested rendering is a future concern.
