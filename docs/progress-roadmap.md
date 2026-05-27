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

## ~~Phase C — Deterministic hooks~~ (Dropped)

Dropped. The complexity didn't earn its keep. JAC runs in any environment, so baked-in hooks (`ruff_check`, `pytest_run`, `ty_check`) were the wrong design. On-the-fly commands passed per-invocation are equivalent to just including verification steps in `success_criteria` and having the main agent run `run_shell` after the sub-agent returns. No framework machinery needed.

## Phase D — Skill loader

Anthropic community format (the spec is locked, the field is not invented here). `~/.jac/skills/<name>/SKILL.md` + `<repo>/.agents/skills/<name>/SKILL.md`.

**Critical reframe vs. the archived design:** skills are **loadable prompts / playbooks**, not a runtime mode. There is no `mode: minion`. A skill body is markdown the main agent reads when relevant — possibly with a prose recommendation like "for diffs over 10 files, spawn a small-tier sub-agent for per-file reviews." The skill never *causes* a sub-agent to spawn; the agent decides.

**Discipline:** descriptions injected into the system prompt are capped at 2 KB total. If installed skills exceed that, only names+descriptions go in-prompt; bodies load on demand via `/skill use NAME` or a model-emitted `load_skill(name)` tool call.

Ships 2–3 reference skills: `code-review`, `summarize-large-files`, `verify-change`.

## Phase E — Parallel + bidirectional comms

Polish on top of B + D. Two pieces, both gated:

- **Parallel spawn**: `spawn_sub_agents([packet1, packet2, ...])`. Single tool, list of packets. Batched HITL approval. `asyncio.gather` with HITL serialization.
- **Bidirectional comms (D41) — feature flag, default OFF.** Sub-agent can call `ask_main_agent(reason, question, context)`; main agent answers; sub-agent resumes. Round-trip cap of 5 per spawn. Renderer paints `[sub-agent → main]` markers. Ships with the flag disabled in `settings.cost.sub_agent_bidirectional` until UX is validated.

The bidirectional comms is the highest-risk new capability in the roadmap. Read [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §10.1 for the explicit risk table before implementing.

## Phase F — MCP loader (promoted 2026-05-27)

Promoted ahead of Plan Mode after external review. Rationale: MCP is the ecosystem surface most users will try first; A2A's protocol-design work was the right early bet, but daily-workflow integrations now block on MCP more than on planning. Plan Mode also benefits from MCP tools being available when the plan executes.

Scope per D28 (already in architecture):

- `~/.jac/mcp.yaml` + `<repo>/.agents/mcp.yaml` schema; layered loader (project shadows user, mirroring other config and the skill loader).
- `MCPServerStdio` / `MCPServerHTTP` wiring as a capability; tools registered into Gru's toolset.
- Per D28, MCP tools skip the `reason: str` enforcement — render `reason: (mcp tool — no reason captured)` in the HITL approval line. JAC-authored MCP tools (if we ever expose Gru-side tools over MCP) still carry `reason:`.
- MCP tool outputs flow through `SummarizingToolset`, so large responses get post-processor treatment automatically — no special-casing.
- Slash surface: `/mcp list` / `/mcp reload` / per-server enable/disable in YAML.

## Phase G — Plan Mode (D23 promoted, now post-MCP)

Plan Mode was v2; the reframe pulled it forward because the main agent benefits from planning *before* spawning sub-agents. A plan step like "explore A/B/C and decide" is a natural sub-agent boundary. Demoted from old Phase F to follow MCP — see Phase F rationale.

Implementation per D23: structural toolset swap (read-only + `write_plan`); bundled `plan`→`tasks` rename moves with it. Builds the `ModeCapability` base that YOLO (still v2) will reuse.

## Phase H — A2A 4.e + broader tests

Lower priority but still planned. (Old Phase G minus MCP, which was promoted out.)

- **A2A Phase 4.e:** `OidcAuth` (discovery from `.well-known/openid-configuration`) and `GcpIdTokenAuth` (via `google-auth`, behind `jac[gcp]` optional dep). Strategy classes + `make_strategy` dispatch branches. User-guide examples for Azure / GCP / Okta peers.
- **Broader test coverage:** Phase 1 core (session, fs/shell bus), memory (`remember`/`forget`), slash edge cases.

## Evaluation loop (Phase 7 stream, added 2026-05-27)

Not a phase of its own — a stream under Phase 7 Quality. The idea: every Logfire span already carries D8's schema (`template`, `task_id`, `parent_run_id`, `token_cost`, `duration`, `exit_status`). That makes span replay a natural eval surface: assertions about *what the agent actually did* rather than *what it returned*.

First trajectory targets (chosen because each is a feature whose correctness is hard to verify with unit tests alone):

1. **Approval flow** — given a tool call with `risk: high`, the span chain must include an approval event before the tool span starts. Denied → no tool span.
2. **Compaction trigger** — given a synthesized 70%-budget history, exactly one compaction span fires with the correct `from_token_count` / `to_token_count` attributes; compacted slice file is written.
3. **Summarization savings** — for a tool whose output exceeds threshold AND a small tier is cheaper, the span carries `original_tokens > summary_tokens` and the `[AI-summarized via …]` tag is present in the returned tool message.
4. **Memory write audit** — `remember(scope=project)` outside a git repo raises; inside a repo, writes the file AND emits a span with the audit comment.
5. **Sub-agent delegation** — `spawn_sub_agent(tier=small)` resolves to the configured small model when present, cascades up when missing; spawn span has correct `parent_run_id`; depth-1 cap structurally enforced (sub-agent toolset has no `spawn_sub_agent`).
6. **Skill body injection** — `/skill use NAME` results in a turn whose user message equals the skill body verbatim.

Mechanism: `tests/eval/` directory, `just eval` recipe (separate from `just check` because eval runs are slower and may need live providers — but the first cut can stub providers and only assert span shape). Likely uses Logfire's testing capture (`CaptureLogfire`-style) to pull spans without exporting.

Tracked in `progress.md` under Phase 7. No new phase number because trajectory eval is ongoing rather than time-boxed.

## Harness alignment — what we reuse vs build (added 2026-05-27)

The Pydantic AI Harness ([README capability matrix](https://github.com/pydantic/pydantic-ai-harness)) overlaps substantially with JAC's roadmap. Most of the overlap is *PR-tracked* in Harness, not stable release — but the upstream gravity is real and we should track it deliberately rather than discover it after building something twice.

**Capabilities we ship today that Harness covers via WIP PR:**

| JAC capability | Harness PR | Decision |
| --- | --- | --- |
| Token-aware compaction (D20) | #191 (Sliding Window / Context Compaction) | **Keep ours.** Our compaction is wired to D25 budgets and `/tokens` UX; ripping it out for a PR-stage component is a regression risk for no user benefit. Revisit when #191 is merged + stable. |
| Tool result post-processor (D38) | #185 (Tool Output Management) | **Keep ours.** Same reasoning. Our opt-in decorator + tier-pricing gate is already shipped and tested. |
| Sub-agent (D35, Phase B) | #178 (Sub-agents) | **Keep ours.** Our packet schema, tier cascade, HITL flow, and depth cap are integrated with JAC's approval + usage tracking. Harness API is unknown until merge. |
| Skills (D21, Phase D) | #183 (Skills) | **Keep ours.** Anthropic community format is the public contract; Harness's surface is implementation detail. Revisit if Harness ships the same format. |
| Cost/token budgets (D25) | #182 (Cost/Token Budgets) | **Keep ours.** Integrated with our session/project rollup; would need to re-wire `/tokens` and `usage.jsonl` to migrate. |
| Approval workflows (D2) | #173 (Approval Workflows) | **Keep ours.** Uses Pydantic AI's `ApprovalRequiredToolset` underneath already; our value-add is the event-bus + renderer integration. |
| AGENTS.md auto-load (D11) | #175 (Repo Context Injection) | **Keep ours.** Same reasoning. |
| Plan / tasks (D15 → Phase G D23) | #180 (Planning) | **Watch.** Plan Mode hasn't shipped in JAC yet. If Harness #180 lands a clean API before we start Phase G, evaluate migration before building. |
| Session persistence (D3) | #176 | **Keep ours.** Filesystem layout is part of JAC's user contract. |
| Memory (D14) | #179 | **Keep ours.** Same reasoning — `memory.md` is the user-visible file. |
| Stuck-loop (v2) | #186 | **Defer decision** — we haven't built this yet; if Harness ships first, prefer adopting. |

**Capabilities we explicitly DON'T reinvent — adopt directly:**

- **`pydantic-monty`** — the Rust-written minimal Python interpreter that backs CodeMode in Harness. We adopt the lower layer (`pydantic_monty.Monty`) directly via a thin `MontyShellCapability`, NOT the higher `CodeExecutionToolset` wrapper from `pydantic-ai-harness`. Rationale: CodeMode's wrapper forces a "write code instead of call tools" execution model, which is the wrong default for JAC's HITL-per-tool UX. Using Monty directly lets us route specific risky tools through the sandbox without remodelling the agent loop. See D43.
- **`pydantic-ai-harness` capability primitives** that don't overlap with what we've already built — likely candidates are `Verification Loop` (#169), `Tool Error Recovery` (#171), `Secret Masking` (#172), and `Adaptive Reasoning` (#174). These are evaluated case-by-case as Harness PRs merge.
- **`pydantic-ai` core** itself — we never reinvent `ApprovalRequiredToolset`, `deferred_tool_calls`, `ProcessHistory`, `Instrumentation`, `ModelMessagesTypeAdapter`, or `pydantic_ai.direct.model_request`. This is already CLAUDE.md policy; restated here for clarity.

**The "what's the point of reinventing the wheel" answer in one paragraph:** JAC's organising thesis (cost-efficiency: D34) and its protocol surface (A2A-first: D24) ARE the differentiation. Capabilities that map cleanly onto that thesis (post-processor, sub-agents, skills, budgets) are worth owning because they're tightly coupled to our UX and instrumentation. Capabilities that are infrastructure with no UX (Monty for sandboxing; pydantic-ai's toolset/agent/instrumentation primitives) we adopt directly. Capabilities still in Harness PR limbo we re-evaluate at merge time — they're tracked here so the reconsideration is deliberate, not accidental.

## Three open gaps tracked elsewhere

These are flagged under `architecture.md` §5 "Still open":

1. **Sub-agent file system semantics** — Phase B grooming sub-item. Current lean: same toolset as main, same filesystem view, HITL per-tool. File writes still go through approval the same way the main agent's do.
2. **Logfire UX for nested traces** — depth cap of 1 (D40) bounds the chain in v1. Richer nested rendering is a future concern.

## v2 ⏸

Updated 2026-05-27 with the Monty isolation decision (D43) and the Harness reuse list above:

- YOLO mode + **sandboxing via direct `pydantic-monty` (D43)** — embedded Rust interpreter; microsecond cold start; zero-grant default (no fs/net/env until we register external functions). NOT `sandbox-exec` / `bwrap` (OS-specific, leaks host details), NOT Docker (network call + cold-start seconds + external dep), NOT `CodeExecutionToolset` from `pydantic-ai-harness` (wraps Monty but imposes a "write code, don't call tools" model that conflicts with JAC's per-tool HITL UX). Implementation sketch: `MontyShellCapability` that opt-in routes `run_shell` (and later mutating filesystem tools) through `pydantic_monty.Monty` with our existing toolset registered as external functions. Git-Clean Guard still required before YOLO entry. Uses `ModeCapability`'s `approval_override` knob from Phase G.
- Stuck-loop detection (defer decision pending Harness #186).
- Night Shift / cron scheduling.
- User-tier memory + predict-calibrate extraction.
- Browser / API / SDK surfaces.
