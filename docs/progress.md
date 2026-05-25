# JAC — Implementation Progress

> **Just Another Companion/CLI** · **Updated:** 2026-05-25 · keep this in sync as work lands.

This file is the **live progress dashboard**: what is shipped, what is active, and what should happen next. It should be short enough for an agent to read at the start of every task without drowning in old implementation detail.

For deeper context:

- Product scope: [`idea.md`](idea.md)
- Architecture and decisions: [`architecture.md`](architecture.md)
- Completed-phase history: [`progress-history.md`](progress-history.md)
- Detailed A2A phase log: [`progress-a2a.md`](progress-a2a.md)
- Extended queued/future roadmap: [`progress-roadmap.md`](progress-roadmap.md)

## Agent Start Here

- **Current active work:** Phase 4.d — A2A polish: `/a2a status`, inbound usage accounting, retention timer, fresh-token visibility, and architecture diagrams.
- **Nearest follow-up:** Phase 4.e — OIDC + GCP ID token auth strategies after Phase 4.d lands.
- **Do not build yet without a grooming session:** Phase 5 minions, v2 YOLO/sandboxing, Plan Mode + `ModeCapability`.
- **Important constraint:** A2A and Skills are no longer v2 work. A2A is Phase 4; Skills are Phase 3; Minions are Phase 5.
- **When design is ambiguous:** `architecture.md` is the source of truth for how; `idea.md` is the source of truth for what JAC is and is not.

## Status Summary

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 0 — Skeleton | ✅ Complete | bare CLI + Gru, Logfire wired, no tools |
| Phase 0.5 — Config foundation | ✅ Complete | workspace, layered config, AGENTS.md, `jac init` |
| Phase 1 — Solo Gru | ✅ Complete | event bus, tools, HITL, session persistence + resume |
| Phase 1.5 — Profiles & secrets | ✅ Complete | multi-profile config, keyring/dotenv/env-only backends, `jac profiles`/`jac keys` |
| Phase 2a — `remember` tool | ✅ Complete | HITL-gated project memory via `.agents/memory.md` |
| Phase 2a.1 — User scope + `forget` | ✅ Complete | user/project memory scopes, `forget`, session-id audit trail |
| Phase 1.6 — Tool surface polish | ✅ Complete | plan, background processes, fs/grep upgrades, web search, clarify |
| Phase 1.7 — Coworker experience | ✅ Complete (minus deferred) | compaction, status bar, slash commands, budgets, feedback, plan persistence, Tavily/DDG search. Plan Mode deferred to v2. |
| Phase 2b — Summarizer minion | ⛔ Superseded | rolled into Phase 1.7.a token-aware compaction |
| Phase 3 — Skills (D21) | ⏸ Queued | community-format skill loader + inline mode |
| Phase 4 — A2A (D24, D30, D31) | 🚧 In flight | PR1-PR3 landed; Phase 4.d polish is active; Phase 4.e auth extensions next |
| Phase 5 — Minions | ⏸ Queued | runtime for skills with `mode: minion`; needs grooming before implementation |
| Phase 6 — MCP | ⏸ Queued | external MCP servers + D28 `reason:` compromise |
| Phase 7 — Quality | 🚧 In flight | ruff + ty + Zensical docs shipped; remaining gaps in broader test coverage |
| v0.2 source restructuring | ✅ Complete | released as v0.2.0; see [`progress-history.md`](progress-history.md) |
| v2 | ⏸ Future | YOLO + Monty + CodeMode + stuck-loop + Night Shift + user-tier predict-calibrate memory |

---

## Current Focus — Phase 4.d A2A Polish ⏸

**Goal:** make A2A operable, not just functional.

- [ ] `/a2a status` — running? bind host:port? truncated token? peer count? last 5 calls?
- [ ] Budget integration: per-inbound-call `result.usage()` feeds host's `UsageTracker.add_external(input, output)` — counts under `project_total` only, **not** `session_total`. Surfaces in `/tokens` as a separate "a2a guest" line.
- [ ] Context retention enforcement: `cleanup_old_contexts(retention_days)` runs on server start AND on a 1-hour timer while server runs.
- [ ] OAuth2 strategy: surface a separate `[a2a token]` event when a fresh access token is minted.
- [ ] `architecture.md §6 + §8` diagrams refreshed to show A2A flow: inbound, outbound, storage, audit, and outbound auth strategies.

See [`progress-a2a.md`](progress-a2a.md) for the completed PR1-PR3 log and queued Phase 4.e / 4.1 context.

---

## Next Queued — Phase 4.e OIDC + GCP ID Tokens ⏸

**Goal:** add the cloud auth strategies enabled by D31's outbound auth protocol.

- [ ] `OidcAuth` config model: `issuer` + `client_id` + `client_secret` + `scope`, with discovery from `<issuer>/.well-known/openid-configuration`.
- [ ] `GcpIdTokenAuth` config model: `audience`, backed by `google-auth`.
- [ ] Add `google-auth` as an optional dependency (`jac[gcp]`) so the base wheel stays small.
- [ ] Add strategy classes and `make_strategy` dispatch branches.
- [ ] Update `gru_system.md` and user docs with Azure / GCP / Okta peer examples.

---

## Queued — Phase 3 Skills (D21) ⏸

**Goal:** adopt the Anthropic community skills format so JAC is not an island.

- [ ] Skill loader walks `~/.jac/skills/` and `<repo>/.agents/skills/` (project shadows user).
- [ ] Frontmatter validator for community spec compliance.
- [ ] Description-based triggering injects skill body into Gru's system prompt when relevant.
- [ ] `/skill NAME` slash to force-load a skill.
- [ ] `/skill list` shows available skills and trigger conditions.
- [ ] Ship 2-3 reference skills in `src/jac/data/skills/`.
- [ ] Documentation: how to write a skill.

See [`progress-roadmap.md`](progress-roadmap.md) for the extended rationale and downstream Phase 4.1 connection.

---

## Grooming Required — Phase 5 Minions ⏸

**Goal:** runtime for skills with `mode: minion` — spawn a sub-agent with isolated context, scoped toolset, structured output, and return to Gru.

- [ ] Grooming session: lock runtime design, add D-numbers.
- [ ] Minion factory capability + `spawn_minion(reason, skill_name, task_packet)` tool.
- [ ] Task packet schema: `objective` / `success_criteria` / `relevant_files` / `forbidden_actions` / `expected_output`.
- [ ] Tier-based model selection from `model_tier:` field.
- [ ] Structured output validation against the skill's `output_schema:` block.
- [ ] First 2-3 reference minion-mode skills.

---

## Queued — Phase 6 MCP ⏸

**Goal:** consume external MCP servers so JAC's tool surface scales without us writing every tool by hand.

- [x] `reason:` tension resolved as D28: MCP tools do not carry `reason: str`; approval UI shows `reason: (mcp tool — no reason captured)`.
- [ ] `~/.jac/mcp.yaml` schema + loader.
- [ ] `MCPServerStdio` / `MCPServerHTTP` wiring via Pydantic AI.
- [ ] `/mcp list` and `/mcp reload` slash commands.
- [ ] Per-server enable/disable.

---

## Ongoing — Phase 7 Quality 🚧

- [ ] CodeMode integration moved to v2 (no concrete pain yet — D9 in `idea.md` notes).
- [ ] Stuck-loop detection moved to v2 (low value in HITL where human catches loops).
- [x] Provider registry tests.
- [x] Phase 1.7 capability tests: compaction, status bar, budgets, plan persistence, HITL feedback, web backends.
- [ ] Broader test suite: Phase 1 core, memory, slash edge cases.
- [x] Ruff + ty config; `just check` runs format, lint, and `ty check src`.
- [x] User docs on Zensical; expand as Phase 3/4 features ship.

---

## Future — v2 ⏸

- [ ] **Plan Mode + `ModeCapability` base (D23 + D29 YOLO sketch)** — includes the bundled `plan`→`tasks` rename.
- [ ] YOLO mode + sandboxing with Monty + `sandbox-exec` / `bwrap` + Git-Clean Guard.
- [ ] CodeMode integration (`pydantic-ai-harness`).
- [ ] Stuck-loop detection.
- [ ] Night Shift / cron scheduling.
- [ ] User-tier memory + predict-calibrate extraction.
- [ ] Browser / API / SDK surfaces.

---

## How to Use This File

- When you start a task, change `- [ ]` to `- [~]` (in flight) in this dashboard.
- When you finish, `- [x]` and a one-line note if anything deviated from the plan.
- When a new task surfaces, add it to the relevant phase or "v2" — do not let it float.
- Architectural decisions go in `architecture.md §11`, not here. This file is *what*, not *why*.
- Keep this file short. Move detailed landed-phase notes to `progress-history.md`, detailed A2A notes to `progress-a2a.md`, and extended future context to `progress-roadmap.md`.
