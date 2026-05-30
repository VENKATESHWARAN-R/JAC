---
name: summarize-large-files
description: Read and summarize files that would otherwise blow up your context. Use before answering questions about a large file, or when the user asks for an overview of a module you haven't read yet.
tools_required:
  - spawn_sub_agent
  - read_file
  - list_dir
---

# Summarizing large files without burning context

The trap: you `read_file` something big, the full content lands in your context, every subsequent turn re-processes those tokens, and a quick "what does this module do?" turns into a 50k-token tax on the rest of the session.

The fix: **delegate the read to a small-tier sub-agent.** The intermediate tokens (the full file) stay in the sub-agent's context. Only the summary returns to you.

## When to delegate vs read directly

| File size (rough) | Action |
| --- | --- |
| < 500 lines | Read directly with `read_file`. Cost is fine. |
| 500–2000 lines | Read directly **only if** you need exact line numbers or the structure matters for what you're doing. Otherwise delegate. |
| > 2000 lines, or any file you'd "skim for the gist" | **Delegate.** Don't pull it in. |
| Whole directory tour (e.g. "what's in `src/foo/`") | Always delegate. Single sub-agent reads many files; you get one summary back. |

You can also delegate **even for small files** when you genuinely don't need the content — only an answer. Example: "Does this file use the old API?" doesn't need the file in your context; it needs a yes/no.

## How to spawn

```python
spawn_sub_agent(
  reason="summarize <file> without keeping its full content in main-agent context",
  task_summary="Read <file> and return a 3-paragraph summary covering: (1) what it does, (2) the public surface, (3) anything surprising or non-obvious.",
  tier="small",
  task_packet=SubAgentTaskPacket(
    objective="Read the file and produce a structured summary.",
    success_criteria="Returns a markdown summary with three labeled sections.",
    relevant_paths=["<file>"],
    allowed_tools=["read_file"],
    expected_output="Markdown — three short paragraphs, no code blocks unless flagging a surprise.",
    max_turns=3,
  ),
)
```

Notes:

- `tier="small"` — summarization is cheap reasoning; don't pay large-tier prices for it.
- `allowed_tools=["read_file"]` — restricts the sub-agent's toolset (enforced at the Agent layer) so it can't go exploring with `write_file`/`run_shell`/etc.; you asked for one file's summary, not a deep dive. A small always-allowed control-plane set (`read_file`, `ask_main_agent`) stays available regardless.
- `max_turns=3` — generous: one to read, one to draft, one for retries. If the summary takes more turns than that, something's off with the request.

## How to use the result

The sub-agent returns prose. **Don't paraphrase it into your own words just to feel useful** — the user asked for an answer, not for a re-summary of a summary. Quote or paste the sub-agent's output as-is, then add only what's needed to answer the actual question.

If the user follows up with "okay, now show me the function that does X" — read just that function with `read_file` using line offsets. Don't re-read the whole file.

## Multi-file overview

For "what's in this directory?":

1. `list_dir` first to enumerate.
2. Spawn one sub-agent per file you actually need (or one sub-agent that reads several, if you only need the gist).
3. Stitch the per-file summaries into a one-paragraph tour. No need to repeat each summary — just the headline for each.

This pattern keeps your context lean while still giving the user a real answer.
