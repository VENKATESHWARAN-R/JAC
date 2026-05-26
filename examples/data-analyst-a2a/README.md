# data-analyst-a2a — JAC demo peer

A small, standalone A2A agent that JAC can call to analyze CSV files. Built directly on `fasta2a` + `pydantic-ai` — no JAC dependency — to demonstrate that JAC speaks the wire protocol, not just JAC↔JAC.

## What it does

One tool: `analyze_csv(reason, question, csv_path)`.

1. JAC's `a2a_call(peer, message, files=["data.csv"])` sends the CSV as a `FilePart` with inline bytes.
2. The agent's custom `AnalystWorker` materializes the bytes to a per-task temp dir before the model runs.
3. A synthetic `[a2a attachment]` message tells the agent where the file landed.
4. The agent calls `analyze_csv` with that path; pandas computes the summary; matplotlib generates a chart if the question asks for one.
5. The agent's text reply lands in the response artifact; any chart PNG comes back as a second artifact with `FilePart` bytes.
6. JAC's `a2a_call` auto-saves the chart under `<project>/.agents/a2a/inbound-files/<task_id>/` and surfaces the path in `_jac_saved_files`.

Total round trip: ~5 seconds for the sample CSV against Claude Sonnet.

## Prereqs

- Python 3.13
- `uv` installed
- A Claude / OpenAI / Gemini API key (whichever model you pick)

## Run

```bash
cd examples/data-analyst-a2a

# Pick your provider. Default is anthropic:claude-sonnet-4-6.
export ANTHROPIC_API_KEY=sk-...

# Optional: pin the bearer so JAC can store it in /a2a peer add prompts.
# Otherwise the server prints a fresh one each restart.
export ANALYST_BEARER=$(openssl rand -hex 24)

# Use uv to materialize the venv from pyproject.toml.
uv run server.py
```

You should see:

```
=== data-analyst A2A agent ===
  URL : http://127.0.0.1:8002
  card: http://127.0.0.1:8002/.well-known/agent-card.json
  auth: bearer
  model: anthropic:claude-sonnet-4-6
```

## Drive from JAC

In a separate terminal, in any project with `.git`:

```bash
uv run jac
```

```
» /a2a peer add analyst http://127.0.0.1:8002 --bearer
  bearer token: <paste $ANALYST_BEARER>
✓ session peer added: analyst → http://127.0.0.1:8002  (auth: bearer)

» /a2a peers
```

Send the sample CSV:

```
» Use a2a_call on peer analyst with files=["./examples/data-analyst-a2a/sample-data.csv"]
  and ask "What's the revenue trend across the year? Plot it."
```

What you'll see:

- `[a2a out →] analyst: ...` followed by `[a2a out ✓] completed analyst (~5000ms)`.
- The agent's text summary in JAC's chat output.
- A `_jac_saved_files` entry in the returned task pointing at the chart PNG: `<your-project>/.agents/a2a/inbound-files/<task_id>/chart-xxxx.png`.

Open the PNG in your image viewer.

## Run without auth (quick local demo)

```bash
uv run server.py --unsafe
```

From JAC:

```
» /a2a peer add analyst http://127.0.0.1:8002
no auth flag given; peer will be added without authentication (works only against --unsafe peers).
✓ session peer added: analyst → http://127.0.0.1:8002  (auth: none)
```

## Override the model

```bash
uv run server.py --model openai:gpt-4o
uv run server.py --model google:gemini-2.0-flash
uv run server.py --model openrouter:openai/gpt-4o-mini
```

(Install the matching provider extra: `uv pip install "data-analyst-a2a[openai]"` etc.)

## Read the code

`server.py` is intentionally one file. Top-to-bottom it covers:

1. **Context-vars** for per-task workdir + chart paths (async-task-local; concurrent requests don't trample each other).
2. **`make_agent`** — the pydantic-ai `Agent` with `analyze_csv` as the only tool.
3. **`_make_chart`** — matplotlib over numeric columns; saves to the task's temp dir.
4. **`AnalystWorker`** — re-implements fasta2a's `run_task` to wrap the agent call with file materialization (in), `_strip_binary_content` (clears raw bytes fasta2a put in history before the model adapter sees them — Anthropic rejects `text/csv`), and chart artifacts (out).
5. **`_materialize_inbound_files`** — decodes `FileWithBytes`, sanitizes filenames, writes to disk.
6. **`_strip_binary_content`** — removes `BinaryContent` entries from pydantic-ai history; model adapters only accept `image/*`, `application/pdf`, `text/plain` — everything else (CSV, TOML, octet-stream) crashes the API call.
7. **`_chart_artifact`** — builds an A2A `Artifact` with a `FilePart` of PNG bytes.
8. **`_BearerMiddleware`** — same auth shape JAC uses.
9. **`build_app`** — wires `AnalystWorker` into the app via a custom `_lifespan` passed to `agent_to_a2a`. **Critical:** without this, fasta2a creates a plain `AgentWorker` internally and the custom `run_task` never runs.
10. **CLI + uvicorn lifecycle** — straightforward.

This is the receive-side counterpart to JAC's outbound file plumbing ([`jac/capabilities/a2a/client.py`](../../src/jac/capabilities/a2a/client.py)) and the same approach JAC's own guest server uses (see [`guest_files.py`](../../src/jac/capabilities/a2a/guest_files.py)) — just streamlined for the demo.

## What this does NOT do

- Streaming responses (`message/stream`). fasta2a 0.6.1 doesn't implement them; we declare `streaming: false` in the card by default.
- File URIs (`FileWithUri`). v1 of the wire ships inline bytes both ways; URI fetching needs an SSRF story we haven't built.
- Persistence. Uses `InMemoryStorage`, so contexts vanish on restart. Fine for the demo.
- Authorization beyond the bearer token. A real deployment would put this behind a real IDP — JAC's `--auth oauth2_client_credentials` flow on the calling side already works against any RFC 6749 §4.4 endpoint.

## When this is useful as a template

Use this as the starting point when you want a JAC peer that:

- Owns a specialized domain (data, docs, search, devops, anything).
- Needs file I/O the JAC guest doesn't have.
- Should be deployable independently (Cloud Run, Lambda, Container Apps, etc.).
- Plays nicely with other A2A clients besides JAC — the wire format is open.

Swap `analyze_csv` for whatever your peer should do, keep the worker scaffolding, and you have a production-shaped A2A agent in a single file.
