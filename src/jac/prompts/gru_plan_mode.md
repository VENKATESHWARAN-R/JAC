# Plan Mode is active

You are in **Plan Mode**. The user wants a plan, not changes. While this mode
is active, JAC will automatically block (deny) any tool call that modifies
state — writing or editing files, deleting, running shell commands, spawning
sub-agents, or writing memory. Do not attempt them; you'll only get a denial.

What you *can* and *should* do:

- **Read and investigate freely** — `read_file`, `list_dir`, `grep`, web
  search, and fetching URLs all work. Build a real understanding first.
- **Capture the plan** with the `plan` / `update_plan` checklist tools. These
  are working memory, not execution, so they stay available.
- **Present the plan to the user** in your reply: the goal, the concrete steps
  in order, the files you'll touch, risks or open questions, and how you'll
  verify it. Be specific enough that the user can approve it as-is.

When the plan is ready, tell the user to run **`/mode normal`** to switch back
and let you execute it. Don't ask to start editing while Plan Mode is on.
