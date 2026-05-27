---
name: code-review
description: Review a diff or branch for correctness, security, and style before it ships. Use when the user asks for a review, when finalizing a feature branch, or before opening a PR.
tools_required:
  - run_shell
  - read_file
  - grep
---

# Code review playbook

You are reviewing changes that are about to ship. Your job is to be a fair, focused, second pair of eyes — not a stylebot, and not a rubber stamp.

## 1. Read the whole diff before reacting

Run `git diff` (or `git diff <base>...HEAD` for a branch). Read every hunk top to bottom **before** you start commenting. A change that looks wrong in isolation often makes sense once you've seen the call site three files down.

If the diff is over ~10 files or ~500 lines, **delegate per-file review to a sub-agent** and aggregate yourself:

```
spawn_sub_agent(
  reason="per-file review of large diff",
  task_summary="Review src/foo.py for correctness; report issues + line numbers",
  tier="small",
  task_packet=SubAgentTaskPacket(
    objective="Review one file end-to-end for correctness, security, and style.",
    success_criteria="Returns either '✓ no issues' or a numbered list of concrete findings with line numbers.",
    relevant_paths=["src/foo.py"],
    expected_output="Markdown list of findings, or '✓ no issues'.",
    max_turns=5,
  ),
)
```

## 2. The five-question filter

For every change, ask:

1. **Does it do what it claims?** Match the diff against the commit message / PR description / issue. Mismatches deserve a comment even if the code is fine.
2. **What did it break?** Look at every function the change touches and trace its callers (`grep` for the symbol). Silent-behavior changes are the most expensive bugs to ship.
3. **What's missing?** New code path → new test. New config → new docs. New error case → new log line. If those are absent, say so.
4. **Will it scale / persist / fail safely?** Look for: unbounded loops, missing timeouts, missing error handling, missing input validation, secrets in logs, paths that escape a workspace root, `shell=True`, `eval`, string-built SQL.
5. **Is it understandable in 6 months?** A clever one-liner that takes 10 minutes to parse is worse than 5 obvious lines. Push back on cleverness that isn't earning its keep.

## 3. Categorize every comment

Tag each finding so the author knows what to act on:

- **🔴 blocker** — must fix before merge (correctness, security, data loss)
- **🟡 should-fix** — strong preference; merge-blocking unless there's a reason
- **🟢 nit** — style or taste; author's call

If everything you find is a nit, lead with that fact — "no blockers, three nits below" — so the author isn't bracing for impact.

## 4. Verify, don't trust

When the diff claims something is tested, **run the tests**:

```
run_shell(
  reason="verify the new test the diff adds actually passes",
  command="uv run pytest tests/test_thing.py -x",
)
```

When the diff claims a type signature change is compatible, **run the typechecker**:

```
run_shell(reason="verify the type change", command="uv run ty check src/")
```

If you can't verify a claim, say so explicitly: "I couldn't verify that X works; please confirm before merging."

## 5. Output shape

Open with a one-paragraph summary: what the change does, what's good about it, and the headline concern (if any). Then a numbered list of findings, tagged by severity, each with:

- The file and line number
- What's wrong (or what's suspicious)
- A concrete suggested fix (or "I'd want to discuss this — what's the constraint I'm missing?")

End with a recommendation: **approve**, **approve with nits**, **changes requested**, or **needs discussion**.
