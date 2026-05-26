# JAC — Implementation Progress

> **Just Another Companion/CLI** · **Updated:** 2026-05-26 · keep this in sync as work lands.

This file is the **live progress dashboard**: what is shipped, what is active, and what should happen next. It should be short enough for an agent to read at the start of every task without drowning in old implementation detail.

For deeper context:

- Product scope: [`idea.md`](idea.md)
- Architecture and decisions: [`architecture.md`](architecture.md)
- Completed-phase history: [`progress-history.md`](progress-history.md)
- Detailed A2A phase log: [`progress-a2a.md`](progress-a2a.md)
- Extended queued/future roadmap: [`progress-roadmap.md`](progress-roadmap.md)

## Agent Start Here

- **Current active work:** Phase 4.d.6 hotfixes wrapped 2026-05-26 (strip `BinaryContent` before agent run, wire `AnalystWorker` lifespan in demo, headless terminal feedback). Phase 4.e is next: OIDC + GCP ID token strategies — or skip to Phase 3 Skills if auth extensions aren't urgent.
- **Nearest follow-up after 4.e:** Phase 3 — community-format Skills loader.
- **A2A is feature-complete for the originally-scoped surface.** Outbound polling, inbound auth, bidirectional file transfer, pluggable peer auth, retention enforcement, usage accounting, demo peer — all shipped.
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
| Phase 4 — A2A (D24, D30, D31, D32, D33) | 🚧 In flight | PR1-PR4 + 4.d hotfixes + file transfer + demo peer landed; Phase 4.e auth extensions (OIDC / GCP) are next |
| Phase 5 — Minions | ⏸ Queued | runtime for skills with `mode: minion`; needs grooming before implementation |
| Phase 6 — MCP | ⏸ Queued | external MCP servers + D28 `reason:` compromise |
| Phase 7 — Quality | 🚧 In flight | ruff + ty + Zensical docs shipped; remaining gaps in broader test coverage |
| v0.2 source restructuring | ✅ Complete | released as v0.2.0; see [`progress-history.md`](progress-history.md) |
| v2 | ⏸ Future | YOLO + Monty + CodeMode + stuck-loop + Night Shift + user-tier predict-calibrate memory |

---

## Current Focus — Phase 4.e OIDC + GCP ID Tokens ⏸

**Goal:** add the cloud auth strategies enabled by D31's outbound auth protocol.

- [ ] `OidcAuth` config model: `issuer` + `client_id` + `client_secret` + `scope`, with discovery from `<issuer>/.well-known/openid-configuration`.
- [ ] `GcpIdTokenAuth` config model: `audience`, backed by `google-auth`.
- [ ] Add `google-auth` as an optional dependency (`jac[gcp]`) so the base wheel stays small.
- [ ] Add strategy classes and `make_strategy` dispatch branches.
- [ ] Update `gru_system.md` and user docs with Azure / GCP / Okta peer examples.

See [`progress-a2a.md`](progress-a2a.md) for the completed PR1-PR4 log and queued Phase 4.1 context.

---

## Previously Active — Phase 4.d.6 A2A file-transfer hotfixes ✅ (landed 2026-05-26)

**Goal:** fix real-world failures found during live testing of `jac a2a serve` and the data-analyst demo peer.

- [x] **`strip_binary_content_from_history`** (new helper in `capabilities/a2a/guest_files.py`) — walks pydantic-ai message history and removes `BinaryContent` items from `UserPromptPart.content` lists. Model adapters (OpenAI/Ollama: `openai.py:1583`, Anthropic: `anthropic.py:1807`) reject `text/csv` / `application/octet-stream` / most non-image types. Called in `AuditingAgentWorker.run_task` right after `build_message_history`; the path-based annotation added by `materialize_inbound_files` is the contract for the guest — raw bytes are redundant and fatal. Same helper inlined in the demo peer.
- [x] **Data-analyst demo `AnalystWorker` never ran** — `build_app` created the custom worker but passed no `lifespan=` to `agent_to_a2a`, so fasta2a used its own plain `AgentWorker` internally. Added `_lifespan` contextmanager to `build_app` that boots the `AnalystWorker`, mirroring JAC's own server pattern.
- [x] **Headless `jac a2a serve` terminal feedback** — the command now creates an `EventBus`, passes it to the capability, and runs a `_print_events` background task that prints `[a2a in ←]` / `[a2a in ✓]` lines with `HH:MM:SS` timestamps, matching the in-REPL renderer format.
- [x] Tests: 7 new unit tests for `strip_binary_content_from_history` in `test_a2a_guest_files.py`; `test_inbound_file_part_lands_under_guest_uploads` augmented to assert terminal state is `completed`. 357 tests pass.

---

## Previously Active — Phase 4.d A2A Polish ✅ (landed 2026-05-26)

**Goal:** make A2A operable, not just functional.

- [x] `/a2a status` — now renders server state, peer count (with profile/session split), and the last 5 inbound calls tailed from `inbound.jsonl`.
- [x] Budget integration: `UsageTracker.add_external(input, output)` records A2A guest call usage onto a new `external` counter; `project_total` includes it, `session_total` does not. JSONL rows carry a `kind` marker (`session` / `a2a_guest`). `AuditingAgentWorker.run_task` now captures `result.usage()` from the guest agent. `/tokens` shows a dedicated "a2a guest" line when external usage > 0.
- [x] Retention enforcement: in addition to the existing run-on-start pass, the server now spawns a 1-hour periodic `cleanup_old_contexts` task; cancelled cleanly on stop. Skipped when `retention_days == 0`.
- [x] OAuth2 fresh-token visibility: new `A2AOutboundTokenMinted` event posted by `OAuth2ClientCredentialsStrategy` after every successful refresh; renderer paints a muted `[a2a token]` line with peer name + expiry. Bearer / api_key strategies don't emit (no IDP roundtrip to surface).
- [x] `architecture.md §6 + §8` added — inbound + outbound sequence diagrams with storage, audit, usage, and auth strategy table.
- [x] Tests landed: external-usage accounting (5 new in `test_usage.py`), status renderings (4 new in `test_a2a_slash.py`), retention lifecycle + usage tracker plumbing (3 new in `test_a2a_server.py`), token-minted event + bus threading (4 new in `test_a2a_auth_strategies.py`). 312 tests pass.

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
