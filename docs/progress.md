# JAC — Implementation Progress

> **Just Another Companion/CLI** · **Updated:** 2026-05-28 · keep this in sync as work lands.

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
- **Current active work:** **Phase F — MCP loader (D28).** Phase E is fully shipped in v0.5.0. Next up: MCP loader, the ecosystem surface most users try first (promoted from old Phase G after 2026-05-27 review).
- **Released:** v0.3.0 (Phases A + B, 2026-05-27) · v0.4.0 (Phase D skill loader, 2026-05-27) · v0.5.0 (Phase E parallel + bidirectional, 2026-05-28) · v0.6.0 (workspace loose-mode, session management, memory slash commands, minion theme, 2026-05-29).
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
| Phase C — Deterministic hooks | 🚫 Dropped | Complexity didn't earn its keep; `success_criteria` + post-return `run_shell` covers the use case |
| **Phase D — Skill loader** | ✅ Complete (v0.4.0) | Loader walks project/user/package; 2 KB prompt cap with name-only fallback; `load_skill` tool; `/skill list|use|reload`; 3 reference skills (`code-review`, `summarize-large-files`, `verify-change`); A2A AgentCard publishes loaded skills as `jac-skill-<name>` entries. |
| **Phase E — Parallel + bidirectional** | ✅ Complete (v0.5.0) | `spawn_sub_agents` parallel fan-out; D41 bidirectional channel (on by default); `minion-N` spawn IDs; sub-agent HITL/skills/A2A parity; parallel approval table; per-spawn lifecycle events |
| **Phase F — MCP loader** | ⏸ Future | **Promoted from old Phase G (2026-05-27)** — MCP is the ecosystem surface most users try first; precedes Plan Mode |
| Phase G — Plan Mode | ⏸ Future | Pulled forward from v2 (D23 promoted); demoted from old Phase F to follow MCP |
| Phase H — A2A 4.e + broader tests | ⏸ Future | OIDC/GCP A2A auth; broader test coverage; eval-loop work tracked under Phase 7 |
| v0.2 source restructuring | ✅ Complete | released as v0.2.0 |
| v2 | ⏸ Future | YOLO + **direct `pydantic-monty` sandbox** (D43) + **ACP editor surface** (D45, condition-gated) + Stuck-loop + Night Shift + user-tier memory |

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

## Current Focus — Phase E: Parallel sub-agents + bidirectional comms 🚧

**Goal:** add `spawn_sub_agents` so the main agent can fan out N independent delegations under one HITL approval; then (separately, behind a flag) the D41 bidirectional channel.

### E.1 Parallel spawn ✅

- [x] `SubAgentSpawnSpec` Pydantic model (tier + optional label + task_packet) — `runtime/sub_agent.py`
- [x] `spawn_sub_agents(reason, task_summary, spawns)` tool — `@jac_tool(summarizable=True)`, registered alongside `spawn_sub_agent` in `SubAgentToolCapability` so depth cap = 1 is preserved structurally with no extra wiring
- [x] Per-spawn tier resolution (each spawn cascades independently); unresolvable tiers surface as `exit=error` in that spawn's block without killing siblings
- [x] `asyncio.gather` via `asyncio.create_task` — HITL multiplexing serializes automatically at the bus level (renderer reads the approval queue one event at a time)
- [x] Logfire outer span `spawn_sub_agents` with `count` / `requested_tiers` / `resolved_tiers` / `ok_count` / `error_count`; each `_run_sub_agent` still opens its own `spawn_sub_agent` child span so the parent chain is intact
- [x] Combined output: `[parallel spawn: N sub-agents]` header + `── spawn N (label): tier=X model=Y turns=N exit=ok ──` divider per block (label parens omitted when empty)
- [x] One HITL approval covers the whole batch (existing approval renderer dumps `spawns` arg generically; batched "review each" prompt is polish for later)
- [x] `gru_system.md` — new "When to call `spawn_sub_agents` (parallel)" section right after the single-spawn section
- [x] Tests (10 new in `tests/test_sub_agent.py`): is_jac_tool + summarizable, structural depth cap, fail-fast no capability, reject empty list, gather happy path preserves order, partial failure doesn't kill batch, per-spawn cascade, unresolvable tier per-spawn surface, JSONL row per spawn

### E.2 Bidirectional comms (D41) 🚧

- [x] `settings.cost.sub_agent_bidirectional` flag, defaults.yaml entry. **Default flipped to `true` on 2026-05-28** after the UX validation pass — the 5-round-trip cap bounds the worst-case cost and the clarifying-question flow earned its keep. Existing users inherit the new value automatically via layered-config fall-through; no migration needed. See CLAUDE.md "Changing config schema" for the policy this set the precedent for.
- [x] `ask_main_agent(reason, question, context)` tool — registered in sub-agent toolset only when flag is on AND a channel is bound. Reads its channel via a contextvar so capability factories stay plain functions.
- [x] `respond_to_sub_agent(reason, spawn_id, answer)` tool — registered on the main agent only when flag is on; **not** approval-gated (the parent spawn was already approved).
- [x] `SubAgentChannel` (per-spawn queues + round_trip counter) + `_pending_channels` registry keyed by 8-hex `spawn_id`. Cleaned up on completion AND on session-end via `_reset_pending_channels`.
- [x] 5-round-trip cap; **6th ask returns a graceful "finalize with what you have" directive** instead of erroring (per user refinement) — sub-agent always produces a coherent final answer.
- [x] Logfire: span attrs `bidirectional`, `ask_main_agent_count`; warning at 3 (`warn_threshold`) and at cap (`cap_reached`).
- [x] Renderer markers + Rich panels: dedicated `SubAgentSpawned` / `SubAgentQuestion` / `SubAgentAnswer` / `SubAgentCompleted` events on the bus, painted as Rich panels (blue spawn start with objective, yellow question, cyan answer, single-line green/red completed). `/spawns` slash command lists every parked channel. Status bar shows `spawns:N` segment when anything is in flight.
- [x] Conditional prompt addendum `gru_bidirectional.md` appended to `gru_system.md` only when flag is on (we never describe tools the agent doesn't have). New section in `sub_agent_system.md` covering `ask_main_agent` use, the cap, and the finalize directive.
- [x] Tests (21 new, all 48 sub-agent tests + 476 total green): capability wiring both flags, jac_tool surface, no-channel fail-fast, happy-path single round-trip, multi round-trip, cap → finalize directive, unknown / already-finished spawn_id error rendering, cancellation cleanup, round_trip counter behaviour, prompt addendum present/absent, lifecycle event order (Spawned → Question → Answer → Completed), sequential path emits no SubAgent* events, error path still emits Completed, `/spawns` empty + populated state, status bar segment visibility.
- [x] Manual UX validation pass (2026-05-28): parallel + bidirectional happy paths confirmed; surfaced the parallel-approval clutter (tracked in E.3) and the sub-agent-without-HITL safety gap (fixed in E.2.1 below).

### E.2.2 Human-readable spawn IDs + "who's asking" on approval

Surfaced during the same E.2 validation pass: the previous 8-hex spawn IDs (`a3f201b9`) were noise to the user, and the approval panel didn't tell you *which* agent (Gru? which sub-agent?) was requesting permission for a destructive tool. Both wins are tiny but compound over a session that fans out several sub-agents in parallel.

- [x] `secrets.token_hex(4)` → session-scoped monotonic counter producing `minion-1`, `minion-2`, … Resets on REPL teardown / `_reset_pending_channels()` so every new session starts at `minion-1`. Sequential + bidirectional + parallel paths all mint via the same `_mint_spawn_id()` helper.
- [x] `_current_agent_label` contextvar (default `"Gru"`) bound to the spawn_id inside `_run_sub_agent`. The shared approval handler reads it and stamps `agent_label` onto every `ApprovalRequest` event. Token reset in a `finally` so the sequential path doesn't leak the label back into Gru's context after the spawn returns.
- [x] `ApprovalRequest` gained an `agent_label: str = "Gru"` field.
- [x] CLI renderer's approval panel title now shows `approval needed · Gru` (dim) or `approval needed · minion-N` (blue, matching the spawn lifecycle panel colour) so the user can tell at a glance who is asking — important for parallel spawns and bidirectional sub-agents that may issue approvals across multiple turns.
- [x] `gru_bidirectional.md` prompt example updated to use the `minion-1` shape so the model's mental model matches what it sees.
- [x] Tests (+5): counter monotonicity, reset behaviour, default label is "Gru", inside-run label is the spawn_id (with contextvar reset confirmed), `ApprovalRequest` carries the label end-to-end through `make_approval_handler`.

### E.2.1 Sub-agent capability parity with main agent

- [x] `sub_agent_capabilities()` now accepts `hooks`, `approval`, `skills_capability`, `a2a_capability` kwargs. When supplied (REPL path), sub-agent destructive tool calls (`write_file`, `edit_file`, `delete_file`, `run_shell`, `remember`) route through the **same HITL approval handler** as the main agent — no walk-away on destructive ops until v2 YOLO/sandboxing lands.
- [x] Skills + A2A capabilities are shared instances (not duplicates), so `/skill reload` is observed by spawned sub-agents and the guest A2A server stays singular. Rationale: a sub-agent talking to a remote A2A peer (or following a loaded skill) keeps the (often large) response in the *sub-agent's* context — on-thesis for cost-efficient delegation.
- [x] REPL wraps `sub_agent_capabilities` in a closure capturing `hooks` / `approval` / `skills_capability` / `a2a_capability`; the `SubAgentCapability.capability_factory` signature stays `(allowed_tools, *, channel=None)` so existing call sites and tests continue to work.
- [x] `sub_agent_system.md` updated: prompt now states destructive tools require HITL approval and lists skills + A2A as available.
- [x] Tests: bare-factory (no kwargs) keeps the silent/unguarded behaviour for hermetic tests; with-kwargs path inserts hooks/approval/skills/a2a by identity; destructive tool capability types match between main and sub.
- [x] Validation: 2026-05-28 — flag flipped on by default after manual testing across happy paths showed no UX regressions. Opt-out via `cost.sub_agent_bidirectional: false` if a user prefers sub-agents to finalize without pausing.

### E.3 Polish (post-bidirectional)

- [x] **Parallel-spawn approval panel** (E.3a): `spawn_sub_agents` now renders a per-spawn summary table (`# / label / tier / one-line objective`) inside the approval panel instead of the generic key/value dump that previously truncated the nested `spawns` JSON to `+1 more`. Header shows count + reason + task_summary; pluralisation handled (`1 parallel sub-agent` vs `N parallel sub-agents`).
- [x] **Per-spawn lifecycle events in parallel path** (E.3b): each worker in `spawn_sub_agents` emits `SubAgentSpawned` at start and `SubAgentCompleted` on finish (mirroring the bidirectional path). User sees a blue `▶ minion-N` panel as each spawn launches and a green `✓ minion-N done · turns=N` line as each completes — no more "nothing visible until the whole gather lands".
- [ ] Batched approval line: `Approve N spawns? [a]ll [d]eny [r]eview each` — "review each" is structural work (would need to reshape the deferred tool call into N approvals at the pydantic-ai layer); skipped for this pass. The existing `y/n/r` prompt now applies to the summary view above.
- [ ] Counter-tier deny flow via D26's `denied_with_feedback("retry tier=large")` (D42) — carried over from Phase B
- [ ] Reference example `examples/sub-agent-summarize/` — carried over from Phase B

---

## Queued — Tool polish ⏸

Cross-cutting bucket for small enhancements to existing first-party tools — not big enough to warrant their own phase, valuable enough to track.

### Clarify tool — richer ask-the-user surface

Today `clarify(reason, question, options[2-8])` is single-select only. Surfaced during Phase E manual UX validation: this is too narrow for several real cases (multi-pick lists, "or describe in your own words", picking from a list with per-option context).

- [ ] `multi_select: bool = False` — when true, the renderer offers a checkbox-style picker and the tool returns a list of selected option strings (vs the current single string)
- [ ] `allow_custom: bool = False` — adds an "Other (describe)" affordance; when chosen the tool returns the user's free-text reply
- [ ] `option_descriptions: list[str] | None` — optional one-line context per option, rendered below the option label
- [ ] Keep the existing single-select / no-description signature working unchanged (defaults preserve current behaviour)
- [ ] Update the model's prompt-side guidance in `gru_system.md` so the agent knows when each new knob is appropriate
- [ ] Tests: each new knob (passthrough when off, expected behaviour when on, mixed combinations)

---

## Queued — Phase F: MCP loader (D28) ⏸

**Goal:** add the most-requested ecosystem surface before Plan Mode. **Promoted from old Phase G on 2026-05-27** after external review noted MCP is the protocol most users will try first; A2A's protocol-design work was the right early bet, but MCP now blocks daily-workflow integrations more than Plan Mode does.

- [ ] `~/.jac/mcp.yaml` + `<repo>/.agents/mcp.yaml` schema + loader (layered like other config; project shadows user)
- [ ] `MCPServerStdio` / `MCPServerHTTP` wiring as a JAC capability — tools published into Gru's toolset alongside local tools
- [ ] **`reason: str` exemption per D28** — render `reason: (mcp tool — no reason captured)` in HITL approval UI; JAC-authored MCP tools still carry `reason:`
- [ ] `/mcp list` / `/mcp reload` slash commands; per-server enable/disable knob in YAML
- [ ] Audit: MCP tool usage flows through the same `SummarizingToolset` wrapper as local tools (so large MCP outputs go through the post-processor)
- [ ] User-guide page: `docs/user-guide/mcp.md` + nav entry
- [ ] Tests: loader (valid/invalid YAML, layered shadowing, missing server), approval flow, reload diff, large-output summarization round-trip

---

## Queued — Phase G: Plan Mode (D23) ⏸

**Goal:** structural toolset swap pulled forward from v2 — now valuable because the main agent benefits from planning *before* spawning sub-agents. **Demoted from old Phase F to follow MCP** (2026-05-27): MCP delivers more daily-workflow value sooner; Plan Mode benefits from MCP tools being available when the plan executes.

- [ ] `ModeCapability` base — `filter_capabilities()` + `approval_override()` knobs
- [ ] Plan Mode toggle (slash `/plan-mode`); read-only subset + `write_plan` tool
- [ ] Bundled `plan` → `tasks` rename (tools, capability, events, session-file)
- [ ] YOLO mode left for v2 — `ModeCapability` is exercised by Plan Mode first

---

## Queued — Phase H: A2A 4.e + broader tests ⏸

- [ ] **A2A 4.e:** `OidcAuth` config (issuer + client_id + client_secret + scope, with `.well-known` discovery); `GcpIdTokenAuth` config (audience via `google-auth`); add `jac[gcp]` optional dep; user docs for Azure/GCP/Okta peers
- [ ] **Broader test coverage:** Phase 1 core (session, fs/shell bus), memory (`remember`/`forget`), slash edge cases

---

## Landed — Workspace / memory / session polish ✅ (2026-05-29)

A focused reliability + UX + test-coverage pass over the workspace, memory, and session subsystems (no new phase; tightening shipped surfaces).

- [x] **Atomic session save.** `Session.save()` now writes `messages.json` via tempfile + rename (matching memory's atomic write), so a kill mid-write can't truncate/corrupt the session. Regression tests assert no leftover `.tmp` and clean overwrite-on-repeat.
- [x] **`test_memory.py` (42 tests).** First coverage of `remember`/`forget` end to end (bootstrap, category + scope routing, de-dup case/whitespace/substring rules, size hint, validation/fail-first paths) plus the markdown parsing internals (`_extract_section`, `_insert_into_section`, `_find_all_matches`, `_remove_line`, `_find_duplicate`, `_strip_bullet_metadata`, `_count_bullets`) on their edge cases.
- [x] **`test_session.py` (17 tests).** First coverage of the message-history round-trip, atomicity, resume failure modes, and `list_ids`/`list_summaries`.
- [x] **`/memory` slash command.** Read-only view of stored entries (both scopes, or `/memory user|project`), audit comments stripped so the prose can be fed back to `forget`. Backed by a new read-only `memory.read_memory_entries(scope)` helper (project scope readable even outside a repo).
- [x] **Richer session listing.** `jac sessions` / `/sessions` now show message count + human-readable creation time per session (via new `Session.list_summaries()` / `SessionSummary`), not just bare ids. Malformed `messages.json` keeps the session listed with an `unreadable` count.
- [x] **Docs fix.** Memory audit-comment example in `sessions-and-memory.md` corrected to the dashes form (`YYYY-MM-DDTHH-MM-SS`) the code actually writes; regression test guards the no-colon invariant.
- [x] Docs updated in the same change: `cli-reference.md` + shipped `jac-cli/SKILL.md` (new `/memory` slash, richer listing), `sessions-and-memory.md` (atomic save, `/memory`, listing).

## Landed — Project detection + loose-mode workspace ✅ (2026-05-29)

Fixes the "`.agents/` scattered into random folders" problem and rounds out session/memory maintenance.

- [x] **Project detection now `.git` OR `.agents/`.** New `paths.project_root() → Path | None` (no CWD fallback), `paths.in_project()`, and `paths.project_state_root()`. `find_project_root()` stays the *working* root (CWD fallback) for tools; `is_in_project_repo()` renamed to `in_project()` (callers: memory, skills, skill slash).
- [x] **Loose-mode fallback.** When no project is found, state writers (sessions, `usage.jsonl`, tool-result cache, A2A) anchor to the user workspace `~/.jac/` via `project_state_root()` instead of creating `<cwd>/.agents/`. Overlay resources (config/memory/prompts/skills) stay project-only and are simply skipped when loose; project-scope `remember` still refuses outside a project.
- [x] **`jac init` opt-in + startup notice.** `bootstrap.init_project_workspace()` creates `.agents/` (clearing the root cache). `jac init` offers it in a non-project folder; the REPL greets with a `workspace: global` line when loose.
- [x] **Session delete + prune.** `Session.delete()` / `Session.prune_older_than()` + `parse_duration()` (`w`/`d`/`h`). CLI: `jac sessions` is now a sub-app (`list` / `delete <id>` / `prune --older-than <dur>`, `--yes` to skip confirm). In-REPL: `/sessions delete <id>` (refuses active session) and `/sessions prune <dur> [yes]` (previews without `yes`). Pruning leaves `usage.jsonl` intact (tokens were spent).
- [x] **User-driven `/remember` + `/forget`.** Deterministic slash edits to memory (no model call; the typed command is the approval). One-bullet audited writes, fixed schema — never a wholesale rewrite.
- [x] **`@jac_tool` overloads.** Added `@overload`s so a bare-decorated tool keeps its original type — lets `/remember`/`/forget` call `remember`/`forget` directly and still type-check.
- [x] **Tests.** New `test_workspace_paths.py` (13: detection, loose fallback, opt-in). `test_session.py` +delete/prune/`parse_duration`. `test_slash.py` +`/sessions delete|prune`, `/remember`, `/forget`. Autouse `_clear_root_caches` in conftest kills the `@cache` cross-test landmine; state-isolation fixtures migrated from patching `find_project_root` to `project_root`.
- [x] **Docs.** `sessions-and-memory.md` (project vs. global, delete/prune, editing memory yourself), `cli-reference.md`, `configuration.md`, shipped `jac-cli/SKILL.md`.

## Ongoing — Phase 7 Quality 🚧

- [x] Provider registry tests
- [x] Phase 1.7 capability tests
- [x] Ruff + ty config; `just check`
- [x] User docs on Zensical
- [ ] Broader test suite (rolled into Phase H)
- [x] Stuck-loop detection deferred to v2 (low value in HITL)
- [ ] **Evaluation loop on Logfire spans (D44, new 2026-05-27)** — trajectory tests that assert against span attributes already emitted per D8 (`template`, `task_id`, `parent_run_id`, `token_cost`, `duration`, `exit_status`). First targets: approval flow correctness, compaction trigger threshold, summarization savings (original vs summary tokens), memory write audit trail, sub-agent delegation (tier resolution + depth cap + parent chain), `/skill use` body injection. Run via `just eval` (new recipe), distinct from `just check` (fast unit tests). Builds on existing Logfire instrumentation rather than adding a separate test harness. Likely starts as `tests/eval/` directory with span-replay fixtures from a `logfire.testing.CaptureLogfire` style capture.

---

## Future — v2 ⏸

- [ ] YOLO mode + **sandboxing via direct `pydantic-monty` (D43)** — embedded Rust interpreter, microsecond cold start, zero-grant default. NOT via `pydantic-ai-harness`'s `CodeExecutionToolset` wrapper (which forces a "write code instead of call tools" mental model). NOT Docker (network call, cold-start seconds, external dep). JAC writes its own thin `MontyShellCapability` that opt-in routes specific tools (initially `run_shell`, later filesystem writes) through `pydantic_monty.Monty` with our existing toolset registered as external functions. Git-Clean Guard required before YOLO entry. Uses `ModeCapability`'s `approval_override` knob from Phase G.
- [ ] Stuck-loop detection
- [ ] Night Shift / cron scheduling
- [ ] User-tier memory + predict-calibrate extraction (the `~/.jac/memory.md` file exists; automatic extraction deferred)
- [ ] **ACP — editor surface (D45, condition-gated)** — `ACPCapability` wrapping the Python [ACP SDK](https://agentclientprotocol.com); sessions (maps to D3), prompt turns, HITL approvals surfaced as ACP tool-call events, slash commands, terminals, diffs. VS Code / Zed / JetBrains extensions become generic ACP clients — we write the server once. **Two conditions before building:** (1) ACP remote HTTP/WebSocket transport stabilises (currently WIP); (2) at least one major editor ships an ACP client. Full design: [`progress-roadmap.md`](progress-roadmap.md) "ACP — Editor surface" section.
- [ ] Other browser / native SDK surfaces (post-ACP, if needed)

---

## How to Use This File

- When you start a task, change `- [ ]` to `- [~]` (in flight) in this dashboard.
- When you finish, `- [x]` and a one-line note if anything deviated from the plan.
- When a new task surfaces, add it to the relevant phase or v2 — do not let it float.
- Architectural decisions go in `architecture.md` §5, not here. This file is *what*, not *why*.
- Keep this file short. Detailed landed-phase notes live in `progress-history.md`, detailed A2A notes in `progress-a2a.md`, extended future context in `progress-roadmap.md`, archived pre-reframe entries in `progress-archive-2026-05.md`.
