# Version snapshot template

> **Purpose:** a single-page, end-to-end view of one shipped version of JAC — install it, set it up, exercise every shipped feature with concrete prompts, and run graduated benchmarks to surface real-world breakages.
>
> **Audience:** anyone who picked up JAC at version `X.Y.Z` and wants a *self-contained* tour without bouncing between five guide pages.
>
> **How to use this template:** copy this file to `vX.Y.Z.md` on release day. Fill every section. **Replace** placeholders — don't append. One snapshot per release; if a version doesn't change a section, copy the prior text verbatim so the snapshot stays self-contained.

---

## 0. Snapshot metadata

| Field | Value |
| --- | --- |
| **Version** | `vX.Y.Z` |
| **Release date** | `YYYY-MM-DD` |
| **Status** | shipped / pre-release / dev |
| **Headline** | one-sentence description of what's new in this version |
| **Previous snapshot** | link to `vX.Y.(Z-1).md` or `—` for first |
| **Test count at release** | `N tests passing` |
| **Companion docs** | [Changelog](../changelog.md) · [Progress](../progress.md) · [Architecture](../architecture.md) |

---

## 1. Install

Full install commands valid for *this* version. Don't link out — paste the working block.

```bash
# Persistent install
uv tool install "git+https://github.com/VENKATESHWARAN-R/JAC.git@vX.Y.Z"

# One-off
uv run --from "git+https://github.com/VENKATESHWARAN-R/JAC.git@vX.Y.Z" jac --help

# Local clone
git clone https://github.com/VENKATESHWARAN-R/JAC.git && cd JAC
git checkout vX.Y.Z
uv sync
uv run jac --help
```

**Requirements:** Python 3.13+, macOS/Linux, `uv`.

---

## 2. First-time setup

The exact commands a fresh user runs to get from zero to a working REPL. No prose — just the path.

```bash
jac init                      # wizard: secrets backend → profile → API key
jac profiles                  # confirm profile exists, marked default
jac keys                      # confirm required keys are stored / present
cd <some-git-repo>
jac                           # first session
```

Expected: greeting prints model id + session id, status bar shows `profile:… tier:… ctx:0%/200k`.

---

## 3. Feature inventory

A truth table for what's in this version. One row per user-visible feature. **Status:** ✅ shipped · ⚠️ partial · 🚫 dropped · ⏸ deferred.

| Area | Feature | Status | Where to evaluate |
| --- | --- | --- | --- |
| CLI | Interactive REPL | ✅ | §4.1 |
| CLI | Slash commands | ✅ | §4.2 |
| Profiles | Multi-profile + tiers | ✅ | §5 |
| Profiles | Secrets backend (keyring/dotenv/env-only) | ✅ | §5 |
| Session | Persistence + `--resume` | ✅ | §6 |
| Session | Plan checklist persistence | ✅ | §6 |
| Memory | `remember` / `forget` (user + project) | ✅ | §7 |
| Memory | Auto-loaded `AGENTS.md` (user + project) | ✅ | §7 |
| Tools | Filesystem (read/write/edit/list) | ✅ | §8 |
| Tools | Search (grep/glob) | ✅ | §8 |
| Tools | Shell + background processes | ✅ | §8 |
| Tools | Web (search/fetch) | ✅ | §8 |
| Tools | Plan + clarify | ✅ | §8 |
| HITL | Approval flow with `reason:` enforcement | ✅ | §9 |
| Cost | Tool-result post-processor | ✅ | §10 |
| Cost | Prompt-cache stability | ✅ | §10 |
| Cost | `/tokens` breakdown + budgets | ✅ | §10 |
| Sub-agents | Sequential `spawn_sub_agent` | ✅ | §11 |
| Sub-agents | Parallel `spawn_sub_agents` | ✅ | §11 |
| Sub-agents | Bidirectional ask/respond | ✅ | §11 |
| Skills | `load_skill` + community format | ✅ | §12 |
| A2A | Inbound guest server | ✅ | §13 |
| A2A | Outbound `a2a_call` + file transfer | ✅ | §13 |
| Compaction | Token-budget-aware auto-compaction | ✅ | §14 |
| Observability | Logfire tracing | ✅ | §15 |

---

## 4. Quick smoke test

A 60-second sanity check after install. If any step fails, stop and investigate before running benchmarks.

```bash
jac --help                                  # CLI loads
jac profiles                                 # at least one profile, default marked
jac keys                                     # required keys present (✓ next to each)
cd <some-git-repo> && jac                    # REPL launches, greeting visible
```

In the REPL:

```text
» Hi, what tools do you have?               # tests model call + system prompt
/tokens                                      # tests usage tracker
/exit
```

---

## 5–14. Feature-by-feature evaluation

> Each section follows the same shape:
>
> 1. **What it is** — one sentence.
> 2. **Setup** — anything required beyond the install above.
> 3. **Sample prompts** — copy-pasteable, escalating from trivial to "I trust it".
> 4. **What success looks like** — observable signals (panels, tool calls, output shape).
> 5. **Common breakages** — what to watch for that indicates a regression.

(Replicate this shape for each feature in the inventory.)

### Template subsection

#### 5.1 Feature name

**What it is.** One sentence describing the user-facing capability.

**Setup.** Anything required beyond `jac init`. "None" if vanilla.

**Sample prompts.**

```text
» <trivial prompt>
» <typical prompt>
» <edge-case prompt>
```

**What success looks like.**
- Specific tool call signature appears.
- Specific renderer panel appears (color / title).
- Specific file lands on disk at known path.

**Common breakages.**
- Missing approval panel where one is expected.
- Tool returns raw bytes instead of summarized form (when summarizable).
- Silent fallback to default model (should be `JacConfigError`).

---

## 15. Benchmark prompts (graduated complexity)

A standardized prompt set for any operator to **stress-test this version of JAC** end-to-end. Save the transcripts; compare across releases. The intent isn't a perfect score — it's to **surface what breaks, what's expensive, and what's never used**.

For each benchmark, capture:

- **Pass?** y/n with one-line note
- **Time** wall clock to final answer
- **`/tokens` snapshot** (in/out/total, cache hit rate, summarize savings, sub-agent breakdown)
- **Anything surprising** (unexpected tool, missing approval, runaway loop)

### L1 — Trivial (single tool, no reasoning)

Purpose: prove tools wire up, approval flow renders, system prompt loads.

```text
» Read the first 30 lines of pyproject.toml and tell me the project name and Python version.
» List the top-level directories under src/ (exclude hidden).
» Grep for "TODO" in src/ and report counts per file.
```

### L2 — Simple (multi-tool, short reasoning)

Purpose: exercise tool composition + small reasoning + memory write path.

```text
» Give me a 3-bullet overview of what this repo does. After answering, remember "this repo is X" as a project fact.
» Find the file that defines the main CLI entrypoint, read it, and summarize the registered subcommands.
» Save my preference: "always use uv run, never bare python" — scope=user.
```

### L3 — Medium (cross-file synthesis + plan)

Purpose: exercise `plan` / `update_plan`, multi-read, clarify under ambiguity.

```text
» Map every public tool Gru exposes to the file where it's defined. Use the plan tool to track progress and produce a table at the end.
» This codebase has a "capability" pattern. Pick three example capabilities, read them, and explain the contract they share.
» I want to add a new slash command called /hello that prints a greeting. Walk me through what I'd need to change, but don't make any edits yet.
```

### L4 — Complex (edit + verify + HITL)

Purpose: exercise `edit_file`, `run_shell`, approval gates, verify-change loop.

```text
» Add a docstring to the main() function in src/jac/cli/app.py describing what it does. Run `just check` after to confirm no regressions.
» Fix any ruff warnings in src/jac/tools/. Run `just fix` first; only apply manual edits for things ruff can't auto-fix.
» Add a single pytest test that asserts `jac --version` exits 0. Place it in tests/ following the existing naming convention.
```

### L5 — Parallel sub-agents (`spawn_sub_agents` fan-out)

Purpose: exercise the *flagship* parallel path — multi-spawn approval table, per-spawn lifecycle events, cost rollup, gather concurrency.

```text
» For each Phase A/B/D/E user-facing feature, find the docs page and write a one-paragraph summary. Use spawn_sub_agents with one worker per phase so we don't blow the main context.
» Read the three reference skills under src/jac/data/skills/ in parallel sub-agents and tell me which one's body is largest and why.
» Compare how src/jac/tools/filesystem.py, src/jac/tools/search.py, and src/jac/tools/shell.py each register their tools. Spawn one minion per file; aggregate the findings.
```

Watch for: per-spawn approval table renders (not a JSON dump), `▶ minion-N` start panels appear *as workers begin* (not all at once at the end), `/tokens` shows a populated `sub_agents:` line, depth cap holds (no minion calling spawn_*).

### L6 — Bidirectional sub-agent (ask/respond)

Purpose: exercise `ask_main_agent` / `respond_to_sub_agent`, round-trip cap, /spawns visibility.

```text
» Use spawn_sub_agent to draft a new section for docs/user-guide/examples.md about A2A. Tell the minion to ASK ME if it's unsure about the section title or tone before finalizing.
» Spawn a minion to triage which tests in tests/ are flakiest. If it needs my judgement on what "flaky" means in this repo, it should ask via ask_main_agent.
```

Watch for: yellow "question from minion-N" panel appears, your reply renders as a cyan panel back, `/spawns` lists the parked spawn while it's waiting, status bar shows `spawns:1`, after 5 round-trips the 6th ask produces a graceful "finalize" directive rather than an error.

### L7 — Skills (`load_skill` + recommended playbooks)

Purpose: exercise discovery, in-prompt visibility, manual `/skill use`, and shadow detection.

```text
» Review my last commit using the code-review skill.
» /skill list                              ← visual check: 3 reference skills shown
» /skill use verify-change                 ← injects the body as a user turn
» Summarize src/jac/cli/app.py — use the summarize-large-files skill if it fits.
```

Then author and test a custom skill:

```bash
mkdir -p .agents/skills/repo-tour
cat > .agents/skills/repo-tour/SKILL.md <<'EOF'
---
name: repo-tour
description: 5-minute tour of this repo for new contributors.
---
# Repo tour
1. Read README.md and CLAUDE.md.
2. List src/jac/ and identify top-level subpackages.
3. Read docs/architecture.md §5 (locked decisions).
4. Output a labeled tour as a numbered list.
EOF
```

```text
» /skill reload         ← should show "+1 new (repo-tour)"
» Give me the repo tour.
```

Watch for: Gru actually calls `load_skill(name="repo-tour")` rather than improvising; the body comes back as a tool message.

### L8 — Cost levers (post-processor + cache + budget)

Purpose: exercise summarization gate, prompt cache, `/budget` and `/budget extend`.

```text
» Fetch https://docs.pydantic.dev/latest/concepts/types/ and summarize the type categories.
   ↑ exercises summarizable=True on fetch_url
» Run `uv run python -m pytest tests/ -x -q` and tell me the first failure (if any).
   ↑ exercises summarizable=True on run_shell when output is large
» /tokens                                                ← should show summarize: line if anything fired
» /budget                                                 ← visible only if a budget is configured
```

Set a tiny budget in `.agents/config.yaml` to *force* the warn and hardstop paths:

```yaml
budget:
  session_total_tokens: 50000
  warn_pct: 50
  hardstop_pct: 90
```

Then run a long prompt; you should see one warning event near 25k, hardstop at 45k, and `/budget extend 100000` should unblock it.

### L9 — A2A (loopback)

Purpose: exercise inbound server, bearer auth, outbound `a2a_call`, file transfer.

Terminal A:

```text
/a2a serve
/a2a token                    # copy the token
```

Terminal B (separate `jac` session):

```text
/a2a peer add self http://127.0.0.1:8001 --bearer
  bearer token: <paste>
» Use a2a_call on peer self to ask: "What's the entrypoint of this codebase?"
» Use a2a_call on peer self with files=["./README.md"] and ask: "Summarize this file."
```

Watch for: `[a2a out →]` / `[a2a out ✓]` panels, `_jac_saved_files` populates when peer returns files, `/a2a status` shows the inbound call in the last-5 list, `inbound.jsonl` gains a new line, `tokens_used` is non-zero.

### L10 — Real-world workflow (the actual job)

Purpose: integration test for *everything together*. Use the actual coworker scenario — fix a real-ish bug.

```text
» We need to add a top-level `jac doctor` subcommand that prints:
  - jac version
  - active profile + tier + model
  - secrets backend
  - count of stored sessions in cwd (if in a git repo)
  - count of loaded skills
  Plan it first using the plan tool. Spawn a sub-agent to summarize how existing
  subcommands are wired in src/jac/cli/. Then implement, run `just check`, and stop
  for my approval before writing files. If you need design input from me at any
  point — naming, output format — ask via ask_main_agent from inside the sub-agent.
```

This single prompt exercises: `plan`, `spawn_sub_agent` (tier=medium), `ask_main_agent` (probably), `read_file`, `write_file`, `edit_file`, `run_shell`, HITL approval, post-processor (on `just check` output), memory (if Gru offers to remember a design decision), and `/tokens` accounting across all of the above. Save the full transcript.

---

## 16. Coverage matrix

After running benchmarks, fill this in. Cells that stay empty for two releases in a row are candidates for **deletion** — if no one ever uses a tool, it shouldn't be paying for its own maintenance.

| Feature | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `read_file` |  |  |  |  |  |  |  |  |  |  |
| `write_file` |  |  |  |  |  |  |  |  |  |  |
| `edit_file` |  |  |  |  |  |  |  |  |  |  |
| `grep` / `glob` |  |  |  |  |  |  |  |  |  |  |
| `run_shell` |  |  |  |  |  |  |  |  |  |  |
| `web_search` / `fetch_url` |  |  |  |  |  |  |  |  |  |  |
| `remember` / `forget` |  |  |  |  |  |  |  |  |  |  |
| `plan` / `update_plan` |  |  |  |  |  |  |  |  |  |  |
| `clarify` |  |  |  |  |  |  |  |  |  |  |
| `start_process` / `tail_process` |  |  |  |  |  |  |  |  |  |  |
| `spawn_sub_agent` |  |  |  |  |  |  |  |  |  |  |
| `spawn_sub_agents` |  |  |  |  |  |  |  |  |  |  |
| `ask_main_agent` |  |  |  |  |  |  |  |  |  |  |
| `load_skill` |  |  |  |  |  |  |  |  |  |  |
| `a2a_discover` / `a2a_call` |  |  |  |  |  |  |  |  |  |  |
| Post-processor fired |  |  |  |  |  |  |  |  |  |  |
| Auto-compaction fired |  |  |  |  |  |  |  |  |  |  |

---

## 17. Sign-off checklist

Before promoting this snapshot to "released":

- [ ] L1–L4 all pass
- [ ] L5 (parallel) passes with per-spawn lifecycle events visible in real time
- [ ] L6 (bidirectional) reaches a graceful finalize on the 6th ask
- [ ] L7 includes at least one custom skill loaded via `/skill reload`
- [ ] L8 has a non-empty `summarize:` line on `/tokens`
- [ ] L9 has both inbound and outbound `[a2a]` panels visible in the transcripts
- [ ] L10 transcript saved; total token cost recorded; no silent fallback errors
- [ ] Coverage matrix populated; tools with empty rows flagged for next snapshot's review
