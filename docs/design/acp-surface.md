# ACP — editor surface (design-only)

> **Status:** design-only (2026-05-31). Not built. Gated on one remaining condition (see [Ship conditions](#ship-conditions)). This doc captures the drop-in design the SDK-first refactor (D49) makes mechanical, so building it later is wiring, not architecture.

## What ACP is

The [Agent Client Protocol](https://agentclientprotocol.com) is the **LSP analogue for coding agents**: one JSON-RPC spec between a code **editor** (the *client*) and an AI coding **agent** (the *server*). Any ACP editor can drive any ACP agent — write the agent once, get Zed / JetBrains / Neovim / VS Code (via extension) for free. Created by Zed (Aug 2025), co-governed with JetBrains; Apache-2.0; moving toward Linux Foundation governance alongside MCP and A2A.

JAC implements the **agent (server) side**. An editor spawns JAC as a subprocess and exchanges newline-delimited JSON-RPC 2.0 over stdio.

## The protocol triangle (why ACP is a distinct surface)

Three non-overlapping protocols cover JAC's whole integration surface — none is a substitute for another:

| Protocol | Direction | JAC's role | Status in JAC |
| --- | --- | --- | --- |
| **MCP** | agent → tools/data | client | shipped (Phase F) |
| **A2A** | agent ↔ agent | server + outbound client | shipped (Phase 4) |
| **ACP** | editor → agent | **server** | **this doc — design-only** |

An ACP-equipped JAC is *also* an MCP client: at `session/new` the editor passes `mcpServers` configs that JAC connects to. Editor (ACP client) → JAC (ACP agent + MCP client) → MCP servers.

## How it drops onto the existing SDK

ACP is a thin **Layer-3 surface adapter** (see [architecture.md §5 D49](../architecture.md)) over the same engine the CLI and web already share. It reuses, not reinvents:

```
ACP editor ──JSON-RPC/stdio──▶  ACPCapability (new, ~the A2A headless pattern)
                                   │
                                   ├─ session/new  → build_session_runtime()  (the shared engine)
                                   ├─ session/prompt → SessionDriver.run_turn()
                                   ├─ control verbs  → SessionController        (D49 control plane)
                                   └─ session/update ← EventBus  (translate JacEvents → ACP notifications)
```

| ACP method / notification | JAC seam |
| --- | --- |
| `initialize` | advertise capabilities (`loadSession`, `sessionCapabilities`, no `image` unless added) |
| `session/new` (`cwd`, `mcpServers`) | `build_session_runtime()` for the project at `cwd`; feed MCP configs to `MCPCapability` |
| `session/load` / `session/list` | the D3 session filesystem (`<repo>/.agents/sessions/`) |
| `session/prompt` | `SessionDriver.run_turn()` |
| `session/update` (agent → editor) | translate `EventBus` `JacEvent`s — same bus the CLI `CliRenderer` and web SSE consumer read. `agent_message_chunk` ← streamed text; `plan` ← `PlanReplaced`; `tool_call` / `tool_call_update` ← `ToolCallStarted`/finished |
| `session/request_permission` | the **same** `asyncio.Future` HITL flow the CLI prompt and web POST resolve (`make_approval_handler`) — map allow/reject options to the future result |
| `fs/read_text_file`, `fs/write_text_file` | JAC's file tools, routed through the client connection when in ACP mode (editor buffer state, diff UI) |
| `terminal/*` | `ProcessCapability` |
| slash commands (`available_commands_update`) | `SessionController` verbs (switch model/profile, reload MCP/skills) + the read-only slash handlers |
| `session/cancel` | turn cancellation |

The control plane (D49) is the load-bearing prerequisite: ACP's slash commands and the editor's model/mode switches map straight onto `SessionController` verbs, with **zero** new mutation logic — the same verbs the CLI slash handlers and web endpoints already call.

## Transport

- **stdio (stable)** — the only production transport today; what every shipping editor spawns. Reached via a future `jac acp` command (mirrors `jac a2a serve` / `jac web serve` — a headless surface entry point). **Constraint:** stdout is reserved for JSON-RPC; all incidental JAC output must go to stderr.
- **Streamable HTTP / WebSocket (draft RFD)** — an active Transports Working Group proposal (`/acp` endpoint, HTTP/2 + SSE or WS upgrade); **not stable**. This is the remaining ship gate.

## SDK

Official Python SDK: [`agent-client-protocol`](https://pypi.org/project/agent-client-protocol/) (Pydantic models generated from the spec schema; `BaseAgent` abstract class; `run_agent()` stdio pump; `acp.helpers` builders for content blocks / tool-call updates / permission requests). Rust / TypeScript / Kotlin / Java / Go SDKs also exist. (Pydantic-AI has an open issue for native ACP support — #3255 — but it is not implemented, so JAC would use the SDK directly, the same way it uses `fasta2a` for A2A.)

## Ship conditions

D45 originally gated ACP on **two** conditions. As of 2026-05-31:

1. ❌ **Remote HTTP/WebSocket transport stabilises** — still a draft RFD. **This remains the gate.**
2. ✅ **A major editor ships an ACP client** — **met**: Zed ships it natively; JetBrains is shipping through 2026; Neovim/Emacs via plugins.

Per the 2026-05-31 decision, ACP stays **design-only** until condition 1 clears — honoring D45 as written. Note, however, that **stdio is already stable and is what editors actually spawn**, so a stdio-only `jac acp` surface could ship today against Zed if the priority changes; remote transport matters for a *network-reachable* ACP server (the A2A/web posture), which is a nice-to-have, not a requirement for the core editor-subprocess model. Revisit when the Transports WG lands a stable spec.

## What building it would touch (when unblocked)

- `src/jac/capabilities/acp/` — `ACPCapability` + a `BaseAgent` subclass translating ACP ↔ `SessionRuntime`/`SessionController`/`EventBus` (mirror `capabilities/a2a/`).
- `src/jac/cli/acp.py` — the headless `jac acp` Typer command (mirror `cli/a2a.py`).
- A bus-event → `session/update` translator (the ACP analogue of `CliRenderer` / the web `event_to_frame`).
- Registry/distribution: an `agent.json` in the ACP Registry so ACP editors can one-click-install JAC.

No engine, control-plane, or HITL changes — those are already shared.
