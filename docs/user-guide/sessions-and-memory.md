# Sessions & memory

> **Audience:** users who need continuity across days and durable facts Gru should remember.

## Sessions

A **session** is one conversation thread in a git project. State lives under:

```text
<repo>/.agents/sessions/<timestamp>/
├── messages.json    # full pydantic-ai message history
├── plan.json        # checklist (D27) — restored on resume
└── compacted/       # archived slices after auto-compaction
```

Session ids are timestamps like `2026-05-24T14-30-00` (filesystem-safe, sortable).

### Create and resume

| Action | CLI | REPL slash |
| --- | --- | --- |
| New session | `jac` | `/clear` |
| Latest session | `jac --resume` | `/resume` |
| Specific session | `jac --session ID` | `/resume ID` |
| List ids | `jac sessions` | `/sessions` |

After each **completed** turn, JAC rewrites `messages.json`. Mid-turn crashes keep prior turns.

Fail-first: `--resume` with no sessions, or unknown `--session` id, raises a clear error.

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
  - `project` — this repo only (`<repo>/.agents/memory.md`); **fails outside a git repo**
- **`category`** — for `remember` only: `convention` | `fact` | `preference` | `gotcha` | `decision`

### Audit trail

Each entry includes an HTML comment:

```html
<!-- jac: 2026-05-24T12:00:00 session: 2026-05-24T10-00-00 -->
```

### De-duplication

Exact-normalized duplicate content in the same section is rejected with feedback to Gru.

### Size hint

Past ~25 bullets in one section, `remember` adds a soft "consider pruning" message — no automatic deletion.

### `forget`

Removes one line by exact-normalized match. Zero or multiple matches → error with guidance to narrow `content`.

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
