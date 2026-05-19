# JAC — IDEA

> **Status:** Draft v2 · **Last revised:** 2026-05-19 · **Type:** product vision

## What JAC is

JAC is a **local-first AI coworker harness**. It runs on the user's machine and wraps an LLM with the things the model lacks on its own: persistent memory, tools, orchestration, context discipline, and continuity across sessions.

JAC is the runtime around the model that turns raw reasoning into a practical worker.

Stack direction: **Python + Pydantic AI**, multi-provider for models.

## Why JAC exists

In honest priority order:

1. **Learning.** Building a harness end-to-end is the best way to understand modern agentic systems. The build itself is the point — shipping is a side effect.
2. **Cross-repo coworking via A2A.** Two JAC instances on two repos can talk to each other via the A2A protocol — letting an agent in a frontend repo negotiate API changes with an agent in a backend repo without a human mediating. Nothing in the open-source landscape covers this well today.

JAC is **not** trying to beat Claude Code at what Claude Code already does well. Most table-stakes capabilities will be shared with the existing pack. We're not chasing benchmark wins.

## What JAC focuses on

- **One visible coworker ("Gru")** that owns the conversation, memory, mode selection, and final accountability.
- **Disposable scoped workers ("minions")** spawned only when delegation reduces context noise, cost, latency, or risk.
- **A "Minion Factory"** that encodes orchestration playbooks (spec-driven, TDD, parallel research, etc.) and worker prompt patterns — so Gru can delegate well without bloating its own system prompt.
- **Two execution modes:** HITL (every tool call gated on approval) and YOLO (sandboxed, autonomous).
- **Three-tier memory:** session, project long-term, user long-term.
- **CodeMode-first tool surface:** prefer one sandboxed Python executor over many granular file tools.
- **A2A interop** as the headline non-table-stakes feature (v2).

## What JAC explicitly does NOT do (in v1)

- Compete on benchmarks against Claude Code, Devin, Aider, etc.
- Run as a hosted/cloud service. Local-first means local execution.
- Provide a managed/SaaS offering.
- Ship its own LLM. Multi-provider via Pydantic AI.
- Support every IDE/surface up front. **CLI first.**
- Market "cost-effective tiered model routing" as a differentiator — it's crowded and hard to prove. Tiered routing is a capability we'll use internally, not a marketed angle.
- Win on raw autonomy — Devin and OpenHands already compete there.

## Architecture (current thesis)

```
User
  ↓
Gru ── works directly for simple tasks
  ↓
Minion Factory ── given Gru's intent, produces a worker spec
  ↓
Minion(s) ── short-lived, scoped, isolated worker(s)
  ↓
Gru ── merges results, decides next action, reports back
```

**Gru** owns: the conversation, memory, mode, accountability, the decision to work directly vs. delegate.

**Minion Factory** is implemented as a **playbook library + thin policy layer** first; escalates to LLM-driven spec generation only if static patterns prove insufficient. Given a goal, it produces a worker spec: objective, context packet, allowed tools, model tier, stop condition, expected output, approval policy.

**Minions** are disposable. Receive clean task packets, use only granted tools, return structured output, disappear. They do not inherit Gru's full conversation by default.

Starting templates: researcher, planner, builder, reviewer, tester, summarizer. These are starting points the Factory instantiates per task, not permanent agents.

**Important separations (do not conflate):**

- **Mode** (HITL/YOLO) — how much of the loop runs without human input.
- **Approval policy** — which tools/actions require confirmation, attached per-tool not per-persona.
- **Model tier** — selected per step / per minion, not globally.
- **Role/persona** — what kind of work a worker is doing.

YOLO does not bypass approval. It changes the default to "approve" *within* a sandbox boundary.

## Operating modes

- **HITL (default):** Every tool call requires user approval. The right mode for any non-trivial environment, and the only mode in v1.
- **YOLO:** Autonomous; tool calls auto-approve within configured boundaries. **Requires sandbox.** Best for scheduled / overnight runs. **Deferred to v2.**

## Sandboxing (when we get to YOLO)

**Two-tier native approach** — no Docker daemon, no VMs, no cloud:

1. **Python tier — [Monty](https://github.com/pydantic/monty):** Pydantic's Rust-based Python sandbox. Sub-millisecond startup. Used for the agent's internal reasoning, scratchpad code, and pure-Python transformations. No network or filesystem unless explicitly mapped.
2. **Shell tier — OS-native jails:**
   - **macOS:** `sandbox-exec` (Seatbelt) with per-session profiles.
   - **Linux:** `bwrap` (Bubblewrap).
   - Both: project dir is read/write; `~/.ssh`, `~/.aws`, credential paths are denied; network policy is configurable.

**Git-Clean Guard:** YOLO refuses to start unless the working tree is clean. Recovery is `git reset --hard`.

**Not using:** Docker (high ceremony, requires daemon), Firecracker/gVisor (overkill, Linux-mostly), e2b (violates local-first).

**v2 candidates to watch:** NVIDIA OpenShell (currently alpha — Landlock + Docker, philosophically aligned), WASI/Wasm, opt-in cloud execution for heavy ML workloads.

## Memory model

Three tiers:

- **Session (short-term):** Conversation transcript for the active session. In-memory + disk-persisted per session.
- **Project long-term:** Facts, decisions, constraints, evidence, patterns scoped to one project/repo.
- **User long-term:** Preferences, role, knowledge, recurring patterns across projects.

**Extraction approach: Predict-Calibrate** (steal from [memv](https://github.com/vstorm-co/memv)). Don't summarize-and-vectorize everything. Predict what the next conversation should contain based on existing memory; compare to actual conversation; store only the prediction errors. Keeps long-term stores razor-sharp instead of bloating into noise.

**Open: who writes when.** Likely a summarizer minion at session boundary + opportunistic mid-session writes for high-signal facts. Not finalized.

## Patterns we are explicitly borrowing

Identified from the landscape research — these are mandatory or near-mandatory for any serious 2026 harness:

| Pattern | Source | Why we want it |
| --- | --- | --- |
| **CodeMode** | `pydantic-ai-harness` | One `run_code` tool in Monty beats N granular file tools. Cuts round-trips, latency, and cost. |
| **Predict-Calibrate memory** | `memv` | Stops long-term memory bloat by only storing what the model failed to predict. |
| **Stuck-loop detection** | `pydantic-deepagents` | Detect A-B-A-B tool-call loops and intervene. **Mandatory for YOLO.** |
| **Orphan tool-call repair** | `pydantic-deepagents` | Clean up broken/dangling tool-call history before the next model call. |
| **Agent-authored skills** | `earendil-works/pi` | Agent saves a useful script as a reusable skill mid-session; future sessions auto-discover it. |

## Differentiators (honest)

What's genuinely uncovered in the open-source landscape, in order of conviction:

1. **Cross-repo A2A interop.** Two JAC instances on two repos coordinating on a change. Frontend-JAC asks backend-JAC what an endpoint returns and adapts. No incumbent does this. Headline feature.
2. **"Night Shift" / scheduled autonomous runs.** Cron-triggered minions: dependency bumps, lint fixes, deprecated-API migrations, summary PRs by morning. The space assumes interactive sessions; nothing treats the agent as a background daemon.
3. **Learning-driven design quality.** Less a feature than a stance: because the goal is understanding, we will tend to choose the *clearer* design over the *flashier* one, and write the system so its decisions are inspectable. This shows up in logging, tracing, and how playbooks are encoded.

What is **not** a differentiator but we'll still implement (just not market):

- Tiered model routing — `pydantic-ai-harness` and others already do this.
- Subagent isolation — Claude Code, `pydantic-deepagents`, others already do this.

## v1 scope — what we actually ship first

The smallest thing that proves the thesis and gets us to a foundation A2A can be built on.

1. **CLI surface only.** No browser, no API, no SDK.
2. **Single visible coworker (Gru)** with the ability to spawn one minion at a time.
3. **HITL mode only**, with per-tool approval prompts.
4. **Session + project long-term memory.** User long-term deferred.
5. **Basic tool set:** read, write, edit, grep, shell. Shell unsandboxed in v1 (HITL provides the safety net).
6. **Pydantic AI** as the agent framework, multi-provider.
7. **CodeMode** for filesystem-heavy operations (via Monty if integration is straightforward; otherwise local subprocess).
8. **Hand-rolled playbook library** for the Minion Factory — start with 2-3 playbooks, not a full taxonomy.
9. **Stuck-loop detection** if it can land cheaply; otherwise v2.
10. **Tracing/logging from day one** — every model call, tool call, minion spawn, and memory write logged structurally. Use Pydantic Logfire.

## v2+ — explicitly deferred

- **A2A interop** (the headline differentiator — but needs a stable v1 first).
- **Night Shift / cron scheduling.**
- **YOLO mode** with native sandboxing (`sandbox-exec` / `bwrap`).
- **User-tier long-term memory** + Predict-Calibrate extraction.
- **Browser / API / SDK surfaces.**
- **Agent-authored skills.**
- **Orphan tool-call repair.**
- **LLM-driven minion spec generation** if playbook library hits limits.

## Open questions

- Minion Factory: how far does a static playbook library scale before LLM-driven spec generation pays off?
- Memory writes: triggered by minion at session-end, or opportunistic during session, or both?
- Tool approval granularity: per-tool, per-tool+args, per-risk-level?
- Minion run logging: what's the right schema for debuggability without bloat?
- A2A protocol: which spec to follow? (Anthropic's MCP is agent-to-tool, not agent-to-agent. The open A2A protocol is in flux.)
- Conflict resolution when two A2A-connected JAC instances disagree?
- Where do "skills" live and how are they versioned across projects?

## References / inspirations

Cloned locally to `~/Projects/personal/JAC-research/` for ongoing reference.

- [pydantic/monty](https://github.com/pydantic/monty) — Rust-based Python sandbox.
- [pydantic/pydantic-ai-harness](https://github.com/pydantic/pydantic-ai-harness) — official Pydantic AI harness primitives; source of CodeMode.
- [vstorm-co/pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents) — full Pydantic AI coworker; source of stuck-loop detection and orphan repair.
- [vstorm-co/pydantic-ai-backend](https://github.com/vstorm-co/pydantic-ai-backend) — execution/tool backends.
- [vstorm-co/memv](https://github.com/vstorm-co/memv) — Predict-Calibrate memory.
- [earendil-works/pi](https://github.com/earendil-works/pi) — multi-surface agent ecosystem; source of agent-authored skills.
- NVIDIA OpenShell — agent governance via Landlock + Docker. Watch for v2.
- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic: Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)

## Next planning steps

One topic at a time:

1. Lock the Minion Factory interface (playbook format + spec schema).
2. Design the memory write/read flow concretely.
3. Define the first 2-3 minion templates as actual prompts.
4. Tool approval policy table.
5. Tracing/logging schema.
6. Scaffold the v1 CLI.
