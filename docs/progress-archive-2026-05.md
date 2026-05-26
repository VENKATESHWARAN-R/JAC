# JAC — Progress Archive (2026-05-26 reframe)

> **Status:** archived · **Reason:** superseded by the cost-efficiency thesis (see [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) and [`architecture.md`](architecture.md) §0).
>
> This file preserves the previously-planned Phase 3 (Skills with `mode: minion`), Phase 5 (Minion runtime), and Phase 6 (MCP loader) entries as they existed in `progress.md` / `progress-roadmap.md` immediately before the 2026-05-26 reframe. Use it for historical context only — **do not act on this content.** The live roadmap is [`progress.md`](progress.md).

## Why the reframe

In a planning session on 2026-05-26 we re-grounded JAC's product thesis around a single observation: **the LLM is the brain; everything around it (tools, memory, sub-agents, hooks, prompt assembly) is where JAC's value lives, and that value is measured in cost-per-task.** The old roadmap's centerpiece — community-format Skills with a `mode: minion` runtime — was rebuilt around a simpler primitive:

- **One `spawn_sub_agent` tool** the main agent calls when delegation helps. No bespoke runtime modes. No separate factory tools.
- **Skills become loadable prompts / playbooks**, not a runtime mode. They're advice the main agent reads when relevant; they may *recommend* spawning a sub-agent in their prose, but the runtime mechanism is the tool, not a frontmatter field.
- **Deterministic hooks** attached per-spawn to avoid LLM "did this work?" turns.
- **Tool result post-processor** with cheap-tier summarization, applied to *every* tool call, runs before sub-agents are even needed.

The active phases (A–G) live in [`progress.md`](progress.md).

---

## Archived: Phase 3 — Skills with `mode: minion` (D21 original)

**Original goal:** adopt the Anthropic community skills format so JAC isn't an island — community-maintained skills install as-is, and our own minions (Phase 5) were built on the same substrate.

The frontmatter was to include a `mode:` field with two values:

- `mode: inline` — inject skill body into Gru's system prompt on description match or `/skill NAME`.
- `mode: minion` — spawn a sub-agent loaded from the skill, with `model_tier:`, `tools:`, and `output_schema:` driving the runtime.

```
- [ ] Skill loader walks ~/.jac/skills/ and <repo>/.agents/skills/ (project shadows user)
- [ ] Frontmatter validator (community spec compliance, including mode field)
- [ ] Description-based triggering — inject skill body when relevant
- [ ] /skill NAME slash to force-load
- [ ] /skill list shows available skills
- [ ] Ship 2-3 reference skills in src/jac/data/skills/
- [ ] Documentation
```

**Status of the new design:** Skill loader survives as Phase D, but `mode: minion` is dropped. Skills are inline-only — loadable prompts/playbooks. Sub-agents come from the `spawn_sub_agent` tool (D35), not from a skill frontmatter field. D21 was revised in-place; see `architecture.md` §5.

---

## Archived: Phase 4.1 — Auto-publish community Skills via A2A AgentCard

**Original goal:** once Phase 3 ships, the AgentCard's `skills:` list advertises real capabilities instead of one generic placeholder.

```
- [ ] Loaded inline-mode community skills auto-appear as Skill entries in the AgentCard
- [ ] Optional per-skill enable/disable via a2a.guest.advertise_skills config
- [ ] Test: skill loader → card builder integration
```

**Status:** still relevant once Phase D lands. Re-add to the live roadmap then. The card-builder integration is small (~30 LOC), it just needs Phase D's loader to exist.

---

## Archived: Phase 5 — Minion runtime (grooming-pending)

**Original goal:** runtime for skills with `mode: minion` — spawn a sub-agent with isolated context, scoped toolset, structured output, return to Gru. Required a separate grooming session before any code landed.

```
- [ ] Grooming session: lock the runtime design, add D-numbers
- [ ] Minion factory capability + spawn_minion(reason, skill_name, task_packet) tool
- [ ] Task packet schema (objective / success_criteria / relevant_files / forbidden_actions / expected_output)
- [ ] Tier-based model selection from model_tier: field
- [ ] Structured output validation against the skill's output_schema: block
- [ ] First 2-3 reference minion-mode skills
```

**Status:** the conceptual goal — delegating context-heavy work to an isolated sub-agent — is alive in Phase B. The differences:

| Old (Phase 5 Minion) | New (Phase B sub-agent) |
| --- | --- |
| Spawned from a skill with `mode: minion` | Spawned from one tool: `spawn_sub_agent(...)` |
| Model picked from skill's `model_tier:` field | Tier picked at call time, HITL-approved (D39) |
| Output schema in skill frontmatter | `result_type` set on the `Agent` separately; packet just describes intent |
| Tools scoped via skill frontmatter | Tools scoped via `allowed_tools` in the task packet |
| Required new file format + factory + loader before any code | Implementable directly: one tool, one capability, no loader prerequisite |

The "minion" terminology is retired. Rename to "sub-agent" in any old code or docs touched as part of an unrelated change.

---

## Archived: Phase 6 — MCP loader (deferred, not killed)

**Original goal:** consume external MCP servers so JAC's tool surface scales without writing every tool by hand. D28 resolved the `reason:` tension (MCP tools render `reason: (mcp tool — no reason captured)`).

```
- [x] reason: tension resolved as D28 (2026-05-22)
- [ ] ~/.jac/mcp.yaml schema + loader
- [ ] MCPServerStdio / MCPServerHTTP wiring via pydantic-ai
- [ ] /mcp list and /mcp reload slash commands
- [ ] Per-server enable/disable
```

**Status:** lives on as **Phase G**, lower priority than A–F. Not killed — JAC's tool surface absolutely needs MCP eventually — just deprioritized below the cost-efficiency arc. The implementation hasn't changed; the timing has.

---

## What survived intact

These were *not* affected by the reframe and continue in `progress.md` unchanged:

- **Phase 0–1.7** — shipped. See `progress-history.md`.
- **Phase 2a / 2a.1** — `remember` / `forget` and user-scope memory. Shipped.
- **Phase 4 A2A** — PR1–PR4 + 4.d hotfixes + file transfer + demo peer. Feature-complete except 4.e (OIDC/GCP) which moves into Phase G.
- **Phase 7 Quality** — ongoing.
- **All locked decisions D1–D33** — preserved. D1 (task packet) and D21 (skills) were *revised* in-place (see `architecture.md` §5); D23 (Plan Mode) was *promoted* from v2 to Phase F. Everything else is untouched.

## Pointer to the new arc

For what's actually happening from 2026-05-26 forward, read in this order:

1. [`architecture.md`](architecture.md) §0 — the thesis and five levers.
2. [`design/cost-efficient-orchestration.md`](design/cost-efficient-orchestration.md) — the active design spec.
3. [`progress.md`](progress.md) — the live tracker for Phases A–G.
