# A2A operator guide

> **Audience:** operators exposing JAC to other agents or calling remote A2A peers.
>
> **Status (v0.2.0):** Phase 4 partial — server, guest Gru, outbound tools, pluggable peer auth, and session peers are shipped (PR1–PR3). PR4 polish (status/budget/retention timer) and PR5 OIDC/GCP tokens are queued — see [`progress.md`](../progress.md).

JAC implements the [Agent-to-Agent (A2A)](https://a2a-protocol.org/latest/) protocol via `fasta2a`. Two directions:

- **Inbound** — other agents call **your** project's read-only **guest Gru**
- **Outbound** — **host Gru** calls remote peers via `a2a_discover` / `a2a_call`

## Security model

| Mode | Behavior |
| --- | --- |
| **Default (bearer)** | Server generates a random token each start; clients send `Authorization: Bearer <token>` |
| **`--unsafe`** | No auth — card omits `securitySchemes`; anyone who can reach the port controls guest Gru |

Default bind: **`127.0.0.1:8001`** (profile `a2a.host` / `a2a.port`). Binding to `0.0.0.0` is an explicit LAN exposure choice.

!!! warning "Guest Gru is not your REPL session"
    Inbound calls do **not** see your interactive session history, approval UI, or write tools. They hit an isolated agent with a read-only toolset.

## Guest Gru boundaries

`build_guest_gru` (`jac.capabilities.a2a.guest`) differs from host Gru:

| Capability | Host REPL | Guest (inbound) |
| --- | --- | --- |
| `read_file`, `list_dir` | Yes | Yes |
| `grep`, `glob` | Yes | Yes |
| `write_file`, `edit_file` | Yes (approval) | No effective writes (no approval handler) |
| `run_shell`, processes | Yes | No |
| `web_search`, `fetch_url` | Yes | No |
| `remember`, `forget` | Yes | No |
| `plan`, `clarify` | Yes | No |
| `a2a_call` | Yes | No |
| Session `messages.json` | Yes | No — uses A2A `context_id` storage |
| Instructions | `gru_system.md` + context | Same + **guest addendum** |

Context loaded: `~/.jac/AGENTS.md`, `~/.jac/memory.md`, `<repo>/AGENTS.md`, `<repo>/.agents/memory.md` — the guest is this project's expert, with your cross-project preferences.

## Start and stop the server

### In the REPL

```text
/a2a serve
/a2a serve --port 9000 --host 127.0.0.1
/a2a serve --unsafe
/a2a status
/a2a token
/a2a stop
```

Token is printed at serve time; `/a2a token` reprints it if the server is running.

### Headless

```bash
jac a2a serve
jac a2a serve --profile claude --port 8001
jac a2a serve --unsafe   # trusted networks only
```

Startup prints:

- Serving URL
- Bearer token (unless `--unsafe`)
- Agent card URL: `{url}/.well-known/agent-card.json`

Ctrl-C or SIGTERM shuts down cleanly.

The server does **not** auto-start when you open `jac` — you opt in with `/a2a serve` or `jac a2a serve`.

## On-disk A2A state

Under `<repo>/.agents/a2a/`:

| Path | Purpose |
| --- | --- |
| `contexts/<context_id>.json` | Per-peer-thread message history |
| `inbound.jsonl` | Audit log of inbound calls |

Retention: profile `a2a.context_retention_days` (default `3`). On each server start, expired context files are pruned. `0` = keep forever.

## Outbound: calling peers

Host Gru has tools:

- **`a2a_discover(reason, url)`** — fetch agent card
- **`a2a_call(reason, peer_or_url, message, context_id?)`** — send a message; optional `context_id` continues a thread

`peer_or_url` is either:

- A **named peer** from config (auth applied automatically), or
- A raw **`http://` / `https://` URL** (unauthenticated unless you configure headers elsewhere)

### Profile peers (persistent)

In `~/.jac/config.yaml` under the profile's `a2a.peers`:

```yaml
profiles:
  claude:
    a2a:
      host: 127.0.0.1
      port: 8001
      context_retention_days: 3
      peers:
        staging:
          url: http://127.0.0.1:8001
          description: Local guest on another checkout
          auth:
            type: bearer
            token: ${MY_STAGING_A2A_TOKEN}
```

Legacy shorthand `token: "..."` is promoted to `auth: {type: bearer, token: ...}`.

Auth types:

| `auth.type` | Use case |
| --- | --- |
| `bearer` | JAC↔JAC, pre-shared token |
| `api_key` | Custom header (`header` + `value`) |
| `oauth2_client_credentials` | Azure/Auth0-style service tokens (`token_url`, `client_id`, `client_secret`, optional `scope`) |

Values may use `${ENV_VAR}` resolved via the secrets backend.

Edit peers: `jac profiles edit NAME`.

### Session peers (ephemeral)

```text
/a2a peers
/a2a peer add mypeer https://example.com:8001 --bearer
/a2a peer add svc https://api.example.com --api-key X-Api-Key
/a2a peer add entra https://login.example.com/token CLIENT_ID --oauth2 https://login.../token CLIENT_ID --scope "api://app/.default"
/a2a peer remove mypeer
```

Secrets are prompted with `getpass` — never pass tokens on the command line. Session peers override profile peers of the same name until removed.

List shows `[session]` vs `[profile]` provenance; shadowed profile entries appear dimmed.

## Typical topologies

### Local two-repo coworking

1. Repo A: `jac a2a serve` → copy token
2. Repo B: `/a2a peer add repo-a http://127.0.0.1:8001 --bearer` → paste token
3. Repo B Gru: `a2a_call` with questions about repo A's codebase (read-only answers)

### Client-only JAC

You do not need a running server to call outbound peers — configure `a2a.peers` and use `a2a_call` from a normal `jac` session.

### Headless endpoint

`jac a2a serve` in tmux/systemd for a stable URL; peers call guest Gru while you use a separate interactive session elsewhere.

## Observability

Inbound/outbound A2A activity emits bus events rendered in the REPL (`[a2a]` notifications). Logfire captures spans from the fasta2a stack when configured.

Audit: append-only `inbound.jsonl` with peer id, context, task state, duration, token preview.

## Not yet shipped

- PR4: richer server status integration with budgets and scheduled retention
- PR5: OIDC / GCP identity tokens for peers
- Automatic server start on `jac` launch

Track [`progress.md`](../progress.md) Phase 4.

## Related

- [CLI reference](cli-reference.md) — tool signatures
- [Configuration](configuration.md) — `a2a` YAML block
- [Codebase map](../developer/codebase-map.md) — `capabilities/a2a/` modules
