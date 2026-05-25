# JAC — A2A Progress Log

> Detailed Phase 4 implementation notes. Start with [`progress.md`](progress.md) for the active checklist.

## Phase 4 — A2A (D24, D30, D31) 🚧

**Goal:** speak the A2A protocol both ways so JAC can talk to other A2A-compatible agents — other JAC instances *or* third-party deployed agents (cloud-hosted data-science agent, enterprise A2A endpoint, anything that follows the spec). Cross-repo coworking via two JAC instances is the headline differentiator from `idea.md`.

**Why this exists (the research pass on 2026-05-24):** A2A v1.0 (announced Nov 2025 by AWS / Cisco / Google / IBM / Microsoft / Salesforce / SAP / ServiceNow) is now a real standard — stable wire format (JSON-RPC 2.0 over HTTPS), standardized AgentCard discovery at `/.well-known/agent-card.json`, OpenTelemetry tracing baked into the spec. `fasta2a` 0.6.1 ships a Pydantic AI bridge (`fasta2a.pydantic_ai.agent_to_a2a`) that takes a pydantic-ai Agent and returns a Starlette ASGI app — auto-builds the AgentCard, registers `POST /` for JSON-RPC, registers the discovery endpoint, and includes a Worker that maps A2A `Message` ↔ pydantic-ai `ModelMessage` and skips `ToolCallPart` in responses (no tool internals leak to peers). That eliminates almost all wire-protocol work; this phase is mostly *isolation*, *auth*, *config*, *audit*, and *UX*.

**Locked decisions (brainstorm 2026-05-24, see D24 revision + D30):**

| # | Locked |
|---|---|
| 1 | Single guest-Gru instance reused; per-call isolation via fasta2a's per-context Storage (not literal fresh agent — pydantic-ai is stateless between `.run()`) |
| 2 | Guest toolset: `read_file`, `list_dir`, `grep`, `glob` only |
| 3 | Auto-approve guest tool calls + inline `[a2a]` event + Logfire span tagged with caller |
| 4 | Single generic skill in AgentCard for v1; community-skill auto-publish → Phase 4.1 (after Phase 3) |
| 5 | Two outbound tools: `a2a_discover(reason, url) → AgentCard`, `a2a_call(reason, peer_or_url, message, context_id=None)` |
| 6 | Bind `127.0.0.1` default; `--host 0.0.0.0` to expose; ephemeral bearer token printed once at startup; `--unsafe` skips auth + omits `securitySchemes` |
| 7 | Headless `jac a2a serve --profile NAME` (falls back to `default_profile`) |
| 8 | Guest tokens count against host's `project_total_tokens`, **not** `session_total_tokens` |
| 9 | Persist contexts + `inbound.jsonl` under `<project>/.agents/a2a/`; default 3-day retention, configurable via `a2a.context_retention_days` |
| 10 | Streaming + cancel NOT in v1 (fasta2a 0.6.1 doesn't implement them); card declares `streaming: false` |

**Non-negotiables (same as every phase):** every new tool carries `reason: str` and goes through `@jac_tool`; new events extend `JacEventT`; CLI subcommand and slash share internals (no duplication); architecture decisions ride to `§11` in the same change.

### Phase 4.a — Server scaffold + guest Gru (PR1) ✅

**Why:** the foundational lift. Stand up the server, build the guest Gru with the narrowed toolset, gate it with bearer auth, persist contexts, and wire the lifecycle to slash + headless command. Outbound and polish come in later PRs but are useless without this.

**Landed (2026-05-24):**
- [x] `jac.capabilities.a2a.__init__` — `A2ACapability` (server lifecycle methods only; outbound tools land in PR2 / Phase 4.b). Public surface: `start_server` / `stop_server` / `shutdown`. `model` accepts `str | Model | None` so tests pass a `TestModel()` instance directly.
- [x] `jac.capabilities.a2a.server` — `A2AServer` wrapping `agent_to_a2a()`; runs on background asyncio task; clean shutdown via `uvicorn.Server.should_exit`. Custom `AuditingAgentWorker` subclasses fasta2a's worker to emit `A2AInboundCall`/`Completed` and append to the audit log around every inbound `run_task`. Custom card route registered before fasta2a's so `securitySchemes` actually ships in the AgentCard (fasta2a 0.6.1 builds its own internal card from constructor args and can't declare auth).
- [x] `jac.capabilities.a2a.guest` — `build_guest_gru(model=)` builds Gru with `FilesystemCapability` + `SearchCapability` only (writes are bundled in `FilesystemCapability` but unreachable — no approval handler installed on the guest). Loads project + user AGENTS.md and memory.md plus a guest-mode addendum.
- [x] `jac.capabilities.a2a.auth` — `BearerAuthMiddleware` (Starlette `BaseHTTPMiddleware`) using `hmac.compare_digest`; `generate_token() -> str` via `secrets.token_urlsafe(32)`; `redact_token` + `peer_id_from_token` helpers. Public path `/.well-known/agent-card.json` bypasses auth so peers can discover before authenticating.
- [x] `jac.capabilities.a2a.card` — `build_agent_card(profile_name, base_url, unsafe)` returns the `AgentCard` TypedDict (snake_case keys → camelCase JSON via fasta2a's `alias_generator=to_camel`). Single generic `jac-coding-assistant` skill in v1; bearer scheme declared when `unsafe=False`, omitted otherwise.
- [x] `jac.capabilities.a2a.storage` — `JacFileStorage(fasta2a.Storage)` keeps tasks in memory (ephemeral execution state) but persists contexts to `<project>/.agents/a2a/contexts/<context_id>.json` via `ModelMessagesTypeAdapter`. Atomic writes (tempfile + rename). Context-id sanitization defends against path-traversal.
- [x] `jac.capabilities.a2a.audit` — `InboundLog` JSONL appender for `<project>/.agents/a2a/inbound.jsonl` (best-effort, swallows OSError so disk failures don't fail inbound calls); `cleanup_old_contexts(retention_days)` mtime-based pruning, runs on server start (1-hour timer comes in PR3).
- [x] Profile schema: `a2a.peers.<name>: {url, token, description}` (optional, defaults `{}`) + `a2a.host` (default `127.0.0.1`) + `a2a.port` (default `8001`) + `a2a.context_retention_days` (default `3`). Validated on profile load. PR1 doesn't *use* peers (outbound is PR2) but the schema is locked now to avoid breaking changes later.
- [x] `/a2a serve [--port N] [--host ADDR] [--unsafe]` + `/a2a stop` + `/a2a status` + `/a2a token` slash commands (`jac.cli.slash.handlers.a2a`). Async work (`serve`/`stop`) goes via new `StartA2AServer` / `StopA2AServer` slash-result types so the REPL drives the coroutine in *its own* event loop — spinning a helper-thread loop would kill the server when the thread exits.
- [x] `jac a2a serve [--port N] [--host ADDR] [--unsafe] [--profile NAME]` headless typer command (`jac.cli.a2a`) — shares `A2ACapability.start_server` with the slash path; sleeps on `asyncio.Event` until SIGINT/SIGTERM.
- [x] Events: `A2AServerStarted(url, token_redacted, unsafe, bind_host)`, `A2AServerStopped(reason)`, `A2AInboundCall(peer_id, context_id, task_id, message_preview)`, `A2AInboundCompleted(peer_id, context_id, task_id, state, duration_ms, tokens_used)` — added to `JacEventT`.
- [x] CLI renderer prints muted cyan `[a2a]` notifications for `A2AInboundCall` (`←`) and `A2AInboundCompleted` (`→` with green/red state coloring). `A2AServerStarted` / `Stopped` events are no-op in renderer because the slash + headless paths already print their own banners (avoids double notifications).
- [x] REPL wires the capability into every session, threads it through `SlashContext`, handles `StartA2AServer` / `StopA2AServer` in the dispatch loop, reaps the server on REPL exit (best-effort, mirrors the `process_capability.shutdown()` reaper).
- [x] `uvicorn>=0.32.0` + `httpx>=0.28.0` added as hard deps in `pyproject.toml`.
- [x] **41 tests across 6 files** (`test_a2a_auth.py`, `test_a2a_card.py`, `test_a2a_audit.py`, `test_a2a_storage.py`, `test_a2a_guest.py`, `test_a2a_slash.py`, `test_a2a_server.py`): bearer middleware (valid/invalid/missing/wrong-scheme/well-known-bypass), card builder (name composition / auth declaration / unsafe omission / fasta2a schema round-trip), audit log + retention (mtime cutoff / disabled-when-zero / non-json-ignored / OSError-swallowed), storage round-trip (task lifecycle / context persist+load / path-traversal sanitization / atomic write), guest toolset introspection (`_cap_toolsets` walk — proves exactly 6 tools, the 4 allowed + 2 unreachable writes; all forbidden tools confirmed absent), slash parsing + dispatch (every subcommand), end-to-end server integration (real uvicorn bind on free port, auth round-trip via httpx).
- [x] All 240 tests in the repo pass (existing 199 + new 41); `just check` (ruff format + lint + ty typecheck) clean.
- [x] architecture.md §11 D24 **revised** + new **D30** (file layout) recorded — 2026-05-24

**Known gaps (PR4-scoped):**
- Inbound `A2AInboundCompleted.tokens_used` is hardcoded to `0`; the budget integration that pulls real usage from the agent's `result.usage()` lands in **PR4** (`UsageTracker.add_external`, guest line in `/tokens`).
- Context retention cleanup runs only on server start; the 1-hour while-running timer is **PR4**.
- `cancel_task` is a no-op (inherited from fasta2a's `AgentWorker`); `tasks/cancel` returns the standard `TaskNotCancelable` error. Revisit when fasta2a implements cancel.
- The agent card declares `streaming: false` because fasta2a 0.6.1 raises `NotImplementedError` on `message/stream`. Revisit when fasta2a ships streaming.

### Phase 4.b — Outbound tools + peer config (PR2) ✅

**Why:** once the server works, give Gru the other half — the ability to *call* peers. Two tools because the A2A spec's `A2ACardResolver` pattern shows clients normally discover first, then send. Single-tool would force Gru to discover blind through trial-and-error.

**Landed (2026-05-24):**

- [x] `jac.capabilities.a2a.client.a2a_discover(reason, url) -> dict` — httpx `GET {url}/.well-known/agent-card.json` with a 10s timeout, validates via `agent_card_ta`, returns the parsed dict with spec **camelCase** keys (re-serialize with `by_alias=True` then parse so Gru sees the same field names the spec documents). 4xx/5xx surfaces as `ValueError`. Empty URL rejected.
- [x] `jac.capabilities.a2a.client.a2a_call(reason, peer_or_url, message, context_id=None) -> dict` — builds a `message/send` JSON-RPC request, dumps with `by_alias=True` (wire = camelCase), posts via raw httpx with our auth-injected headers. Returns the peer's `result` (Task/Message envelope) as a plain dict. JSON-RPC errors surface as `ValueError` carrying the code + message. 60s timeout (generous for peers running real models).
- [x] Profile schema `a2a.peers.<name>: {url, token, description}` was locked in PR1; PR2 wired it into the runtime. Peer-name regex matches the profile-name regex (`[a-z0-9-]+`).
- [x] `resolve_target(peer_or_url, peers)` — pure function in `client.py`. URL with `http(s)://` prefix → raw target (no token). Otherwise look up by name; unknown name raises `JacConfigError` listing the configured peers so the agent can recover. Returns `_ResolvedTarget(url, token, display)` — `display` is what we surface in events (peer name when called by name, URL when raw).
- [x] **No `token=` kwarg on `a2a_call`** — deliberate. Putting bearer secrets in tool args means they end up in the model's context window and on disk in `messages.json`. Peers with tokens live in the profile only.
- [x] `/a2a peers` slash command — lists name / URL / auth (bearer or none) / truncated description. Reads from the capability's live `peers` dict so `/profile` swaps surface immediately.
- [x] Outbound tools registered via `A2ACapability.get_toolset()` — `jac_function_toolset(a2a_discover, a2a_call)`. Carried into every session by default; the capability is in `_default_tool_capabilities()` via the REPL's `persisted_capabilities` list.
- [x] **Peer-getter closure pattern** — tools close over `peers_getter` (a zero-arg callable), not the dict directly. When `/profile` mutates `A2ACapability.peers` in place, the tools' next call sees the new map without rebuilding the toolset. (Capturing the dict by value would leave them stuck with the original.)
- [x] Events: `A2AOutboundCall(target, message_preview)`, `A2AOutboundCompleted(target, state, duration_ms)` added to `JacEventT`. State is binary: `"completed"` (got a response, even a JSON-RPC error one — that's still a successful round-trip) or `"failed"` (network/auth/protocol error before we got a body).
- [x] CLI renderer paints `[a2a out →]` for outbound call and `[a2a out ✓]` for completion. Inbound notifications renamed to match (`[a2a in ←]` / `[a2a in ✓]`) so direction is unambiguous in scrollback.
- [x] REPL refreshes `a2a_capability.peers` on `/profile` rebuild (in-place mutation via `.clear()` + `.update()` so the existing closure stays valid).
- [x] `gru_system.md` extended: new "When to call `a2a_discover` / `a2a_call`" section with do/don't lists, two-step discover-then-call rhythm, auth model explanation. Tool listing at top updated with both new tools.
- [x] **23 new tests** across 2 files (`test_a2a_client.py` 21, `test_a2a_slash.py` 2): `resolve_target` (5 cases — by-name / raw-URL / https / unknown / lists-configured-on-error), `a2a_discover` (5 — returns camelCase / rejects empty / raises on 404 / raises on malformed / emits events), `a2a_call` (9 — sends `message/send` / injects bearer for named peers / omits auth for raw URL / context_id round-trip / surfaces JSON-RPC errors / rejects empty message / unknown peer / events with peer-name target / failed-state events), `peers_getter` runtime mutation, plus `/a2a peers` slash (empty state + populated rendering).
- [x] All 263 tests pass (240 prior + 23 new); `just check` clean (ruff format ✓, lint ✓, ty ✓).

### Phase 4.c — Pluggable outbound auth strategies + session peers (PR3) ✅

**Why:** PR1 + PR2 shipped bearer-only outbound auth, which works for JAC↔JAC with a pre-shared static token but blocks every real-world remote case. Azure peers want OAuth2 client_credentials (Entra ID); GCP Cloud Run wants ID tokens; third-party SaaS often uses API keys in custom headers. Worse, the JAC↔JAC token rotates on every server restart and the operator had to hand-paste it into peer config. Both problems are about *credential handling*; the framework-agnostic part of A2A was already fine (the wire protocol is just JSON-RPC). D31 generalizes outbound auth into pluggable strategies AND separates "stable peers in YAML" from "ephemeral peers in memory" — the second surface keeps secrets out of `messages.json` for restart-rotating peers.

**Landed (2026-05-24):**

- [x] `jac.profiles.A2APeerConfig.auth` is now a **discriminated union** — `BearerAuth | ApiKeyAuth | OAuth2ClientCredentialsAuth` via pydantic `Discriminator("type")`. The legacy `token: <str>` shorthand auto-promotes to `BearerAuth` via a `model_validator(mode="before")` — zero migration burden on existing configs. Side-by-side `token:` + `auth:` is rejected (ambiguous).
- [x] `jac.capabilities.a2a.auth_strategies` (new) — `AuthStrategy` Protocol (`async def headers_for() -> dict[str, str]`) + three implementations: `BearerStrategy` (static), `ApiKeyStrategy` (custom header name + value), `OAuth2ClientCredentialsStrategy` (RFC 6749 §4.4 — POST to token_url with HTTP Basic id:secret, parse `access_token` + `expires_in`, cache per-strategy in memory, lazy refresh with 30s slack). `make_strategy(auth)` dispatches by `isinstance`.
- [x] `${ENV_VAR}` reference expansion via `_resolve_env(value, field=...)` — works in every credential field (bearer token, api_key value, oauth2 client_id / client_secret / token_url / scope). Missing env vars raise `JacConfigError` listing every missing var so the operator can fix in one pass.
- [x] `A2ACapability` split: `profile_peers` (from YAML) + `session_peers` (from slash); `peers` is now a `@property` returning the merged view (session overrides profile). Strategy cache keyed by `id(peer.auth)` — instance-identity indexing means `/profile` rebuilds + slash-add operations naturally invalidate (new instance → new id → new strategy → fresh OAuth2 token fetch).
- [x] `/a2a peer add NAME URL [--bearer | --api-key HEADER | --oauth2 TOKEN_URL CLIENT_ID [--scope X]]` slash — registers a session-scoped peer. **Secrets are NEVER passed on the command line** — prompted via `getpass.getpass()` so the value doesn't echo + doesn't land in shell history or prompt-toolkit history. With no auth flag, peer is added unauthenticated (works against `--unsafe` peers only).
- [x] `/a2a peer remove NAME` slash — drops a session peer; reverts to the profile peer of the same name if one exists.
- [x] `/a2a peers` rewritten — shows merged view with `[session]` / `[profile]` provenance tags (Rich brackets escaped with `r"\["` so they aren't stripped). Session entry shadowing a profile entry renders the shadowed row greyed-out underneath.
- [x] `client.py` `a2a_call` refactored: `_ResolvedTarget` now carries the resolved `A2APeerConfig` (not a bearer token); `build_outbound_tools` accepts a `strategy_provider` callable for the capability's cached lookup; on call, the strategy's `headers_for()` is awaited and merged into the request headers. Bearer-only path is dead — all auth flows through the strategy interface.
- [x] REPL passes `profile_peers` (instead of legacy `peers`) into `make_a2a_capability` and refreshes via in-place `.clear() + .update()` on `/profile` rebuild. Session peers survive profile switches (intentional — they're the operator's per-session overrides).
- [x] `gru_system.md` "Auth model" section rewritten: explicit on the two-surface design (stable in profile, ephemeral via `/a2a peer add`), `getpass` prompt for secrets, "you never handle credentials" guarantee. Slash commands section gains the `/a2a peer add|remove` entries.
- [x] **31 new tests** across 2 files (17 in `test_a2a_auth_strategies.py`, 14 in `test_a2a_slash.py`): strategy dispatch, bearer/api_key with env-var expansion, OAuth2 end-to-end via in-process Starlette token endpoint (correct grant_type, Basic auth header, scope passthrough, caching across calls, expiry-driven refresh, 4xx / non-JSON / no-access-token error paths, env-var expansion in every field), plus all `/a2a peer add` variants (unauth / bearer / api_key / oauth2), invalid URL/name/flag rejection, cancellation via empty input, shadowing with loud warning, `/a2a peer remove` with revert-to-profile, peers listing with shadowed-row rendering.
- [x] All 296 tests pass at last count (263 prior + 31 new at PR3; +2 since); `just check` clean (ruff format ✓, lint ✓, ty ✓).
- [x] architecture.md §11 **D31** recorded — pluggable auth strategies + in-memory/config split + privacy guarantee.

### Phase 4.d.2 — `resolve_target` auto-promotes URL → configured peer (hotfix) ✅

**Why:** real-world follow-up on 2026-05-26. The user added an authenticated session peer (`/a2a peer add project-a http://127.0.0.1:8001 --bearer`), then asked Gru in plain English to "use a2a_call on peer project-a". Gru remembered the URL from a prior `a2a_discover` step and called `a2a_call(peer_or_url="http://127.0.0.1:8001", ...)` instead of using the peer name. Our `resolve_target` only matched by name — raw URLs went unauthenticated by design — so the request landed at the server with no `Authorization` header and got a 401. The model interpretation was reasonable but our resolver was too literal.

**Landed (2026-05-26):**

- [x] `resolve_target` ([client.py](../src/jac/capabilities/a2a/client.py)): when `peer_or_url` is a raw http(s):// URL, check if exactly one configured peer's URL matches (after trailing-slash normalization). If so, promote — apply that peer's auth strategy + use its name in `display`. Zero or multi-match cases fall through to a raw call (we don't guess on ambiguous configs). The model's "use the URL I just discovered" instinct now works correctly without surprising anyone who deliberately wants an unauthenticated raw call (those just don't have a matching configured peer).
- [x] [`gru_system.md`](../src/jac/prompts/gru_system.md): added explicit guidance that the peer NAME is preferred over the URL. Surfaces the auto-promote as a safety net, not the intended path.
- [x] **5 new tests** in [`test_a2a_client.py`](../tests/test_a2a_client.py): exact URL match promotes + carries token, trailing-slash normalization (both directions), no-match stays raw, multi-match falls through to raw, end-to-end auth header injection when calling with a URL that matches a configured peer. 324 tests pass; `just check` clean.

---

### Phase 4.d.1 — Outbound `a2a_call` polls until terminal (hotfix) ✅

**Why:** real-world test on 2026-05-26 surfaced the issue: project-A served `jac a2a serve --unsafe`, project-B's interactive Gru called `a2a_call` and got back the immediate `{state: "submitted"}` envelope. The guest *did* run the work (audit log shows 4848ms / 14748 tokens) — but the calling Gru never saw the answer, because our client returned as soon as fasta2a acknowledged the submission. Every follow-up "can you check the status?" was a fresh `message/send` against the same context, producing more tasks instead of retrieving the original answer.

**Landed (2026-05-26):**

- [x] `result.usage()` → `result.usage` in [`AuditingAgentWorker.run_task`](../src/jac/capabilities/a2a/server.py) — pydantic-ai exposed it as a property; method form raised a `PydanticAIDeprecationWarning`.
- [x] [`client.py`](../src/jac/capabilities/a2a/client.py) — `a2a_call` now keeps the httpx `AsyncClient` open across the initial `message/send` AND any follow-up `tasks/get` polls. New `_wait_for_terminal()` helper drives the loop: exponential backoff from 250ms → 2s (1.5x), bounded by a shared `_CALL_TIMEOUT_S` deadline (bumped 60s → 120s). Returns early if state is already terminal or `input-required` / `auth-required`. On timeout, returns the last task envelope with `_jac_timeout: true` so the calling Gru can distinguish stale state from fresh terminal.
- [x] Auth headers are reused on every `tasks/get` so authenticated peers (bearer / api_key / OAuth2 strategies) work transparently through the poll loop.
- [x] [`gru_system.md`](../src/jac/prompts/gru_system.md) "When to call `a2a_call`" section rewritten: the tool now blocks until terminal; the model is told to read `artifacts[].parts[].text` and `history[]` agent messages for the actual answer, and to react if it sees `_jac_timeout: true`.
- [x] **7 new tests** in [`test_a2a_client.py`](../tests/test_a2a_client.py): polling transitions (submitted → working → working → completed), inline-terminal skip (no tasks/get when peer responds synchronously), `input-required` early return, auth header propagation to tasks/get, timeout returns partial with marker, task id pass-through, bare `Message` response (no status block) handled without polling. All 319 tests pass; `just check` clean.

---

### Phase 4.d — Polish: status, audit, budget integration (PR4) ✅

**Why:** the bits that make A2A *operable* rather than just *functional*. Visibility into running servers, integration with the budget system so guest calls aren't a budget loophole, retention enforcement so audit files don't grow forever. (Was Phase 4.c before D31; renamed when auth strategies pushed in front of it.)

**Landed (2026-05-26):**

- [x] `/a2a status` ([slash/handlers/a2a/status.py](../src/jac/cli/slash/handlers/a2a/status.py)) — three rendering blocks: server (URL, bind, auth, card), peers (merged-count with profile/session split), inbound (last 5 calls tailed from `inbound.jsonl` with timestamp / peer / state / duration / preview). Missing log file or malformed rows degrade gracefully.
- [x] **Budget integration.** [`UsageTracker.add_external(in, out)`](../src/jac/runtime/usage.py) tracks A2A guest usage on a new `_ExternalCounters` field. `project_total_tokens` now = `baseline + counters.total + external.total`; `session_input` / `session_total` are deliberately untouched so a peer can't bloat the host session's view of its own work. JSONL rows tag with `kind` (`session` / `a2a_guest`). `AuditingAgentWorker.run_task` was rewritten as a full override (not `super().run_task()`) so we can capture `result.usage()` between the agent run and the storage update — the same hook also fills the real `tokens_used` in `A2AInboundCompleted` events and `InboundRecord` rows, fixing the PR1 hardcoded `0`. The capability is plumbed via `A2ACapability.usage_tracker` (attached by the REPL right after `make_usage_tracker`); session swap (`/clear`, `/resume`) re-attaches the fresh tracker. `/tokens` gains a dedicated "a2a guest" line when external usage > 0.
- [x] **Retention timer.** `A2AServer._retention_loop` is a 1-hour periodic `asyncio.create_task` spawned in `start()` when `retention_days > 0`. Errors inside the prune call are logged and the loop keeps running; `stop()` cancels and awaits before tearing the uvicorn task down. Skipped entirely when `retention_days == 0` (keep-forever mode).
- [x] **OAuth2 fresh-token visibility.** New `A2AOutboundTokenMinted(token_url, peer_name, expires_in_s)` event in `runtime/events.py`. `OAuth2ClientCredentialsStrategy` gained `bus` + `peer_name` fields and emits the event from `_refresh()` after the IDP returns an access token. `make_strategy()` accepts `bus=` + `peer_name=` and threads them to OAuth2 only — bearer / api_key ignore them. Capability's `_strategy_for()` forwards its bus and the peer's resolved name; `client.py` strategy_provider signature is now `(peer, peer_name) -> AuthStrategy | None`. Renderer paints a muted `[a2a token]` line.
- [x] **Architecture diagrams.** `docs/architecture.md` §6 (inbound flow) and §8 (outbound flow + auth strategy table) added, with the privacy guarantees (credentials never reach LLM context; session peers stay in memory; per-peer token cache).
- [x] **Tests.** 16 new across 4 files — `test_usage.py` (+5: external bumps project not session, jsonl `kind` marker, session/external dedup, project_total triggering, no session-budget triggering), `test_a2a_slash.py` (+4: peer count, last-5 rendering, empty log, malformed-row tolerance), `test_a2a_server.py` (+3: retention task lifecycle, retention disabled when days=0, usage tracker plumbing), `test_a2a_auth_strategies.py` (+4: token-minted event on refresh, no event on cached hit, `make_strategy` threads bus/peer into OAuth2 only, bearer ignores bus). All 312 tests pass; `just check` clean.

### Phase 4.e — OIDC + GCP ID tokens (PR5, after PR4) ⏸

**Why:** Phase 4.c's strategy Protocol opens the door; this phase walks through it. OIDC discovery (pull token endpoint from `.well-known/openid-configuration`) unlocks any IDP that advertises it (Okta, Auth0, Google, Microsoft Entra, Keycloak). GCP ID tokens unlock Cloud Run / App Engine — the second-most-common cloud A2A deployment target after Azure.

- [ ] `OidcAuth` config model: `issuer` (discovery URL base) + `client_id` + `client_secret` + `scope`. Fetches `<issuer>/.well-known/openid-configuration` to learn the token endpoint, then reuses the OAuth2 client_credentials path under the hood.
- [ ] `GcpIdTokenAuth` config model: `audience` (the Cloud Run URL or service account audience). Uses `google-auth` to mint an ID token via the metadata service (inside GCP) or service account credentials (anywhere else).
- [ ] Add `google-auth` as an optional dep (`pip install 'jac[gcp]'` — keeps the base wheel small).
- [ ] Two new strategy classes implementing `AuthStrategy`; `make_strategy` dispatch grows two branches.
- [ ] Documentation: `gru_system.md` auth section gains a "supported strategies" reference; user guide gets a "configuring Azure / GCP / Okta peers" walkthrough.

### Phase 4.1 — Auto-publish community Skills (after Phase 3) ⏸

**Why:** once Phase 3 ships the community-format skill loader, the AgentCard's `skills:` list can advertise real capabilities instead of one generic placeholder. This is what makes Phase 3 and Phase 4 reinforce each other.

- [ ] Loaded inline-mode community skills (from `<repo>/.agents/skills/` and `~/.jac/skills/`) auto-appear as `Skill` entries in the AgentCard; frontmatter `description` → A2A `Skill.description`, frontmatter `name` → A2A `Skill.id`
- [ ] Optional per-skill enable/disable via `a2a.guest.advertise_skills: [name1, name2]` (default: all installed)
- [ ] Test: skill loader → card builder integration

---
