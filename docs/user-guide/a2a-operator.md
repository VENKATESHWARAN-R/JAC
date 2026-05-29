# A2A operator guide

```text
        JAC A2A RADIO TOWER

              📡
              │
        ┌─────▼─────┐
        │ Gru@local │
        └─────┬─────┘
              │  "Calling all agents..."
     ┌────────┼────────┐
     │        │        │
     ▼        ▼        ▼
  peer-A   peer-B   peer-C
 "banana" "on it" "who pushed main?"

  One CLI. Many agents. Mild chaos. Mostly controlled.
```

> **Audience:** operators exposing JAC to other agents or calling remote A2A peers.
>
> **Status:** Phase 4.d shipped (2026-05-26) — server, guest Gru, outbound tools with `tasks/get` polling, pluggable peer auth, session peers, **bidirectional file transfer (inline bytes)**, inbound usage accounting, 1-hour retention timer, OAuth2 token-mint visibility, and a standalone [data-analyst demo peer](#demo-peer-data-analyst). OIDC / GCP ID-token strategies (Phase 4.e) and skill auto-publish (Phase 4.1) are queued — see [`progress.md`](../progress.md).

JAC implements the [Agent-to-Agent (A2A)](https://a2a-protocol.org/latest/) protocol via `fasta2a`. Two directions:

- **Inbound** — other agents call **your** project's read-only **guest Gru**
- **Outbound** — **host Gru** calls remote peers via `a2a_discover` / `a2a_call`

Both directions support attaching files (CSVs, images, small docs) as inline `FileWithBytes` parts; JAC saves received files to disk so its path-based tools can act on them. See [File transfer](#file-transfer-inline-bytes-both-directions).

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

| Path | Purpose | Pruned automatically? |
| --- | --- | --- |
| `contexts/<context_id>.json` | Per-peer-thread message history | Yes — `context_retention_days` |
| `inbound.jsonl` | Audit log of inbound calls (peer id, state, duration, tokens used) | No — append-only ledger |
| `guest-uploads/<context_id>/<file>` | Files peers attached to inbound `message/send` calls (Phase 4.d.4) | No today; manual cleanup |
| `inbound-files/<task_id>/<file>` | Files **outbound peers** returned in artifacts (Phase 4.d.3); paths surfaced as `_jac_saved_files` to host Gru | No today; manual cleanup |

Retention: profile `a2a.context_retention_days` (default `3`). The retention pass runs **on server start AND on a 1-hour timer while the server is up**, pruning context JSON files older than the window. `0` = keep forever. `guest-uploads/` and `inbound-files/` are not pruned automatically — they're operator-owned for now; `rm -rf <repo>/.agents/a2a/guest-uploads <repo>/.agents/a2a/inbound-files` is the safe manual cleanup.

## Outbound: calling peers

Host Gru has tools:

- **`a2a_discover(reason, url)`** — fetch agent card
- **`a2a_call(reason, peer_or_url, message, context_id?, files?)`** — send a message and **block until the peer reaches a terminal state**

### How `a2a_call` actually behaves

The A2A spec lets a server return a `Task` in `submitted`/`working` state and asks clients to poll `tasks/get` for completion. `a2a_call` does that polling for you (Phase 4.d.1):

1. POST `message/send` with your text + optional file attachments.
2. If the returned task is non-terminal, poll `tasks/get` with exponential backoff (250 ms → 2 s, capped at 120 s total).
3. Return the task once `status.state` is one of `completed` / `failed` / `canceled` / `rejected`, **or** the peer asks the client to act (`input-required` / `auth-required`).
4. If the timeout fires, the returned dict has `_jac_timeout: true` so the calling agent knows the state is stale.

Practical implication: Gru doesn't need to manage task ids, decide when to poll, or interpret "submitted" as the final answer. The tool blocks; Gru reads `artifacts[].parts[].text` and `history[]` agent messages for the answer.

### `peer_or_url` resolution

`peer_or_url` is either:

- A **named peer** from config or a session-scoped `/a2a peer add` (auth applied automatically), or
- A raw **`http://` / `https://` URL** (no auth — peer must be `--unsafe` or accept anonymous calls)

**URL auto-promote (Phase 4.d.2):** if you pass a raw URL that happens to match a configured peer's URL exactly (after trailing-slash normalization), `a2a_call` quietly promotes the call to use that peer's auth. This catches the common case where the model copies the URL from a prior `a2a_discover` step into a follow-up `a2a_call` without realizing the named peer carries auth. Multi-match peers (two peers with the same URL) fall through to a raw call — JAC won't guess which credentials to use.

### Attaching files

```text
» a2a_call analyst "summarize Q3 revenue trends" files=["./sales-q3.csv"]
```

`files` is a list of paths. Each is read, base64-encoded, capped at **5 MB per file**, attached as a `FilePart` alongside the text part. Mime type is guessed from extension (`application/octet-stream` fallback). Use this for CSVs, images, small docs — anything the peer's `agent-card.json` says it accepts. **Don't paste binary content into `message`** — use `files`.

### Receiving files from peers

When the peer's response includes inline file artifacts (a chart PNG, a rendered report), JAC decodes them and saves to `<repo>/.agents/a2a/inbound-files/<task_id>/<filename>`. The returned task dict has `_jac_saved_files: [paths]` so Gru can `read_file` the contents or surface the path to you. **The bytes never enter Gru's context window** — paths only, so a 2 MB PNG doesn't blow the token budget.

Filename sanitization defeats path-traversal (`../etc/passwd` becomes `_etc_passwd`); collisions within a task get numeric suffixes (`chart-2.png`).

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

## File transfer (inline bytes, both directions)

v1 supports A2A's `FileWithBytes` (base64) part type both ways. `FileWithUri` (URL pointer) is intentionally deferred until JAC has an SSRF story for fetching arbitrary URIs.

### Outbound — JAC sends files

```text
» a2a_call analyst "what's the trend?" files=["./sales.csv", "./baseline.csv"]
```

Behavior:

- Each path is validated (exists, is a regular file, ≤ 5 MB).
- File is read, base64-encoded, attached as a `FilePart`.
- Filename lands in both `file.name` (A2A spec field) and `metadata.filename` (belt-and-braces — some validators strip `file.name`).
- Auth headers from the named peer's strategy ride along on every request, including `tasks/get` polls.

### Outbound — JAC receives files back

When the peer's response includes a `FilePart` with `bytes` (in `artifacts[].parts` or `history[].parts`), JAC:

- Decodes the base64.
- Sanitizes the filename (strips `..`, leading dots, unsafe chars).
- Writes under `<repo>/.agents/a2a/inbound-files/<task_id>/<filename>`.
- Adds the saved paths to the returned dict as `_jac_saved_files`.

The result dict the model sees has paths, not bytes. The model can:

- Use `read_file` for textual content (CSV, JSON, code).
- Surface the path to you for binary content (open the PNG yourself).

URI-only file parts are skipped in v1 (no fetch happens).

### Inbound — JAC's guest receives files

When a peer attaches a `FilePart` to `message/send`, the guest server:

1. Lets fasta2a do its normal job — multimodal models receive the bytes directly as `BinaryContent` (vision-capable models can "see" images and PDFs natively).
2. **Additionally** decodes the bytes to disk under `<repo>/.agents/a2a/guest-uploads/<context_id>/<filename>` (Phase 4.d.4).
3. Appends a synthetic `[a2a attachment]` user message to the agent's history listing the saved paths.

Result: even the guest's path-based tools (`read_file`, `grep`, `glob`) can act on uploaded files. Multi-turn conversations reuse the same per-context directory; collisions across turns get numeric suffixes (`data-2.csv`).

### Size and safety limits

| Concern | Limit / behavior |
| --- | --- |
| Outbound per-file size | 5 MB (configurable in `client.py`; rejected before network) |
| Filename traversal | Sanitized — `Path(name).name` + non-alphanumeric → `_` |
| Path-collisions | Numeric suffix (`out-2.png`, `out-3.png`); never silent overwrite |
| Malformed base64 | Skipped without raising; remaining parts still save |
| URI-only parts | Skipped in v1 (no SSRF guard) |

## Demo peer (data-analyst)

`examples/data-analyst-a2a/` is a standalone reference peer — pandas + matplotlib, ~220 LOC, no JAC dependency. Demonstrates the full receive-side wire including chart artifacts coming back.

```bash
# Terminal A — start the peer
cd examples/data-analyst-a2a
export ANTHROPIC_API_KEY=sk-...                  # or OPENAI / GOOGLE / OPENROUTER
export ANALYST_BEARER=$(openssl rand -hex 24)    # or skip for --unsafe
uv run server.py
```

```text
# Terminal B — drive from JAC
» /a2a peer add analyst http://127.0.0.1:8002 --bearer
  bearer token: <paste $ANALYST_BEARER>

» Use a2a_call on peer analyst with files=["./examples/data-analyst-a2a/sample-data.csv"]
  and ask "What's the revenue trend across the year? Plot it."
```

What you get back:

- Text summary inline in the chat output.
- A chart PNG saved at `<your-project>/.agents/a2a/inbound-files/<task_id>/chart-xxxx.png` — open it in your image viewer.

Read [`examples/data-analyst-a2a/README.md`](https://github.com/VENKATESHWARAN-R/JAC/blob/main/examples/data-analyst-a2a/README.md) for the walkthrough. The server source is one file you can adapt as the starting point for your own A2A peer (docs agent, devops agent, search agent, whatever).

## Typical topologies

### Local two-repo coworking

1. Repo A: `jac a2a serve` → copy token from startup banner.
2. Repo B: `/a2a peer add repo-a http://127.0.0.1:8001 --bearer` → paste token.
3. Repo B Gru: `a2a_call repo-a "..."` with questions about repo A's codebase (read-only answers).

For multimodal cross-repo work, attach files: `a2a_call repo-a "summarize this design" files=["./docs/design.pdf"]`.

### Client-only JAC

You do not need a running server to call outbound peers — configure `a2a.peers` and use `a2a_call` from a normal `jac` session. This is the common case: a remote analyst / search / data agent is the peer; JAC is just the operator's interface.

### Headless endpoint

`jac a2a serve` in tmux / systemd / a container for a stable URL; peers call your guest Gru while you use a separate interactive session elsewhere. Inbound usage feeds your project budget via `UsageTracker.add_external` so a runaway peer can't bypass `project_total_tokens` limits.

## Observability

| Surface | What it shows |
| --- | --- |
| Renderer `[a2a in ←]` / `[a2a in ✓]` | Inbound call started / completed (peer id, context, duration, tokens used) |
| Renderer `[a2a out →]` / `[a2a out ✓]` | Outbound call started / completed (target, state, duration) |
| Renderer `[a2a token]` | OAuth2 strategy minted a fresh access token (peer name, IDP URL, expiry) |
| `/a2a status` | Server state (URL, bind, auth, card URL) + peer count + last 5 inbound calls from `inbound.jsonl` |
| `/tokens` | Per-session input/output/total **plus** an "a2a guest" line when inbound usage > 0 (counts toward `project_total` only, not `session_total`) |
| `inbound.jsonl` | Persistent audit ledger: timestamp, peer id, context/task id, state, duration, real `tokens_used`, message preview |
| Logfire | fasta2a + pydantic-ai spans when `LOGFIRE_TOKEN` is set; every model call, tool call, and inbound task is traced |

## Not yet shipped

- **Phase 4.e:** OIDC discovery and GCP ID-token strategies (Azure / Cloud Run / Okta convenience on top of the existing `oauth2_client_credentials`).
- **Phase 4.1:** auto-publishing community-format skills (Phase 3) into the AgentCard's `skills` list.
- **Streaming:** `message/stream` not implemented in fasta2a 0.6.1; card declares `streaming: false`.
- **`FileWithUri`:** outbound URI fetching needs an SSRF guard; inbound URI parts are silently skipped.
- **Automatic server start on `jac` launch:** by design — the operator opts in.

Track [`progress.md`](../progress.md) Phase 4 and the [A2A detail log](../progress-a2a.md).

## Related

- [CLI reference](cli-reference.md) — tool + slash signatures
- [Configuration](configuration.md) — `a2a` YAML block
- [Architecture §6/§8](../architecture.md) — inbound + outbound flow diagrams
- [Codebase map](../developer/codebase-map.md) — `capabilities/a2a/` modules
- [`examples/data-analyst-a2a/`](https://github.com/VENKATESHWARAN-R/JAC/tree/main/examples/data-analyst-a2a) — reference peer source
