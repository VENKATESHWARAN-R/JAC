# Module strategy

> **Audience:** anyone adding code to JAC. Read this before placing a new file or splitting an existing one.
>
> This is the canonical rulebook for **where things live and why**. The shape of the codebase was locked in 2026-05-24 (v0.2 refactor). When in doubt, this doc wins over instinct; if reality and this doc disagree, update the doc in the same change.

The goal is conceptual clarity: a new contributor (or AI agent) should be able to predict where a piece of code belongs without grepping. The rules below codify the boundaries.

## Folder rulebook

| Folder | What lives here | What does NOT |
| --- | --- | --- |
| [`capabilities/`](../../src/jac/capabilities/) | Pydantic AI `AbstractCapability` subclasses (or factories returning one). Each provides tools, instructions, or lifecycle hooks **to an agent**. May import from `runtime/`, `workspace/`. | Never imports from `cli/`. A top-level file here with no `Capability` subclass/factory belongs in `runtime/` or `workspace/` instead — **but a capability may be a package** (e.g. `capabilities/a2a/`) whose `Capability` lives in `__init__.py` and whose siblings (`server.py`, `client.py`, …) are submodules, not standalone capabilities. |
| [`runtime/`](../../src/jac/runtime/) | Agent-execution layer: `EventBus`, event types, `Session` (message persistence), `UsageTracker`, `build_gru`, interaction-mode policy (`modes.py`), session-lifetime wiring primitives (`hooks.py`, `approval.py`, `observability.py`). | No CLI imports. No prompt/render concerns. |
| [`workspace/`](../../src/jac/workspace/) | Filesystem concerns: paths SSOT, layered config, prompts, AGENTS.md/memory.md loaders, the session-id `ContextVar`. | No agent loop, no rendering. |
| [`tools/`](../../src/jac/tools/) | `@jac_tool` decorator + the toolset construction/enforcement primitives (`jac_function_toolset`, `summarizing_wrap`, `restrict_toolset`). May import `runtime/tool_summarize` for the summarizing wrapper. | No tool implementations (those live in their capability). |
| [`providers/`](../../src/jac/providers/) | Provider-catalog code: `ProviderRegistry` over `providers.yaml` — credential inference (prefix → required env) + tier pricing for the summarizer gate. Import-light. | No CLI / capability imports; no agent loop. |
| [`cli/`](../../src/jac/cli/) | Anything a human sees: Typer commands, REPL loop, renderer, status bar, prompt-toolkit. Full import rights to the rest of the tree. | No model calls outside the REPL turn loop. |
| [`cli/slash/`](../../src/jac/cli/slash/) | Slash handlers: synchronous, user-initiated REPL-state mutations. Return `SlashResult`. | No model calls. May read + mutate the state of capabilities passed on `SlashContext` in place (see the rule below). Must **not** drive a Gru rebuild or session-lifecycle change directly — those return a typed `SlashResult`. |
| top-level `*.py` ([`profiles.py`](../../src/jac/profiles.py), [`profiles_io.py`](../../src/jac/profiles_io.py), [`profiles_crud.py`](../../src/jac/profiles_crud.py), [`config.py`](../../src/jac/config.py), [`errors.py`](../../src/jac/errors.py), [`secrets.py`](../../src/jac/secrets.py)) | Cross-cutting schema + config + errors used everywhere. Keep import-light. | No CLI / capability imports. |

### One rule per folder, applied at the file level

- A file in `capabilities/` **must** export a `Capability` subclass or a factory returning one — **unless it is a submodule of a capability package** (e.g. `capabilities/a2a/server.py`), whose `Capability` lives in the package `__init__.py`. A standalone top-level file with no capability belongs in `runtime/` (lifecycle wiring) or `workspace/` (paths / config).
- A file in `cli/slash/handlers/` **must** correspond to a registered slash command. Filename = command name. Multi-subcommand commands (only `/a2a` today) get a subpackage where each subcommand is one file plus `_args.py` / `_shared.py` for helpers.
- A file in `runtime/` **must** be part of the agent-execution loop or session lifecycle. Not "things that don't fit elsewhere."

## Slash command vs Capability

The single most common "where does this go" question. The decision tree:

```
Is the LLM going to invoke it?
├── Yes → Capability (under capabilities/)
│         Provides tools, instructions, or lifecycle hooks. Subject to HITL approval where mutating.
└── No → Operator-initiated; sync; affects REPL session state?
         ├── Yes → Slash command (under cli/slash/handlers/)
         │         Returns SlashResult. NEVER calls a model. May mutate a
         │         SlashContext capability in place; must NOT rebuild Gru or
         │         change session lifecycle directly (return a SlashResult).
         └── No → Probably a Typer subcommand (under cli/, top-level)
```

### Concrete examples

- `read_file`, `remember`, `web_search`, `a2a_call` → **capability tools**. The LLM decides when to invoke.
- `/profile`, `/model`, `/sessions`, `/resume`, `/clear`, `/budget`, `/a2a serve` → **slash commands**. The operator drives them; the model has no say.
- `jac init`, `jac profiles list`, `jac keys set` → **Typer commands**. Not interactive REPL state; out-of-session config management.

### Where the line actually is: in-place mutation vs. `SlashResult`

A slash handler receives the live capabilities it needs on `SlashContext`
(`usage_tracker`, `a2a`, `skills`, `mcp`, …). It **may read and mutate those
in place** — `/budget extend` calls `tracker.extend(...)`, `/skill reload`
calls `skills.reload()`, `/mcp enable` calls `mcp.set_enabled(...)`, `/a2a peer
remove` calls `cap.remove_session_peer(...)`. That's the simplest correct shape
and it's the as-built convention; forcing a typed result for every in-session
tweak would be ceremony without value.

What a handler must **not** do directly is anything the REPL owns: **rebuilding
Gru** (toolset/instruction changes) or **session lifecycle** (switch / clear /
start-server). Those return a typed `SlashResult` (`RebuildGru`,
`RefreshToolsets`, `SwitchSession`, `StartA2AServer`, `CompactNow`, …) that the
REPL applies — because the handler can't see the Gru instance or the turn loop,
and shouldn't. See [`cli/slash/result.py`](../../src/jac/cli/slash/result.py)
for the catalog.

Rule of thumb: **mutate the capability you were handed; return a result for
anything that touches Gru or the session.**

## Slash command file layout

One file per command — opening [`cli/slash/handlers/`](../../src/jac/cli/slash/handlers/) is your slash-command catalog.

| File | Command(s) |
| --- | --- |
| `meta.py` | `/help` + `/exit` (kept paired — both 1-line REPL meta-ops) |
| `sessions.py` | `/sessions` |
| `resume.py` | `/resume` |
| `clear.py` | `/clear` |
| `profile.py` | `/profile` |
| `model.py` | `/model` |
| `budget.py` | `/budget` |
| `tokens.py` | `/tokens` |
| `compact.py` | `/compact` |
| `context.py` | `/context` |
| `mode.py` | `/mode` |
| `memory.py` | `/memory` (read-only view) |
| `memory_edit.py` | `/remember` + `/forget` (paired — both 1-bullet audited edits) |
| `skill.py` | `/skill` |
| `spawns.py` | `/spawns` |
| `mcp.py` | `/mcp` |
| `a2a/` (subpackage) | `/a2a serve|stop|status|token|peers|peer …` — one file per subcommand, plus `_args.py` (parsers) and `_shared.py` (rendering / prompts) |

(`meta.py` and `memory_edit.py` are the two deliberate exceptions to strict filename=command — each pairs two tightly-related one-liners. The set is drift-guarded by `just drift`.)

A new slash needs three things:
1. A new module in `handlers/` named after the command.
2. A `@register` decorator on the handler function (see [`cli/slash/registry.py`](../../src/jac/cli/slash/registry.py)).
3. A side-effect import in [`handlers/__init__.py`](../../src/jac/cli/slash/handlers/__init__.py) so the decorator fires at import time.

## Self-documenting modules

Every Python file in `src/jac/` carries a top-level docstring (1–4 lines) explaining what it owns and its key public exports. This is enforced informally during code review; the smoke check is:

```python
import importlib, pkgutil, jac
for m in pkgutil.walk_packages(jac.__path__, "jac."):
    mod = importlib.import_module(m.name)
    assert mod.__doc__, f"{m.name} is missing a module docstring"
```

The docstring is for **what the module owns and why**, not how. Don't restate function bodies. Do note non-obvious invariants ("this is the only writer of X"), cross-module collaboration ("emits events consumed by Y"), and architectural decisions the file implements (cite D1–D47 from `architecture.md`).

## When this doc and the code disagree

`architecture.md` §5 owns decisions D1–D47 (the *what*). This doc owns the *where*. If you find yourself moving a file in a way that violates the table at the top, either:

- the rule is wrong (update this doc in the same change), or
- the move is wrong (don't make it).

Code-review pushback citing this doc is normal and expected.

## Related

- [`architecture.md`](../architecture.md) — locked decisions D1–D47; the *why*.
- [`codebase-map.md`](codebase-map.md) — the as-built module tree.
- [`capabilities.md`](capabilities.md) — patterns for writing a new capability.
- [`contributing.md`](contributing.md) — `just` workflow and conventions.
