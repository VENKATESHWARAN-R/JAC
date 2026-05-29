# MCP servers

JAC connects to external [Model Context Protocol](https://modelcontextprotocol.io)
servers so its tool surface scales without hand-writing every tool. An MCP
server is any process (local or remote) that exposes tools over the MCP
protocol — GitHub, Slack, a database, a browser, your own scripts.

JAC adds the *fabric* around pydantic-ai's MCP client: layered config, HITL
approval, the large-output post-processor, and — crucially — **tool search**,
so dozens of MCP tools don't bloat the prompt.

## Configuring servers

Servers live in a JSON file using the standard `mcpServers` shape — the same
one Claude Desktop, Cursor, and the MCP spec use, so an existing config pastes
in verbatim:

- **User-level:** `~/.jac/mcp.json`
- **Project-level:** `<repo>/.agents/mcp.json`

Project entries **shadow** user entries of the same name (same precedence as
skills and prompts).

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" }
    },
    "docs": {
      "type": "http",
      "url": "https://example.com/mcp"
    }
  },
  "jac": {
    "docs": { "requires_approval": false }
  }
}
```

Environment variables are expanded with `${VAR}` (errors if unset) or
`${VAR:-default}` (falls back). Keep secrets in your environment / `.env`, not
in the file.

### Transports

| Shape | Transport |
| --- | --- |
| `"command": ...`, `"args": [...]` | local subprocess (stdio) |
| `"type": "http"`, `"url": ...` | Streamable HTTP |
| `"type": "sse"`, `"url": ...` | SSE (legacy; prefer HTTP) |

## The `jac` knobs block

The optional `jac` block carries JAC-specific per-server settings. It's a
*sibling* of `mcpServers`, so the file stays a valid standard catalog. Any
server you don't list gets the defaults.

| Knob | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Attach this server's tools. Toggle at runtime with `/mcp enable\|disable`. |
| `defer` | `true` | Hide the tools behind **tool search** until needed (recommended). Turn off only for a tiny, always-used server. |
| `requires_approval` | `true` | HITL-gate every call into the server. Set `false` for trusted / read-only servers. |

## Why this won't bloat your context

A handful of MCP servers can add tens of thousands of tokens of tool
definitions before you've typed anything. JAC defers every MCP server's tools
by default: pydantic-ai's built-in **tool search** discovers them on demand —
natively on Anthropic/OpenAI models, via a local fallback elsewhere — and the
discovery is append-only, so prompt caching survives. Gru sees a short
"servers available: …" note and searches when a task needs one.

Large tool *outputs* are handled too: every MCP result flows through the same
[cost-control post-processor](cost-controls.md) as local tools, so a 50k-token
API dump gets summarized by the small-tier model before it hits the main loop
(the full output is cached to disk and re-readable).

## Approval & safety

External servers run code JAC didn't write, so **every MCP call is approved by
default**. The approval panel shows `reason: (mcp tool — no reason captured)`
— external tools don't carry JAC's `reason:` convention, and that's expected.
Approve with **Enter** (default yes), deny with **n**, or **r** to redirect.

Trust a specific server (e.g. a read-only docs lookup)? Set
`"requires_approval": false` for it in the `jac` block.

## Managing servers at runtime

| Command | Effect |
| --- | --- |
| `/mcp list` | Table of every server: transport, enabled/disabled, approval & defer knobs, and which file it came from. Surfaces parse / load errors. |
| `/mcp reload` | Re-scan both catalogs and rebuild Gru — pick up newly added or edited servers without restarting. |
| `/mcp enable NAME` | Turn a server on; persists to the owning file and rebuilds Gru. |
| `/mcp disable NAME` | Turn a server off (its tools disappear from Gru); persists and rebuilds. |

Sub-agents inherit the same MCP servers, deferred-loaded — so a spawned
minion can search for and use an MCP tool, and its bulky output stays in the
minion's isolated context.

## Server logs & the terminal

A stdio MCP server's **stderr is redirected to a log file**, not your
terminal: `<.agents-or-~/.jac>/cache/mcp/logs/<server>.log`. This keeps the
REPL clean *and* prevents a misbehaving server (notably Node-based ones like
chrome-devtools / playwright) from holding the controlling terminal and
flipping it into raw mode mid-prompt. If a server misbehaves, read its log
file for the raw output. As a second layer of defence, JAC also forces the
terminal back into a sane line-editing mode each time it shows an approval
prompt — so even a rogue server can't freeze the `y/n/r` prompt.

> If a terminal ever does end up wedged (e.g. from an older build or an
> unrelated tool), `stty sane` or `reset` in that shell restores it.

## Troubleshooting

- **`/mcp list` shows a load error.** Usually a missing env var
  (`environment variable 'X' is not set`) or an unreachable URL. Errors are
  **per-server** — one bad server doesn't stop the others. Fix the catalog /
  environment and `/mcp reload`. A broken catalog never crashes JAC.
- **Gru doesn't use an MCP tool.** With `defer` on, Gru must *search* for it
  first. Make sure the server is `enabled` (`/mcp list`) and that its tool
  names/descriptions match what you're asking for.
- **A disabled server's env var is undefined — does that break loading?** No.
  Disabled servers are omitted before env expansion, so they can't abort the
  load of the others.

## What's not here (yet)

Programmatic tool calling / code mode (the model orchestrating many MCP calls
inside one sandboxed script) is intentionally **deferred to v2**: it bypasses
JAC's per-tool approval, and sub-agents already capture most of the
"keep intermediate results out of the main context" benefit.
