# Examples

> **Audience:** users who learn by worked scenarios.

Assumes you have run [`jac init`](getting-started.md) and are inside a git repository.

## 1. First visit to a repository

```bash
cd ~/src/my-app
jac
```

Gru loads `AGENTS.md` if present. Ask:

```text
» Give me a one-paragraph overview of this repo's layout.
```

Gru uses `list_dir`, `read_file`, and possibly `grep`. No approval needed for reads.

If you want a fact saved for later sessions, Gru may call `remember` — you will see an approval panel with scope and category. Approve only what you want persisted.

## 2. Fix a bug with HITL edits

```text
» There's an off-by-one in pagination in src/api/routes.py — fix it.
```

Typical flow:

1. `grep` / `read_file` to locate the bug
2. Gru proposes a change; may call `edit_file` with patches
3. Approval panel shows `reason` and diff hunks — answer `y` or `n`
4. Optional `run_shell` to run tests (also approval-gated)

If unsure between approaches, Gru may call `clarify` with numbered options.

## 3. Resume yesterday's session

```bash
jac --resume
# or
jac --session 2026-05-23T09-15-00
```

Greeting shows `(resumed, N prior messages)`. Plan steps restore if `plan.json` exists.

In-session:

```text
/sessions
/resume 2026-05-23T09-15-00
```

## 4. Switch model or profile mid-session

List profiles:

```text
/profile
```

Switch default profile for this REPL (rebuilds Gru; rolls back on missing keys):

```text
/profile ollama-local
```

Pick another model from the active profile's tiers:

```text
/model
```

Ad-hoc gateway routing:

```text
/model openai:gpt-4o
```

## 5. Save a cross-project preference

When Gru calls `remember` with `scope=user`:

- File: `~/.jac/memory.md`
- Survives across all projects on this machine
- Example content: "Prefer pytest over unittest"

Deny approval if you do not want it stored.

Project-only convention:

- `scope=project` → `<repo>/.agents/memory.md`
- Fails if you are not in a git repo

Remove later:

```text
# Gru calls forget(reason, content, scope) with the same normalized text
```

Or edit `memory.md` directly.

## 6. Run a dev server in the background

```text
» Start the frontend dev server and tell me when it's listening.
```

Gru may call `start_process` (approval), then `tail_process` to read logs, and `kill_process` when done.

For one-shot commands, Gru uses `run_shell` instead.

## 7. Web research with Tavily

Set a key (optional):

```bash
jac keys set TAVILY_API_KEY
# or export TAVILY_API_KEY=...
```

```text
» What changed in Pydantic v2 migration for settings?
```

Without Tavily, `web_search` uses DuckDuckGo. `fetch_url` retrieves a specific page.

## 8. Token budget guardrail

In `<repo>/.agents/config.yaml`:

```yaml
budget:
  session_total_tokens: 200000
  warn_pct: 80
```

During a long session:

```text
/budget
/tokens
/budget extend 50000
```

Hard stop at 100% of limit — use `/budget extend` or `/clear` for a fresh session.

## 9. Expose JAC to another agent (A2A)

Terminal A — start the headless server (it prints the bearer token on startup):

```bash
jac a2a serve
```

Terminal B — another JAC or A2A client:

```text
/a2a peer add staging https://127.0.0.1:8001 --bearer
# paste token at prompt
```

Then Gru can `a2a_discover` and `a2a_call` to ask the remote guest about the repo.

Headless equivalent:

```bash
jac a2a serve --profile claude
```

Full detail: [A2A operator](a2a-operator.md).

## 10. Headless operator checklist

```bash
jac profiles
jac keys
jac sessions
jac a2a serve --host 127.0.0.1 --port 8001
```

Capture the printed bearer token for peer config. Use `--unsafe` only on trusted loopback tests.

## Related

- [CLI reference](cli-reference.md)
- [Configuration](configuration.md)
- [Sessions & memory](sessions-and-memory.md)
