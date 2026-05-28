# Skills

> **Audience:** anyone who wants Gru to follow a consistent playbook for recurring tasks — code reviews, large-file summarization, change verification — without re-explaining the rules every session.

A **skill** is a markdown playbook with YAML frontmatter that Gru loads on demand. Skills are **advice, not execution** — they don't give Gru new tools or new permissions; they give it instructions for *how* to use the tools it already has. Think of them as named, on-disk prompts that the model can pull into context when the user's request matches.

## Why skills

Three problems that skills solve:

1. **Repeating yourself.** "Always check `just check` before declaring done" should not be something you say every session. Put it in a `verify-change` skill and Gru reads it once when relevant.
2. **Project-specific conventions.** Your team's code review checklist isn't anyone else's. A project-local `code-review` skill encodes it once and everyone who uses JAC on the repo gets the same review.
3. **Discoverability.** Gru sees the loaded skills' names and descriptions in its system prompt. When a user asks "review my diff", it knows to call `load_skill(reason=..., name="code-review")` rather than improvising.

Skills follow the [Anthropic community skill format](https://github.com/pydantic/skills): one folder per skill, one `SKILL.md` file inside it, YAML frontmatter on top, markdown body below.

## Where skills live

JAC scans **all three** locations on every session start (and on `/skill reload`). Skills with different names from different sources **all load together** — the project dir doesn't replace the user dir, the user dir doesn't replace the package. The three sources only interact when two of them define a skill with the **same name**, in which case the higher-priority one wins and the lower-priority one is reported as **shadowed** (not silently dropped).

| Priority | Path | Use it for |
| --- | --- | --- |
| 1 (highest) | `<repo>/.agents/skills/<name>/SKILL.md` | Project-specific playbooks. Commit alongside the code they relate to. |
| 2 | `~/.jac/skills/<name>/SKILL.md` | Personal cross-project skills (your own review style, your favourite sub-agent recipes). |
| 3 (lowest) | shipped reference skills inside the JAC package | Defaults JAC ships with. See **Reference skills** below. |

### Concrete example

If you have these on disk:

```
~/.jac/skills/code-review/SKILL.md          # your personal review style
~/.jac/skills/release-notes/SKILL.md        # used across many repos
<repo>/.agents/skills/code-review/SKILL.md  # the repo's tuned review checklist
<repo>/.agents/skills/changelog/SKILL.md    # repo-specific
# (package ships: code-review, summarize-large-files, verify-change)
```

Then Gru sees **five active skills**: `code-review` (project), `release-notes` (user), `changelog` (project), `summarize-large-files` (package), `verify-change` (package). Two `code-review` entries are shadowed — the user one and the package one — and both are listed under "Skills (shadowed)" in `/skill list` so you can see what got overridden.

Each skill folder must contain a `SKILL.md` whose `name:` frontmatter field exactly matches the folder name. Mismatches are skipped with a warning at load time.

## Skill format

```markdown
---
name: my-skill
description: One-sentence summary of when Gru should load this. Shown in the system prompt.
tools_required:
  - run_shell
  - read_file
---

# My skill

The body is free-form markdown. Imperative voice works best — you're
writing instructions Gru will follow, not documentation Gru will
quote. Examples and concrete commands beat abstractions.
```

### Frontmatter fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Lowercase letters, digits, hyphens. Must match the folder name. |
| `description` | yes | One-line summary. **Soft cap of 200 chars** — longer descriptions log a warning because they eat into the 2 KB system-prompt budget faster. |
| `tools_required` | no | Informational list of tool names the body mentions. **Does not gate loading** — the skill loads regardless. Shown in `/skill list`; surfaced to A2A peers as tags. |

The body is whatever markdown you want. JAC doesn't parse or validate it — it just hands it to Gru as the result of `load_skill`.

## How Gru sees skills

Loaded skills surface in two places:

1. **System prompt.** Gru sees a `# Skills` block listing every loaded skill's name + description (up to a 2 KB cap; longer lists fall back to name-only). This is what tells the model "you can call `load_skill(name='code-review')` if the user wants a review."
2. **Tool result.** When Gru calls `load_skill(reason, name)`, the full body comes back as the tool's return value and lands in conversation history as a tool message. Gru reads it like any other tool result, then acts on its guidance.

The 2 KB cap exists to keep the prompt prefix small and cache-friendly. If you install dozens of skills, only the names are listed in-prompt — descriptions are still visible via `/skill list`. The cap is a constant in the source today (`_INSTRUCTIONS_CAP_BYTES`), not a setting.

## Slash commands

| Command | What it does |
| --- | --- |
| `/skill list` | Prints two tables: **Skills (active)** with name, source, description, required tools — and (when any exist) **Skills (shadowed)** showing entries that lost a name collision, including the path so you can see exactly which file was overridden. |
| `/skill use NAME` | Manually inject a skill's body as the next user message. Equivalent to forcing `load_skill` from the user side; useful when you want Gru to follow a specific playbook for the *next* request. |
| `/skill reload` | Re-scan all three skill directories. Use it while authoring a skill so edits show up without restarting Gru. Reports added / removed / unchanged counts. |

## Authoring a skill

The shortest path to a working skill:

```bash
mkdir -p .agents/skills/my-checklist
cat > .agents/skills/my-checklist/SKILL.md <<'EOF'
---
name: my-checklist
description: My team's pre-merge checklist.
---

# Pre-merge checklist

1. `just check` is green.
2. CHANGELOG.md has an entry.
3. The PR description mentions any breaking change.
EOF
```

Then in your JAC session:

```
» /skill reload
✓ reloaded — 4 skill(s) (+1 new, -0 removed, 3 unchanged)
  + my-checklist

» can you walk me through my pre-merge checklist?
```

Gru will see `my-checklist` in its system prompt, recognize the match, call `load_skill(name="my-checklist")`, and follow the body.

### Tips for good skills

- **Be imperative.** "Run `just check` before declaring done" beats "It is recommended to run the checks."
- **Show, don't describe.** A concrete tool-call example is worth a paragraph of prose.
- **Cite tools by name.** When the body mentions `spawn_sub_agent`, `run_shell`, etc., Gru maps that back to its available tools immediately.
- **Keep descriptions tight.** The frontmatter `description` is what convinces Gru to load the skill — be specific about *when* to use it ("when the user asks for a review", "after editing code") rather than what the skill is.

## Reference skills

JAC ships four reference skills you can use as templates or tune for your own repo. They live under `src/jac/data/skills/` in the installed package; override them by creating a skill with the same name under `~/.jac/skills/` or `<repo>/.agents/skills/`.

| Name | When to use |
| --- | --- |
| `code-review` | Reviewing a diff or branch before it ships. Lays out the five-question filter and recommends per-file sub-agent delegation for large diffs. |
| `summarize-large-files` | Reading a file that would otherwise blow up your context. Pushes the model toward `spawn_sub_agent(tier="small")` rather than pulling 50k tokens into the main loop. |
| `verify-change` | Running the project's check suite after an edit. References `just check` and shows how to structure verification steps in a sub-agent's `success_criteria`. |
| `jac-cli` | Composing JAC CLI invocations and REPL slash commands — flag combinations, A2A peer auth shapes (bearer / API key / OAuth2), budget kinds. Loaded when the user asks "how do I run X in JAC". Kept in sync with [CLI reference](cli-reference.md) per CLAUDE.md policy. |

Run `/skill list` to see them in your session.

## How skills interact with other features

| Feature | Interaction |
| --- | --- |
| **Sub-agents (Phase B)** | Skills are read by the *main* agent. They commonly recommend `spawn_sub_agent(...)` for context-heavy steps. Sub-agents themselves don't load skills today — the playbook is delivered via `task_packet.objective`. |
| **Post-flight verification** | The `verify-change` reference skill demonstrates the pattern: skill body suggests putting check steps in `success_criteria` so the sub-agent self-verifies before returning. (Phase C deterministic hooks were considered and dropped — D37.) |
| **A2A AgentCard** | Every loaded skill is published as an additional A2A `Skill` entry on the guest server's card (under `id: jac-skill-<name>`). Peers calling `/.well-known/agent-card.json` can discover what playbooks your JAC has loaded. Refresh requires a server restart (`/a2a stop && /a2a serve`). |
| **Prompt cache** | The Skills system-prompt block is stable as long as your on-disk skills are stable, so it stays inside the cache prefix. `/skill reload` changes the block content; the next turn pays one cache-miss to seat the new prefix. |

## When **not** to use skills

- **One-off instructions.** If you're going to say it once, just say it. Skills are for repeated guidance.
- **Anything that needs runtime behavior.** Skills don't add tools, don't gate permissions, don't run code. If you need execution, build a tool (or a capability).
- **Sensitive content.** Skill bodies land in conversation history when loaded. Don't put secrets in them.
- **Auto-triggers.** Skills don't auto-fire on a pattern match. Gru decides whether to load one based on the description in its system prompt. If you need deterministic gating, that's a hook or a slash command, not a skill.
