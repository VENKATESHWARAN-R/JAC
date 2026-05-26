# JAC — Implementation Progress

> **Just Another Companion/CLI** · **Updated:** 2026-05-26 · keep this in sync as work lands.

This file is the **live progress dashboard**: what is shipped, what is active, what should happen next. Short enough for an agent to read at the start of every task.

For deeper context:

- Product thesis: [`architecture.md`](architecture.md) §0
- Active design spec: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md)
- Product scope: [`idea.md`](idea.md)
- Locked decisions: [`architecture.md`](architecture.md) §5
- Completed-phase history: [`progress-history.md`](progress-history.md)
- Detailed A2A phase log: [`progress-a2a.md`](progress-a2a.md)
- Old roadmap (pre-2026-05-26 reframe): [`progress-archive-2026-05.md`](progress-archive-2026-05.md)
- Extended queued/future context: [`progress-roadmap.md`](progress-roadmap.md)

## Agent Start Here

- **Roadmap was reframed on 2026-05-26 around the cost-efficiency thesis.** Old Phase 3 (Skills with `mode: minion`), Phase 5 (Minion runtime), and Phase 6 (MCP) were archived to [`progress-archive-2026-05.md`](progress-archive-2026-05.md). Read [`architecture.md`](architecture.md) §0 and [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) before touching anything in Phases A–G.
- **Current active work:** Phase A — Context-cost foundation (tool result post-processor + cache-friendly prompt assembly + `/tokens` breakdown).
- **Nearest follow-up after A:** Phase B — `spawn_sub_agent` tool.
- **Terminology change:** "Minion" is retired. New name: **sub-agent**. If you touch old "minion" references in unrelated changes, rename in the same commit.
- **A2A is feature-complete** for its v1 scope. Phase 4.e (OIDC/GCP) was demoted to Phase G; not urgent.
- **Do not build yet without grooming:** v2 YOLO/sandboxing, CodeMode, stuck-loop, Night Shift.
- **When design is ambiguous:** `architecture.md` is the source of truth for *how*; `idea.md` for *what JAC is and is not*; this file for *current state*.

## Status Summary

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 0 — Skeleton | ✅ Complete | bare CLI + Gru, Logfire wired |
| Phase 0.5 — Config foundation | ✅ Complete | workspace, layered config, AGENTS.md, `jac init` |
| Phase 1 — Solo Gru | ✅ Complete | event bus, tools, HITL, session persistence + resume |
| Phase 1.5 — Profiles & secrets | ✅ Complete | multi-profile config, keyring/dotenv/env-only backends |
| Phase 2a — `remember` tool | ✅ Complete | HITL-gated project memory |
| Phase 2a.1 — User scope + `forget` | ✅ Complete | user/project memory scopes, audit trail |
| Phase 1.6 — Tool surface polish | ✅ Complete | plan, processes, fs/grep upgrades, web search, clarify |
| Phase 1.7 — Coworker experience | ✅ Complete | compaction, status bar, slash commands, budgets, feedback, plan persistence, web backends |
| Phase 4 — A2A | ✅ Complete (v1 scope) | inbound + outbound + file transfer + demo peer + hotfixes. Phase 4.e (OIDC/GCP) demoted to Phase G. |
| **Phase A — Context-cost foundation** | 🚧 Active | Tool result post-processor (D38) + cache-friendly prompt assembly + `/tokens` breakdown |
| Phase B — Sub-agent tool | ⏸ Next | `spawn_sub_agent` (D35), task packet (D36), tier-HITL (D39), depth cap = 1 (D40) |
| Phase C — Deterministic hooks | ⏸ Queued | Per-spawn callables (D37); retry budget 3 |
| Phase D — Skill loader | ⏸ Queued | Anthropic community format, no `mode: minion` (D21 revised) |
| Phase E — Parallel + bidirectional | ⏸ Future | Parallel spawn + D41 bidirectional comms feature flag |
| Phase F — Plan Mode | ⏸ Future | Pulled forward from v2 (D23 promoted) |
| Phase G — A2A 4.e + MCP + tests | ⏸ Future | OIDC/GCP A2A auth; MCP loader; broader test coverage |
| v0.2 source restructuring | ✅ Complete | released as v0.2.0 |
| v2 | ⏸ Future | YOLO + Monty + sandbox + Stuck-loop + Night Shift + user-tier predict-calibrate memory |

---

## Current Focus — Phase A: Context-cost foundation 🚧

**Goal:** stop wasting tokens on raw tool output and prompt-cache misses *before* introducing any sub-agents. Pure plumbing. Likely the single biggest cost reduction available today (estimated 30-50% on long sessions). Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §3.

### A.1 Tool result post-processor (D38)

- [ ] `jac.runtime.tool_summarize` module — `maybe_summarize_tool_result()` async wrapper
- [ ] Tokenizer for input sizing (provider-agnostic — use a simple heuristic, refine later)
- [ ] Wire wrapper into `FunctionToolset.call_tool` boundary for **both** Gru and (future) sub-agents
- [ ] Settings additions: `cost.tool_result_threshold_tokens` (default 8000), `cost.no_summarize_tools: []`, `cost.summarize_prompt_template`
- [ ] Tier-pricing read from `providers.yaml`; only summarize when small tier is strictly cheaper per output token than current tier
- [ ] Disk cache at `<project>/.agents/cache/tool-results/<run-id>/<call-id>.txt`; agent can `read_file` to retrieve full output
- [ ] Tagged return format: `[AI-summarized via <model>: original NNN tokens — full output at <path>]\n\n<summary>`
- [ ] Unit tests: above threshold → summarize; below → passthrough; no small tier → passthrough; not cheaper → passthrough; opted-out → passthrough; cache file written + re-readable
- [ ] User-guide page: `docs/user-guide/cost-controls.md` (new) — when summarization kicks in, how to opt out, where originals live

### A.2 Cache-friendly prompt assembly audit (L4)

- [ ] Read `Gru.build_instructions()` + `jac.workspace.context` end-to-end
- [ ] Confirm/enforce order: stable header → slowly-changing (AGENTS.md + memory.md) → per-turn changing (history + user prompt)
- [ ] Strip any time-of-day / session-id / random strings from the cached prefix
- [ ] Audit `get_instructions()` on every Capability for hidden per-turn changes
- [ ] Add an inline comment in `build_instructions()` marking the cache boundary
- [ ] Smoke test: two consecutive identical requests show cache-hit metrics > 0 (Anthropic returns cache stats in usage)

### A.3 `/tokens` breakdown improvements

- [ ] Add `tool_summarize` line when small-tier calls have run this session
- [ ] Add placeholder for `sub_agents` line (filled in Phase B; show only when count > 0)
- [ ] Show cache hit rate when the provider returns it
- [ ] Update `test_usage.py` with the new lines

### A.4 Documentation

- [ ] `docs/user-guide/cost-controls.md` — new page covering: tool result thresholds, opt-outs, the disk cache, what the AI-summarized tag means
- [ ] `docs/user-guide/configuration.md` — add `cost.*` block to the schema
- [ ] Cross-link from `docs/user-guide/getting-started.md`
- [ ] `progress.md` checkbox flip + one-line note per item as work lands

---

## Next Up — Phase B: Sub-agent tool ⏸

**Goal:** introduce delegation as a single tool the main agent calls. Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §4.

- [ ] `SubAgentTaskPacket` Pydantic model (D36): `objective`, `success_criteria`, `relevant_paths`, `forbidden_actions`, `expected_output`, `allowed_tools`, `hooks`, `max_turns`
- [ ] `Hook` and `HookResult` Pydantic models (stub for Phase C — accept empty list in B)
- [ ] `SubAgentResult` Pydantic model
- [ ] `SubAgentCapability` — builds sub-agent `Agent` instances with: tier-resolved model, allowlisted toolset (default = main toolset minus `spawn_sub_agent`), short system prompt rendering the packet
- [ ] `@jac_tool(approval_required=True) spawn_sub_agent(reason, task_summary, tier, task_packet)` — wires the capability + the tool result post-processor (A.1)
- [ ] HITL approval renders: reason, task_summary, resolved tier (with cascade note), tool allowlist, max_turns, hook count (D39)
- [ ] Tier cascade in `profiles`: missing tier resolves up; approval line includes cascade note
- [ ] Counter-tier deny flow via D26's `denied_with_feedback("retry tier=large")` (D42)
- [ ] `UsageTracker.add_sub_agent(in, out, tier)` — counts toward `session_total`; JSONL row `kind: sub_agent, tier: X`
- [ ] `/tokens` shows sub-agent line
- [ ] Logfire span: `tier`, `objective` (≤100 chars), `turns_used`, `hook_failures`, `exit_status`
- [ ] Depth cap = 1: sub-agent toolset constructed without `spawn_sub_agent` (D40), enforced structurally
- [ ] Tests: spawn approval, deny, counter-tier, tier cascade, depth cap, budget rollup, failure modes
- [ ] Reference example: `examples/sub-agent-summarize/` — main agent reads big file via sub-agent, returns 3-paragraph summary
- [ ] `gru_system.md` prompt update: when to spawn, heuristic threshold (>20k intermediate tokens), examples

---

## Queued — Phase C: Deterministic hooks ⏸

**Goal:** post-flight validators that skip the next LLM turn when everything's already correct. Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §5.

- [ ] `Hook` Protocol: `name`, `kind ∈ {python, shell}`, `target`; returns `HookResult(ok, output)`
- [ ] Built-in hooks: `ruff_check`, `pytest_run`, `ty_check` (in `jac.hooks.*`)
- [ ] `run_hook()` runner: python = import + call; shell = subprocess with 8KB stdout/stderr cap
- [ ] Retry budget = 3 hard-coded (D37) — failure routes back to *same* sub-agent's loop with `"hook X failed:\n<output>\n\nFix and respond again."`
- [ ] `hook_failures` count surfaced in `SubAgentResult` + Logfire span
- [ ] Tests: all-pass returns verbatim with no extra turn; one-fail retries; budget exhaustion returns `hooks_exhausted`
- [ ] Reference example extending Phase B's: add a `pytest` hook so the sub-agent writes test code, hook verifies it passes, no extra LLM turn

---

## Queued — Phase D: Skill loader ⏸

**Goal:** Anthropic community-format skills as loadable prompts. Skills are *advice the main agent reads when relevant*, not a runtime mode. Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §6.

- [ ] Frontmatter Pydantic model: `name`, `description`, optional `tools_required`
- [ ] Loader walks `~/.jac/skills/` and `<repo>/.agents/skills/`; project shadows user
- [ ] `SkillsCapability.get_instructions()` — publishes `name + description` for every skill; total ≤ 2 KB cap, truncates with a note if exceeded
- [ ] `load_skill(reason, name)` tool — returns skill body as a user message in the next turn
- [ ] `/skill use NAME` slash for explicit invocation
- [ ] `/skill list` slash — shows installed skills + trigger conditions
- [ ] Ship reference skills in `src/jac/data/skills/`: `code-review`, `summarize-large-files`, `verify-change`
- [ ] AgentCard integration: loaded skills auto-appear in A2A's `skills:` list (was Phase 4.1 in old roadmap)
- [ ] User-guide page: `docs/user-guide/skills.md`

---

## Queued — Phase E: Parallel sub-agents + bidirectional comms ⏸

- [ ] `spawn_sub_agents([packet1, packet2, ...])` — single tool, list of packets
- [ ] Batched HITL approval: `Approve N spawns? [a]ll [d]eny [r]eview each`
- [ ] `asyncio.gather` with HITL serialization (only one prompt visible at a time)
- [ ] Logfire parallel branches under one parent span
- [ ] **Bidirectional comms (D41) — behind feature flag** `settings.cost.sub_agent_bidirectional` (default `false`): `ask_main_agent(reason, question, context)` tool registered in sub-agent toolset only when flag is on; 5-round-trip cap; renderer markers `[sub-agent → main]` / `[main → sub-agent]`
- [ ] Validation: ship the flag off; flip on after manual testing demonstrates no UX regressions

---

## Queued — Phase F: Plan Mode (D23) ⏸

**Goal:** structural toolset swap pulled forward from v2 — now valuable because the main agent benefits from planning *before* spawning sub-agents.

- [ ] `ModeCapability` base — `filter_capabilities()` + `approval_override()` knobs
- [ ] Plan Mode toggle (slash `/plan-mode`); read-only subset + `write_plan` tool
- [ ] Bundled `plan` → `tasks` rename (tools, capability, events, session-file)
- [ ] YOLO mode left for v2 — `ModeCapability` is exercised by Plan Mode first

---

## Queued — Phase G: A2A 4.e + MCP + tests ⏸

- [ ] **A2A 4.e:** `OidcAuth` config (issuer + client_id + client_secret + scope, with `.well-known` discovery); `GcpIdTokenAuth` config (audience via `google-auth`); add `jac[gcp]` optional dep; user docs for Azure/GCP/Okta peers
- [ ] **MCP loader (D28):** `~/.jac/mcp.yaml` schema + loader; `MCPServerStdio` / `MCPServerHTTP` wiring; `/mcp list` and `/mcp reload` slash commands; per-server enable/disable
- [ ] **Broader test coverage:** Phase 1 core (session, fs/shell bus), memory (`remember`/`forget`), slash edge cases

---

## Ongoing — Phase 7 Quality 🚧

- [x] Provider registry tests
- [x] Phase 1.7 capability tests
- [x] Ruff + ty config; `just check`
- [x] User docs on Zensical
- [ ] Broader test suite (rolled into Phase G)
- [x] CodeMode integration deferred to v2 (no concrete pain yet)
- [x] Stuck-loop detection deferred to v2 (low value in HITL)

---

## Future — v2 ⏸

- [ ] YOLO mode + sandboxing with Monty + `sandbox-exec` / `bwrap` + Git-Clean Guard (uses `ModeCapability`'s `approval_override` knob from Phase F)
- [ ] CodeMode integration (`pydantic-ai-harness`)
- [ ] Stuck-loop detection
- [ ] Night Shift / cron scheduling
- [ ] User-tier memory + predict-calibrate extraction (the `~/.jac/memory.md` file exists; automatic extraction deferred)
- [ ] Browser / API / SDK surfaces

---

## How to Use This File

- When you start a task, change `- [ ]` to `- [~]` (in flight) in this dashboard.
- When you finish, `- [x]` and a one-line note if anything deviated from the plan.
- When a new task surfaces, add it to the relevant phase or v2 — do not let it float.
- Architectural decisions go in `architecture.md` §5, not here. This file is *what*, not *why*.
- Keep this file short. Detailed landed-phase notes live in `progress-history.md`, detailed A2A notes in `progress-a2a.md`, extended future context in `progress-roadmap.md`, archived pre-reframe entries in `progress-archive-2026-05.md`.
