# Sessions & memory

> **Audience:** users who need continuity across days and durable facts Gru should remember.

## Where state lives: projects vs. the global workspace

JAC has two workspaces:

- **User workspace** `~/.jac/` — your global config, profiles, user-scope memory, prompts, skills.
- **Project workspace** `<root>/.agents/` — sessions, project-scope memory, usage log, tool-result cache, and A2A state for one project.

**A folder is a "project" if it has a `.git` or a `.agents/` directory** at or above the current directory. `.git` is the obvious case; `.agents/` is the explicit opt-in for non-git folders (created by `jac init`).

When you run `jac` **outside any project** (no `.git`, no `.agents/`), JAC runs in **loose mode**: sessions and usage are written to the *global* user workspace (`~/.jac/sessions/`, `~/.jac/usage.jsonl`) instead of dropping a `.agents/` folder into an unrelated directory. The REPL prints a one-line `workspace: global` notice so you know. Run `jac init` to make the current folder a project (it offers to create `.agents/`).

Project-scope **memory** is stricter: `remember`/`forget` with `scope="project"` *refuse* outside a project rather than fall back — there's no repo for the fact to be "about". Use `scope="user"` for cross-project facts.

## Sessions

A **session** is one conversation thread. State lives under:

```text
<state-root>/sessions/<timestamp>/
├── messages.json    # full pydantic-ai message history
├── plan.json        # checklist (D27) — restored on resume
└── compacted/       # archived slices after auto-compaction
```

`<state-root>` is `<root>/.agents` in a project, or `~/.jac` in loose mode. Session ids are timestamps like `2026-05-24T14-30-00` (filesystem-safe, sortable).

### Create and resume

| Action | CLI | REPL slash |
| --- | --- | --- |
| New session | `jac` | `/clear` |
| Latest session | `jac --resume` | `/resume` |
| Specific session | `jac --session ID` | `/resume ID` |
| List sessions | `jac sessions` | `/sessions` |

The listing shows each session id, its message count, and a human-readable creation time (oldest → newest), with the most recent marked `(latest)`.

After each **completed** turn, JAC rewrites `messages.json` atomically (tempfile + rename), so a kill mid-write can't corrupt the file and a mid-turn crash keeps prior turns intact.

Fail-first: `--resume` with no sessions, or unknown `--session` id, raises a clear error.

### Delete and prune

Sessions accumulate indefinitely; clean them up with:

| Action | CLI | REPL slash |
| --- | --- | --- |
| Delete one | `jac sessions delete <id>` | `/sessions delete <id>` |
| Prune by age | `jac sessions prune --older-than 30d` | `/sessions prune 30d [yes]` |

Durations are `<n>w` / `<n>d` / `<n>h` (weeks/days/hours). Prune deletes sessions whose **creation time** (from the timestamp id) is older than the cutoff; hand-renamed sessions whose id isn't a timestamp are skipped, never deleted.

Both are confirmed before acting: the CLI prompts (`--yes` / `-y` to skip); the in-REPL forms refuse to touch the **active** session, and `/sessions prune <dur>` previews what would go — append `yes` to actually delete. Deleting a session removes its directory but **leaves `usage.jsonl` intact** — those tokens were spent and still count toward the `project_total` budget.

### Plan on resume

If `plan.json` exists, steps reload into Gru's plan tools. Steps marked `in_progress` become `pending` (the actor was interrupted). Corrupt `plan.json` warns in yellow but does not block resume.

### Context compaction

Long sessions trigger automatic history compaction (see [Configuration](configuration.md)). Old message slices may be summarized and stored under `compacted/`. At **refuse** threshold the REPL blocks new input until you `/clear` or raise limits.

## The 2×2 memory matrix

JAC loads four context sources into Gru's instructions every run:

|  | **User scope** (all projects) | **Project scope** (this repo) |
| --- | --- | --- |
| **User-authored** (JAC never writes) | `~/.jac/AGENTS.md` | `<repo>/AGENTS.md` |
| **JAC-managed** (via tools, HITL) | `~/.jac/memory.md` | `<repo>/.agents/memory.md` |

Load order in the prompt: user AGENTS → user memory → project AGENTS → project memory (newest facts last).

**Sub-agents (minions) get a narrower slice:** only `AGENTS.md` (user + project), so a spawned worker respects the same repo conventions and safety rules Gru does. They do **not** receive the JAC-managed `memory.md` files (memory grows unbounded and is often irrelevant to a bounded task — if a specific fact matters, Gru puts it in the task packet) or the parent conversation history (isolation is the point of delegation). When the session has bidirectional comms enabled, a minion can pull a missing fact from Gru via `ask_main_agent`.

### AGENTS.md

Community convention files you edit yourself. JAC **never** modifies them. Use for stable project or personal instructions.

### memory.md

JAC-owned Markdown with five sections:

- Conventions
- Facts
- Preferences
- Gotchas
- Decisions

Bootstrapped on first `remember` call. You may edit files by hand; JAC preserves manual edits and appends new bullets.

## `remember` and `forget`

Both tools require approval. Every call must include:

- **`reason`** — shown in the approval panel
- **`content`** — the fact (one bullet's worth of text)
- **`scope`** — required, no default:
  - `user` — cross-project (`~/.jac/memory.md`)
  - `project` — this repo only (`<repo>/.agents/memory.md`); **fails outside a project**
- **`category`** — for `remember` only: `convention` | `fact` | `preference` | `gotcha` | `decision`

### Editing memory yourself

You can curate memory two ways:

- **Ask Gru** — "remember that we use uv, not pip" / "forget that convention". Gru calls the `remember`/`forget` tools; each call is HITL-approved.
- **Slash commands** — edit it directly, no model call, the typed command is the approval:

  ```text
  /remember <user|project> <category> <text>
  /forget   <user|project> <exact text>
  ```

  e.g. `/remember project convention uses uv, not pip`. Use [`/memory`](#viewing-memory-memory) to see the exact text of an entry before `/forget`.

Either way, writes are one audited bullet at a time in the fixed schema — JAC never rewrites the file wholesale. You can also hand-edit `memory.md`; manual edits are preserved.

### Audit trail

Each entry includes an HTML comment:

```html
<!-- jac: 2026-05-24T12-00-00 session: 2026-05-24T10-00-00 -->
```

The timestamp and session id both use the filesystem-safe `YYYY-MM-DDTHH-MM-SS` form (dashes, no colons). When no session is active (headless scripts, tests) the `session:` field is omitted.

### De-duplication

Exact-normalized duplicate content in the same section is rejected with feedback to Gru.

### Size hint

Past ~25 bullets in one section, `remember` adds a soft "consider pruning" message — no automatic deletion.

### `forget`

Removes one line by exact-normalized match. Zero or multiple matches → error with guidance to narrow `content`.

### Viewing memory (`/memory`)

`/memory` prints stored entries grouped by section, for both scopes (`/memory user` or `/memory project` narrows to one). Entries are shown with their audit comments stripped, so you can copy the exact prose back into a `forget` request. Removal still goes through Gru's HITL-approved `forget` tool — `/memory` itself is read-only.

## Session id in tools

The REPL sets a context variable with the active session id so `remember` can stamp audit comments without passing session objects through every tool.

## Token usage log

Per-turn token counts append to `<repo>/.agents/usage.jsonl` for project-wide budgets. See [Configuration](configuration.md) (`budget.project_total_tokens`).

## A2A vs host sessions

Inbound A2A calls use **separate** `context_id` storage under `<repo>/.agents/a2a/contexts/` — not the interactive REPL session's `messages.json`. See [A2A operator](a2a-operator.md).

## Related

- [Getting started](getting-started.md)
- [CLI reference](cli-reference.md) — tool signatures
- [Examples](examples.md) — remember/forget scenarios
