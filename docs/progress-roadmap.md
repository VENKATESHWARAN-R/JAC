# JAC — Extended Roadmap

> Queued and future-phase context that is useful when planning, but too heavy for the live dashboard. Start with [`progress.md`](progress.md) for current status.

## Phase 3 — Skills (D21) ⏸

**Goal:** adopt the Anthropic community skills format so JAC isn't an island — community-maintained skills install as-is, and our own minions (Phase 5) are built on the same substrate.

**Why:** D21. The previously-planned bespoke YAML AgentSpec format was reinventing what's now a community standard. Skills are markdown-with-frontmatter (`name:`, `description:`, body) loaded from `~/.jac/skills/<name>/SKILL.md` and `<repo>/.agents/skills/<name>/SKILL.md`. Default `mode: inline` injects the body into Gru's context when the skill's description matches the user's request (description-based triggering, same as Claude Code and Anthropic's own skill ecosystem). Optional `mode: minion` extends the same file for sub-agent spawning — but the *runtime* for that lives in Phase 5 (needs grooming).

- [ ] Skill loader walks `~/.jac/skills/` and `<repo>/.agents/skills/` (project shadows user)
- [ ] Frontmatter validator (community spec compliance)
- [ ] Description-based triggering — inject skill body into Gru's system prompt when relevant
- [ ] `/skill NAME` slash to force-load a skill
- [ ] `/skill list` shows available skills and their trigger conditions
- [ ] Ship 2-3 reference skills in `src/jac/data/skills/` (a `code-review` skill is a good first candidate)
- [ ] Documentation: how to write a skill (point at the Anthropic spec)
- [ ] architecture.md §11 D21 recorded ✅ (done in this change)

---

## Phase 4.1 — Auto-publish community Skills (after Phase 3) ⏸

**Why:** once Phase 3 ships the community-format skill loader, the AgentCard's `skills:` list can advertise real capabilities instead of one generic placeholder. This is what makes Phase 3 and Phase 4 reinforce each other.

- [ ] Loaded inline-mode community skills (from `<repo>/.agents/skills/` and `~/.jac/skills/`) auto-appear as `Skill` entries in the AgentCard; frontmatter `description` → A2A `Skill.description`, frontmatter `name` → A2A `Skill.id`
- [ ] Optional per-skill enable/disable via `a2a.guest.advertise_skills: [name1, name2]` (default: all installed)
- [ ] Test: skill loader → card builder integration

---

## Phase 5 — Minions ⏸ (grooming pending)

**Goal:** runtime for skills with `mode: minion` — spawn a sub-agent with isolated context, scoped toolset, structured output, return to Gru.

**⚠️ Needs a grooming session before any code lands.** D21 locks the *file format* (skills with `mode: minion`). The *runtime* is not yet designed: output schema enforcement, tool scoping rules, factory orchestration, parallelism (one at a time? many?), failure handling, structured output validation, retry policy. Owner of next grooming session: TBD.

- [ ] Grooming session: lock the runtime design, add D-numbers
- [ ] Minion factory capability + `spawn_minion(reason, skill_name, task_packet)` tool
- [ ] Task packet schema (already locked in §5a — `objective` / `success_criteria` / `relevant_files` / `forbidden_actions` / `expected_output`)
- [ ] Tier-based model selection from `model_tier:` field
- [ ] Structured output validation against the skill's `output_schema:` block
- [ ] First 2-3 reference minion-mode skills

---

## Phase 6 — MCP ⏸

**Goal:** consume external MCP servers so JAC's tool surface scales without us writing every tool by hand.

**`reason:` tension resolved (D28):** MCP tools don't carry `reason: str`. We accept loose enforcement — render `reason: (mcp tool — no reason captured)` in the approval UI. Honest about the gap, community-compatible. See architecture.md §11 D28.

- [x] `reason:` tension resolved as D28 (2026-05-22)
- [ ] `~/.jac/mcp.yaml` schema + loader
- [ ] `MCPServerStdio` / `MCPServerHTTP` wiring via pydantic-ai
- [ ] `/mcp list` and `/mcp reload` slash commands
- [ ] Per-server enable/disable

---

## Phase 7 — Quality 🚧

- [ ] CodeMode integration moved to v2 (no concrete pain yet — D9 in idea.md notes)
- [ ] Stuck-loop detection moved to v2 (low value in HITL where human catches loops)
- [x] Provider registry tests (`tests/test_provider_registry.py`, `just test`)
- [x] Phase 1.7 capability tests — compaction, status bar, budgets, plan persistence, HITL feedback, web backends (`test_history`, `test_statusbar`, `test_usage`, `test_budget_slash`, `test_plan_persistence`, `test_hitl_feedback`, `test_web_backends`; ~98 tests)
- [ ] Broader test suite (pytest) — Phase 1 core (session, fs/shell bus), memory (`remember`/`forget`), slash edge cases
- [x] Ruff + ty config (`pyproject.toml` `[tool.ruff]` / `[tool.ty]`; `just check` runs format, lint, `ty check src`)
- [x] User docs on Zensical — six pages under `docs/user-guide/` (getting-started, cli-reference, configuration, sessions-and-memory, examples, a2a-operator); expand as Phase 3/4 features ship

---

## v2 ⏸

- [ ] **Plan Mode + `ModeCapability` base (D23 + D29 YOLO sketch)** — deferred from 1.7.e on 2026-05-23. Carries the bundled `plan`→`tasks` rename: tools (`plan`/`update_plan`/`get_plan` → `tasks`/`update_task`/`get_tasks`), capability (`PlanCapability` → `TaskListCapability`), events (`PlanReplaced`/`PlanStepUpdated` → `TaskListReplaced`/`TaskStepUpdated`), session file (`<session>/plan.json` → `<session>/tasks.json`). Build the base + Plan Mode together so the abstraction is exercised on day one; YOLO follows when sandboxing lands.
- [ ] YOLO mode + sandboxing (Monty + sandbox-exec / bwrap + Git-Clean Guard) — uses `ModeCapability`'s `approval_override` knob (D29 sketch)
- [ ] CodeMode integration (`pydantic-ai-harness`)
- [ ] Stuck-loop detection
- [ ] Night Shift / cron scheduling
- [ ] User-tier memory + predict-calibrate extraction
- [ ] Browser / API / SDK surfaces

---
