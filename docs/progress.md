# JAC — Implementation Progress

> **Just Another Companion/CLI** · **Updated:** 2026-05-27 · keep this in sync as work lands.

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
- **Current active work:** Phases A + B both ✅ **complete and tagged v0.3.0** (2026-05-27). Next up: **Phase C — Deterministic hooks** (post-flight validators for sub-agents). Read `design/cost-efficient-orchestration.md` §5; the `HookSpec` / `HookResult` Pydantic models already exist in `jac.runtime.sub_agent` so the surface is locked.
- **Nearest follow-up after A:** Phase B — `spawn_sub_agent` tool.
- **Phase D — Skill loader: ✅ landed 2026-05-27** on the `phase-d-skill-loader` branch (developed independently). Loader + capability + `/skill list|use|reload` slash + 3 reference skills + A2A AgentCard integration all in.
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
| **Phase A — Context-cost foundation** | ✅ Complete (v0.3.0) | A.1 post-processor + A.2 prompt-cache fix + A.3 `/tokens` breakdown all landed 2026-05-27. Biggest single-session cost win shipped. |
| Phase B — Sub-agent tool | ✅ Complete (v0.3.0) | `spawn_sub_agent`, packet model, tier cascade (small→medium→large, never down), depth cap = 1 structural, HITL via existing approval flow, UsageTracker.add_sub_agent + JSONL `kind=sub_agent:<tier>`, `/tokens` line. Hooks shape locked, runner stubbed (Phase C). |
| Phase C — Deterministic hooks | ⏸ Queued | Per-spawn callables (D37); retry budget 3 |
| **Phase D — Skill loader** | ✅ Complete | Loader walks project/user/package; 2 KB prompt cap with name-only fallback; `load_skill` tool; `/skill list|use|reload`; 3 reference skills (`code-review`, `summarize-large-files`, `verify-change`); A2A AgentCard publishes loaded skills as `jac-skill-<name>` entries. |
| Phase E — Parallel + bidirectional | ⏸ Future | Parallel spawn + D41 bidirectional comms feature flag |
| Phase F — Plan Mode | ⏸ Future | Pulled forward from v2 (D23 promoted) |
| Phase G — A2A 4.e + MCP + tests | ⏸ Future | OIDC/GCP A2A auth; MCP loader; broader test coverage |
| v0.2 source restructuring | ✅ Complete | released as v0.2.0 |
| v2 | ⏸ Future | YOLO + Monty + sandbox + Stuck-loop + Night Shift + user-tier predict-calibrate memory |

---

## Current Focus — Phase A: Context-cost foundation 🚧

**Goal:** stop wasting tokens on raw tool output and prompt-cache misses *before* introducing any sub-agents. Pure plumbing. Likely the single biggest cost reduction available today (estimated 30-50% on long sessions). Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §3.

### A.1 Tool result post-processor (D38)

- [x] `jac.runtime.tool_summarize` module — `maybe_summarize_tool_result()` async (uses `pydantic_ai.direct.model_request`)
- [x] Tokenizer for input sizing — chars/4 heuristic (looser than compaction's 3; this is a should-summarize gate)
- [x] Wire wrapper into `FunctionToolset.call_tool` boundary via `SummarizingToolset` wrapper in `jac.tools.toolset`. Layering: `ApprovalRequired → Summarizing → Function`. Same wrapper covers Gru today + future sub-agents.
- [x] Settings additions: `cost.tool_result_threshold_tokens` (8000), `cost.no_summarize_tools: []`, `cost.summarize_tools: []`, `cost.summarize_prompt_template`
- [x] Tier-pricing in `providers.yaml` (Anthropic/OpenAI/Google seeded); `ProviderRegistry.get_pricing()`; gate is `is_strictly_cheaper(small, current)` — both must be priced, equal price returns False
- [x] **Opt-in via decorator instead of opt-out**: `@jac_tool(summarizable=True)` (bare `@jac_tool` still works, defaults to False). Default-summarizable tools: `run_shell`, `web_search`, `fetch_url`. `read_file` / `list_dir` / `grep` / memory / plan tools never opted in (preserves line numbers + exact content).
- [x] `run_shell` 10KB hard-truncate removed — summarizer now handles large output instead of throwing it away.
- [x] Disk cache at `<project>/.agents/cache/tool-results/<session-id>/<call-id>.txt`; agent re-reads via existing `read_file`
- [x] Tagged return format: `[AI-summarized via <model> — original NNN tokens, full output at <path>]\n\n<summary>`
- [x] Tests (15): above threshold → summarize; below → passthrough; no summarizer model → passthrough; not cheaper / equal → passthrough; decorator-off → passthrough; force-on override; summarizer failure → fallback; non-string output JSON-serialized for sizing; toolset wrapper smoke; stats accumulate
- [x] User-guide page: `docs/user-guide/cost-controls.md` + nav entry in `zensical.toml`

### A.2 Cache-friendly prompt assembly audit (L4)

- [x] Read `jac.workspace.context` + `ContextCapability.get_instructions` end-to-end. **Only `ContextCapability` contributes instructions** — every other capability returns the default (none). `build_gru` passes no agent-level `instructions=`. So the audit surface is one function.
- [x] Order confirmed: stable header (`gru_system.md` body, loaded once at construction) → slowly-changing (date + AGENTS.md + memory.md, re-read per turn) → per-turn (history + user prompt, owned by pydantic-ai).
- [x] **Critical fix: dropped time-of-day from the system prompt.** `format_session_datetime()` used to emit `Tuesday, May 27, 2026 at 3:42:18 PM (PDT)` — second-precision → cached prefix changed every single turn → Anthropic prompt cache never hit. Now emits day-granularity only (`Wednesday, May 27, 2026`); one miss per midnight rollover instead of one per turn.
- [x] Other `datetime.now()` call sites audited: `memory.py:283`, `history.py:133`, `audit.py:123`, `storage.py:154` — all write timestamps to *stored data* (memory entries, compaction markers, audit log), none touch the cached system prompt. Correct as-is.
- [x] `gru_system.md` + `load_prompt()` confirmed static (no templating).
- [x] Inline `=== Prompt cache boundary ===` comment added in `ContextCapability.get_instructions` documenting the static-vs-dynamic split and what must NOT be added to the instructions slot.
- [x] Smoke tests (3, `tests/test_prompt_cache_stability.py`): regression guard against the second-precision clock; assertion that two back-to-back instruction callable invocations return byte-identical output; assertion that memory.md changes DO invalidate (we haven't over-cached).

### A.3 `/tokens` breakdown improvements

- [x] Add `summarize:` line with calls / original / summary / saved / small-tier in+out — only shown when activity > 0 this session
- [x] Show `cache:` line with read / write / hit-rate when the provider populates `RunUsage.cache_read_tokens` / `cache_write_tokens` (Anthropic does). `UsageTracker.record()` now accepts both as kwargs; REPL passes through from `result.usage`.
- [ ] Placeholder for `sub_agents` line — deferred to Phase B (no point adding a dead line)
- [x] Tests in `test_budget_slash.py`: cache visible when populated, hidden when silent; summarize line visible after activity

### A.4 Documentation

- [x] `docs/user-guide/cost-controls.md` — new page covering tool result thresholds, opt-in/opt-out, the disk cache, the AI-summarized tag, where to read the originals
- [x] `docs/user-guide/configuration.md` — `cost.*` block documented in its own section
- [x] Cross-linked from `docs/user-guide/getting-started.md` → Next steps table
- [x] `zensical.toml` nav — new entry "Cost controls" between "Configuration" and "Sessions & memory"
- [x] `progress.md` flipped + landed notes

---

## Phase B — Sub-agent tool ✅ (v0.3.0)

**Goal:** introduce delegation as a single tool the main agent calls. Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §4.

- [x] `SubAgentTaskPacket` Pydantic model in `jac.runtime.sub_agent` — objective, success_criteria, relevant_paths, forbidden_actions, expected_output, allowed_tools, hooks, max_turns
- [x] `HookSpec` + `HookResult` Pydantic models (surface locked; runner stubbed for Phase C; tool accepts empty list)
- [x] `SubAgentResult` Pydantic model — output, turns_used, resolved_tier, resolved_model, hook_failures, exit_status
- [x] `SubAgentCapability` (factory pattern) — builds sub-agent Agent on demand with tier-resolved model + capability-list factory. Registered via module-level setter `set_sub_agent_capability`, mirroring `set_summarizer_model`.
- [x] `@jac_tool(summarizable=True) spawn_sub_agent(reason, task_summary, tier, task_packet)` — wired through `SubAgentToolCapability` which marks it approval-required (existing HITL handler shows reason / task_summary / tier / packet)
- [x] Tier cascade — `resolve_tier(profile, requested)` walks `small → medium → large` (never down). Cascade note appears in result header (`cascaded up to 'medium'`).
- [x] `UsageTracker.add_sub_agent(in, out, tier)` — counts toward `session_total`; JSONL row tagged `kind: sub_agent:<tier>`. Recorder pattern (`sub_agent_usage.set_sub_agent_usage_recorder`) bridges the sub-agent module to the REPL's tracker without an import cycle.
- [x] `/tokens` shows `sub_agents:` line with spawns / input / output / total + per-tier breakdown when count > 0.
- [x] Logfire span `spawn_sub_agent` with `tier`, `requested_tier`, `cascaded`, `model`, `objective` (≤100 chars), `max_turns`, `allowed_tools`, `hook_count`, `turns_used`, `exit_status`, `hook_failures`.
- [x] Depth cap = 1 — `sub_agent_capabilities()` excludes `SubAgentToolCapability`; verified by test. Recursion is structurally impossible.
- [x] Tests (17, `test_sub_agent.py`): tier cascade up + never down + unknown tier + no-tier-available; depth cap structural; spawn_sub_agent is jac_tool + summarizable; packet rendering (every section + skip-empties); add_sub_agent bumps counters + writes tagged JSONL; fail-fast when no capability; happy path tagged output + stats; cascade visible in header; error path returns error result; recorder forwarding; /tokens line.
- [x] `gru_system.md` updated — new "When to call `spawn_sub_agent`" section (spawn criteria, tier guidance, packet schema, depth cap explanation).
- [ ] Counter-tier deny flow via D26's `denied_with_feedback("retry tier=large")` (D42) — **deferred to Phase E** (multi-spawn polish phase).
- [ ] Reference example `examples/sub-agent-summarize/` — **deferred** (not blocking; the test suite + cost-controls.md cover the surface).

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

## Landed — Phase D: Skill loader ✅

**Goal:** Anthropic community-format skills as loadable prompts. Skills are *advice the main agent reads when relevant*, not a runtime mode. Design: [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) §6. Landed 2026-05-27 on `phase-d-skill-loader`.

- [x] `SkillFrontmatter` Pydantic model: `name`, `description`, optional `tools_required` (informational only — never gates loading)
- [x] Loader walks **three** locations — project > user > **package** — with the package source carrying shipped reference skills. **Every source contributes**: only same-name collisions trigger shadowing, and shadowed entries are kept in the catalog (`SkillCatalog.shadowed`) so they're visible via `/skill list`, not silently dropped. Mismatched name/folder, empty body, invalid YAML, bad name regex all log + skip cleanly.
- [x] `SkillsCapability.get_instructions()` — publishes `name + description` for every active skill; re-rendered per request so `/skill reload` shows up without a Gru rebuild. 2 KB cap with name-only fallback when exceeded.
- [x] `load_skill(reason, name)` tool — returns skill body as the tool's string result (becomes a tool message in history). Unknown-name errors enumerate the available skills so the model can self-correct.
- [x] `/skill use NAME` — injects the body via a new `InjectUserText` slash result; REPL runs a real turn with the body as input.
- [x] `/skill list` — Rich tables: **active** (name / source colour-coded / description / required tools) plus, when any exist, **shadowed** (name / source / shadowed-by / path) so overrides aren't invisible.
- [x] `/skill reload` — re-scans on demand; reports added / removed / unchanged counts.
- [x] Reference skills in `src/jac/data/skills/`: `code-review`, `summarize-large-files`, `verify-change`. Each references JAC-specific patterns (`spawn_sub_agent`, hooks, `just check`).
- [x] AgentCard integration: loaded skills appended to A2A `skills:` list as `id: jac-skill-<name>`; generic skill remains as base advertisement. `skills_getter` on `A2ACapability` is a callable so `/skill reload` between server restarts is reflected on the next `/a2a serve`.
- [x] User-guide page: `docs/user-guide/skills.md` + nav entry in `zensical.toml`.
- [x] Tests (32 new in `tests/test_skills.py` + 5 new in `tests/test_a2a_card.py`): frontmatter validation, two-way + three-way shadowing (both surfaced via `SkillCatalog.shadowed`), distinct-name coexistence across all three sources, 2 KB cap behaviour, tool happy / unknown / no-skills-loaded, reload diff, slash list/use/reload + shadowed table, AgentCard publication.

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
