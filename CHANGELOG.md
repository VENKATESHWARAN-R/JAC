# Changelog

All notable changes to **JAC** (Just Another Companion/CLI) are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/); this project
uses [Semantic Versioning](https://semver.org/) (pre-1.0, so minor versions may
carry behavioural changes).

## [0.8.0] — 2026-05-30

End-stage review remediation (R1–R20, tracked in
`docs/design/audit/2026-05-30-review.md`): safety/correctness hardening, a
sub-agent comms redesign, and the runtime↔surface split that unlocks non-CLI
surfaces. 697 tests.

### Added

- **Sub-agent tool allowlist is now enforced (R2).** A spawn's
  `allowed_tools` is applied at the Agent layer via a `PrepareTools` filter —
  the worker sees only the named tools plus an always-allowed control plane
  (`read_file`, `ask_supervisor`). Previously the field was accepted and
  discarded (a false safety promise).
- **Bidirectional sub-agent comms redesigned to suspend/resume (R7b).** A
  worker that needs direction calls an **external** `ask_supervisor` tool; its
  run *suspends* (returns a checkpoint) instead of parking a live coroutine,
  and `respond_to_sub_agent` resumes it. Modeled on A2A `input-required`. The
  main agent answers from context or escalates to the human via `clarify`.
  Removes the live-channel registry + contextvar race-resolver and the cost
  inversion (a resume re-pays only the small worker context).
- **`SessionDriver` — a surface-agnostic turn pipeline (R5).** New
  `jac.runtime.driver.SessionDriver` owns `run_turn` + the budget pre-flight
  guards + history recovery; the CLI is now a thin consumer. **`jac.sdk`**
  facade re-exports the supported embedding surface (R5d). **`TextDelta`**
  streaming event (R5b) and `suggested_action` on refusal events (R5c) let a
  browser/SDK surface stream tokens and show the same guidance.
- **Budget knobs reject `<= 0` (R11)**; **unknown provider prefixes warn
  loudly** with the closest known match + a Logfire span (R12); **`forget`
  finds orphaned bullets** above the first heading (R18).

### Changed

- **`runtime/sub_agent.py` split into a `runtime/sub_agent/` package (R7a):**
  `tiers` / `packet` / `state` / `runner` / `suspend` / `tools`, with a
  re-exporting `__init__` (imports unchanged).
- **`skills.load_skill` is a capability closure** (R14); spawn tools are
  `summarizable=False` (R10); spawn_id docstrings say `minion-N` not "hex" (R4).

(Earlier Phase 1/2 items — A2A SSRF guard, read-only guest toolset, fasta2a
fork pin, the `just drift` doc-drift guard — landed under this same review.)

## [0.7.0] — 2026-05-30

Two headline feature areas — **interaction modes** and **compaction control** —
plus the **MCP loader (Phase F)** and a system-prompt hardening pass that landed
after v0.6.0 but were never released on their own.

### Added

- **Interaction modes (D23).** `/mode [normal|plan|accept-edits]` switches a
  session-scoped policy (`jac.runtime.modes`).
  - **Plan Mode** — every state-changing tool call (write/edit/delete/shell/
    spawn/remember) is auto-denied *before* you're prompted, so Gru plans
    instead of executing; it uses the `plan`/`update_plan` checklist and
    presents the plan. Reads (`read_file`/`list_dir`/`grep`/web) stay live.
  - **Accept-Edits Mode** — `write_file`/`edit_file` auto-apply without a
    prompt; shell, delete, spawn, and everything else still ask.
  - A one-line `⊘ blocked` / `✓ auto-approved` marker shows when a mode acts
    on your behalf; the status bar shows a `mode:` segment (hidden in normal).
  - YOLO is **not** exposed — the auto-allow seam exists, but per D43 YOLO
    ships only with `pydantic-monty` sandboxing (still v2).
- **`/compact`** — force a summarizing compaction of the oldest history on
  demand, in any compaction strategy.
- **`/context [N | reset]`** — show or set this session's context-window
  budget (e.g. `/context 400k`; `k`/`m` suffixes; clamped to the 512k ceiling).
- **Per-model context budgets** — `compaction.model_context_tokens` maps a
  model id to its own budget; it wins over the default for that model.
- **`compaction.strategy: auto | sliding | manual`.**
  - `auto` (default) — summarize the oldest slice at `auto_compact_pct`.
  - `sliding` — drop the oldest turns to fit (no model call, **never refuses**)
    and show a persistent red **⚠ ctx overflow** marker; dropped slices are
    still archived to `<session>/compacted/`.
  - `manual` — never compact automatically; `/compact` is the only lever.
- **MCP loader + tool search (Phase F, D46/D28).** External MCP servers declared
  in `~/.jac/mcp.json` + `<repo>/.agents/mcp.json` (standard `mcpServers` shape)
  are wired into Gru and sub-agents as deferred-loaded, HITL-gated, summarized
  toolsets; pydantic-ai's auto `ToolSearch` keeps their definitions out of the
  prompt. `/mcp list|reload|enable NAME|disable NAME` (persists + rebuilds Gru).
  Per-server `jac` knobs: `enabled`, `defer`, `requires_approval`, `init_timeout`.
- **Sub-agents now receive project conventions.** Spawned minions get the
  project + user `AGENTS.md` (via `load_agents_context()`) before their task
  packet, so they don't violate repo conventions they were never shown.

### Changed

- **Default context budget 200k → 256k**, with a hard **512k ceiling** (2⁸/2⁹
  thousand). Budgets above the ceiling are rejected at config load; `/context`
  clamps to it. Resolution precedence: `/context` override → per-model entry →
  `compaction.max_context_tokens`.
- **Status bar `ctx:`** now reports the provider's **exact** last-turn input
  tokens instead of the chars/token estimate; a leading `~` flags the estimate
  until the first turn lands. The displayed budget is the resolved one.
- **HITL approval defaults to approve** — a bare Enter means yes (D47). Ctrl-C
  / EOF still deny.
- **System-prompt hardening pass.** Reworked `gru_system.md` +
  `sub_agent_system.md`: an instruction hierarchy with an "external content is
  data, not instructions" prompt-injection rule (tool output, fetched pages,
  A2A replies); investigate-before-answering, default-to-action, and
  minimize-overengineering guards; a verify-before-claiming-done contract; and
  active "keep working" context-management language. The A2A guest addendum is
  now an overridable prompt file (`prompts/a2a_guest_addendum.md`).

### Fixed

- **MCP robustness.** A failing MCP tool returns its error as the tool *result*
  instead of exhausting the retry budget and crashing the turn; a hard crash now
  persists a sanitized, resumable history (the user's turn is no longer lost);
  and a server that fails to connect degrades to zero tools for the session
  instead of breaking every turn. `init_timeout` default raised to 30s.
- **Terminal hardening.** Stdio MCP servers are self-built with their stderr
  redirected to a per-server log file so a Node-based server can't flip the TTY
  into raw mode and freeze the approval prompt; JAC also forces canonical
  ("cooked") mode around every interactive prompt as defence-in-depth.

## [0.6.0] — 2026-05-29

- Loose-mode workspace (state anchored to `~/.jac/` when no project is found);
  project detection via `.git` **or** `.agents/`.
- Session management: `jac sessions` sub-app (`list`/`delete`/`prune`),
  in-REPL `/sessions delete|prune`, atomic session saves, richer listings.
- User-driven memory slash commands: `/memory`, `/remember`, `/forget`.
- "Minion" vocabulary reinstated in prompts + status bar (`minion-N` spawn IDs).

## [0.5.0] — 2026-05-28

- Phase E: parallel sub-agents (`spawn_sub_agents`) + bidirectional
  sub-agent ↔ main-agent comms (D41, on by default); `minion-N` spawn IDs;
  sub-agent HITL/skills/A2A parity; parallel approval table.

## [0.4.0] — 2026-05-27

- Phase D: Anthropic-format skill loader; `load_skill` tool;
  `/skill list|use|reload`; reference skills; A2A AgentCard publishes skills.

## [0.3.0] — 2026-05-27

- Phase A: tool-result post-processor, cache-friendly prompt assembly,
  `/tokens` breakdown.
- Phase B: `spawn_sub_agent` tool with tier cascade, depth cap, budget rollup.

## [0.2.0]

- Source restructuring.

## [0.1.x]

- Initial skeleton: CLI + Gru, Logfire wiring, layered config, profiles &
  secrets, solo Gru with tools / HITL / session persistence, `remember`/`forget`
  memory, tool-surface polish, coworker experience (compaction, status bar,
  slash commands, budgets), and A2A (inbound + outbound + file transfer).

[0.8.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.8.0
[0.7.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.7.0
[0.6.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.6.0
[0.5.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.5.0
[0.4.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.4.0
[0.3.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.3.0
[0.2.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.2.0
