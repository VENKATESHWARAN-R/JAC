# JAC — Extended Roadmap

> Queued and future-phase context that is useful when planning, but too heavy for the live dashboard. Start with [`progress.md`](progress.md) for current status, [`architecture.md`](architecture.md) §0 for the thesis, and [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) for the full Phase A–G design spec.
>
> **Pre-2026-05-26 entries** (old Phase 3 Skills with `mode: minion`, Phase 5 Minion runtime, Phase 6 MCP) are in [`progress-archive-2026-05.md`](progress-archive-2026-05.md). This file covers only the *current* arc.

## The thesis (one paragraph)

JAC is orchestration around an intelligent layer. The model thinks; JAC controls what enters the context, when, in what tier, and via which delegation pattern. Cost ≈ Σ(`turn_tokens × turn_price`). The model's price is exogenous; JAC controls `turn_tokens`. The active roadmap (Phases A–G) is structured strictly around lowering `turn_tokens` for equivalent or better task outcomes.

## Phase A — Context-cost foundation 🚧

Pure plumbing. Highest leverage available today before any agent-architecture change. Goal: cut 30–50% off long-session cost without changing how the agent loop behaves.

Three things, only:

1. **Tool result post-processor (D38).** Any tool result over the threshold (default 8000 tokens) AND with a cheaper-per-output-token small tier available gets routed through `pydantic_ai.direct.model_request` against the small tier with a fixed summarize prompt. Tagged result returns to the agent loop; original cached to disk for re-read.
2. **Cache-friendly prompt assembly.** Audit `Gru.build_instructions()` so that the cumulative prefix at "end of slowly-changing content" (after AGENTS.md + memory.md, before history) is stable across turns. Anthropic's cache hits at 10% of input — money on the table.
3. **`/tokens` breakdown.** Add summarization usage, future sub-agent usage placeholder, cache-hit rate.

**Why first:** doesn't depend on any new architecture. Applies to *every* tool call, including the future sub-agents'. Compounds with every later phase.

## Phase B — Sub-agent tool

A single tool: `spawn_sub_agent(reason, task_summary, tier, task_packet)`.

Key constraints baked into the design:

- **Sequential only in v1.** Parallel goes to Phase E. Adding parallelism before the single-spawn UX is solid is asking for HITL multiplexing pain.
- **Depth cap = 1** (D40). The sub-agent's toolset is constructed without `spawn_sub_agent` — structural enforcement, not prompt trust.
- **Tier, not model.** The HITL approval line shows the tier; user can counter-propose. Profile cascades small→medium→large if a tier is unconfigured, with the cascade noted in the approval line.
- **Result is NOT compressed.** Inter-agent communication is full-fidelity. The cost saving comes from *intermediate* tokens never reaching the main loop, not from a smaller final result.
- **Cold context.** Sub-agent starts with only the task packet; no inherited AGENTS.md or memory. `relevant_paths` in the packet lets the main agent point the sub-agent at the right files.

See `design/cost-efficient-orchestration.md` §4 for the full schema (task packet, result, capability, prompt template) and §10.2 for the tier cascade rule.

## Phase C — Deterministic hooks

Hooks are pure callables (Python or shell) returning `(ok, output)`. Attached per-spawn via the task packet. Retry budget = 3 (hard-coded in v1, per D37).

**The cost win:** all hooks passing means the sub-agent's response returns *verbatim* — no extra LLM turn just to ask "did the tests pass?" That's the whole point.

**The fix loop:** any hook fails → the failure output becomes the next user message into the *same* sub-agent. Sub-agent retries. Up to 3 times total. After that, `SubAgentResult(ok=False, output="hooks exhausted: <hook>: <last_output>")` returns to the main agent, which decides what to do.

**Hard invariant:** hooks must not call LLMs. A hook that calls an LLM = sub-agent inside sub-agent = abandoned design.

## Phase D — Skill loader

Anthropic community format (the spec is locked, the field is not invented here). `~/.jac/skills/<name>/SKILL.md` + `<repo>/.agents/skills/<name>/SKILL.md`.

**Critical reframe vs. the archived design:** skills are **loadable prompts / playbooks**, not a runtime mode. There is no `mode: minion`. A skill body is markdown the main agent reads when relevant — possibly with a prose recommendation like "for diffs over 10 files, spawn a small-tier sub-agent for per-file reviews." The skill never *causes* a sub-agent to spawn; the agent decides.

**Discipline:** descriptions injected into the system prompt are capped at 2 KB total. If installed skills exceed that, only names+descriptions go in-prompt; bodies load on demand via `/skill use NAME` or a model-emitted `load_skill(name)` tool call.

Ships 2–3 reference skills: `code-review`, `summarize-large-files`, `verify-change`.

## Phase E — Parallel + bidirectional comms

Polish on top of B–D. Two pieces, both gated:

- **Parallel spawn**: `spawn_sub_agents([packet1, packet2, ...])`. Single tool, list of packets. Batched HITL approval. `asyncio.gather` with HITL serialization.
- **Bidirectional comms (D41) — feature flag, default OFF.** Sub-agent can call `ask_main_agent(reason, question, context)`; main agent answers; sub-agent resumes. Round-trip cap of 5 per spawn. Renderer paints `[sub-agent → main]` markers. Ships with the flag disabled in `settings.cost.sub_agent_bidirectional` until UX is validated.

The bidirectional comms is the highest-risk new capability in the roadmap. Read [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §10.1 for the explicit risk table before implementing.

## Phase F — Plan Mode (D23 promoted)

Plan Mode was v2; the reframe pulled it forward because the main agent benefits from planning *before* spawning sub-agents. A plan step like "explore A/B/C and decide" is a natural sub-agent boundary.

Implementation per D23: structural toolset swap (read-only + `write_plan`); bundled `plan`→`tasks` rename moves with it. Builds the `ModeCapability` base that YOLO (still v2) will reuse.

## Phase G — A2A 4.e + MCP + tests

Lower priority but still planned.

- **A2A Phase 4.e:** `OidcAuth` (discovery from `.well-known/openid-configuration`) and `GcpIdTokenAuth` (via `google-auth`, behind `jac[gcp]` optional dep). Strategy classes + `make_strategy` dispatch branches. User-guide examples for Azure / GCP / Okta peers.
- **MCP loader (D28 already in architecture):** `~/.jac/mcp.yaml` schema + loader; `MCPServerStdio` / `MCPServerHTTP` wiring; `/mcp list`, `/mcp reload`.
- **Broader test coverage:** Phase 1 core (session, fs/shell bus), memory (`remember`/`forget`), slash edge cases.

## Three open gaps tracked elsewhere

These are flagged under `architecture.md` §5 "Still open":

1. **Sub-agent file system semantics** — Phase B grooming sub-item. Current lean: same toolset as main, same filesystem view, HITL per-tool. File writes still go through approval the same way the main agent's do.
2. **Hook scope beyond per-spawn** — skill-declared default hooks and project-global hook config are deferred to Phase D+. Keep per-spawn only in v1.
3. **Logfire UX for nested traces** — depth cap of 1 (D40) bounds the chain in v1. Richer nested rendering is a future concern.

## v2 ⏸

Unchanged from the prior plan:

- YOLO mode + sandboxing (Monty + `sandbox-exec` / `bwrap` + Git-Clean Guard) — uses `ModeCapability`'s `approval_override` knob (D29 sketch). `ModeCapability` is built in Phase F so this slots in cleanly.
- CodeMode integration (`pydantic-ai-harness`).
- Stuck-loop detection.
- Night Shift / cron scheduling.
- User-tier memory + predict-calibrate extraction.
- Browser / API / SDK surfaces.
