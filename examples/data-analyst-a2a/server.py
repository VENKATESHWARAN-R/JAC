"""Data-analyst A2A agent — demo peer for JAC.

Self-contained fasta2a + pydantic-ai server exposing one tool:
``analyze_csv(reason, question, csv_path)``. Accepts CSV uploads via
A2A ``FilePart`` (bytes), loads with pandas, computes a summary, and
optionally returns a matplotlib chart as a ``FilePart`` artifact.

Designed as a reference: standalone, no JAC dependency, ~200 LOC.
Read top-to-bottom to understand the A2A wire on the server side.

Usage:

.. code-block:: bash

    export ANTHROPIC_API_KEY=sk-...
    # Optional: pin an auth token (otherwise --unsafe)
    export ANALYST_BEARER=$(openssl rand -hex 24)

    uv run server.py --model anthropic:claude-sonnet-4-6

From JAC (in another terminal):

.. code-block:: text

    /a2a peer add analyst http://localhost:8002 --bearer
    (paste $ANALYST_BEARER when prompted)

    Use a2a_call on peer analyst with
    files=["./examples/data-analyst-a2a/sample-data.csv"] and ask
    "What's the revenue trend across the year? Plot it."
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
import logging
import os
import re
import secrets
import signal
import tempfile
import uuid
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd
import uvicorn
from fasta2a.broker import InMemoryBroker
from fasta2a.pydantic_ai import AgentWorker, agent_to_a2a
from fasta2a.schema import Artifact, Message, Skill, TaskSendParams
from fasta2a.storage import InMemoryStorage
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

matplotlib.use("Agg")  # headless — no display needed
import matplotlib.pyplot as plt  # noqa: E402

LOG = logging.getLogger("data-analyst-a2a")

# Per-task workdir for incoming CSVs + generated charts. Set in run_task,
# read by the tool and by build_artifacts. ContextVars are async-task-local
# so concurrent inbound calls don't trample each other.
_task_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "task_dir", default=None
)
_task_charts: contextvars.ContextVar[list[Path]] = contextvars.ContextVar(
    "task_charts", default=[]
)


# ---------- Tool: pandas + optional matplotlib ----------


def _wants_chart(question: str) -> bool:
    """Cheap keyword check — was the user asking for a visualization?"""
    return bool(re.search(r"\b(plot|chart|graph|visuali[sz]e|trend)\b", question, re.IGNORECASE))


SYSTEM_PROMPT = """You are a data analyst agent. Callers attach CSV files which JAC has
saved to disk; their paths are listed in an `[a2a attachment]` message in your context.

When asked about the data, call `analyze_csv(reason, question, csv_path)` using one of
those paths. The tool loads the CSV with pandas, computes a summary, and — if the
question implies a visualization — generates a matplotlib chart that is returned to
the caller as a separate file artifact.

Keep your reply terse: a short narrative of what the tool found, no need to repeat
the entire dataframe. If a chart was generated, mention that the caller will receive
it as an attachment.
"""


def make_agent(model_id: str) -> Agent:
    """Build the pydantic-ai agent with the single analyze_csv tool."""
    agent: Agent = Agent(model_id, system_prompt=SYSTEM_PROMPT)

    @agent.tool_plain
    async def analyze_csv(reason: str, question: str, csv_path: str) -> str:
        """Load a CSV with pandas and answer the question about it.

        Args:
            reason: One-sentence justification (telemetry only).
            question: The user's question.
            csv_path: Path to the CSV (taken from an [a2a attachment] message).
        """
        LOG.info("analyze_csv: reason=%r question=%r path=%s", reason, question, csv_path)
        p = Path(csv_path)
        if not p.is_file():
            return f"Error: {csv_path} not found."
        try:
            df = pd.read_csv(p)
        except Exception as exc:  # noqa: BLE001 — the agent reports failures verbatim
            return f"Error: failed to load {p.name}: {exc}"

        out_lines: list[str] = [
            f"File: {p.name}  shape: {df.shape}  columns: {list(df.columns)}",
            "",
            "Describe (numeric + categorical):",
            df.describe(include="all").to_string(),
        ]

        if _wants_chart(question):
            chart = _make_chart(df, question)
            if chart is not None:
                _task_charts.get().append(chart)
                out_lines.append("")
                out_lines.append(
                    f"Generated chart: {chart.name} — it will be returned to the caller as a FilePart."
                )

        return "\n".join(out_lines)

    return agent


def _make_chart(df: pd.DataFrame, question: str) -> Path | None:
    """Plot numeric columns; save to per-task dir; return the path or None."""
    tdir = _task_dir.get()
    if tdir is None:
        return None
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return None
    out = tdir / f"chart-{uuid.uuid4().hex[:8]}.png"
    fig, ax = plt.subplots(figsize=(8, 4.5))
    numeric.plot(ax=ax, marker="o")
    ax.set_title(question[:80])
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


# ---------- Worker: materialize inbound files + return chart artifacts ----------


class AnalystWorker(AgentWorker):
    """fasta2a AgentWorker that:

    1. Materializes inbound FilePart bytes to a per-task temp dir before the
       agent runs, so the analyze_csv tool can read them with pandas.
    2. Annotates the conversation with the saved paths so the agent picks them
       up naturally.
    3. Appends any chart PNGs generated by the tool as ``FilePart`` artifacts
       on the way out.

    Re-implements ``run_task`` end-to-end (rather than calling ``super()``) so
    we can wrap the agent call with our contextvars. Mirrors JAC's
    ``AuditingAgentWorker`` pattern.
    """

    async def run_task(self, params: TaskSendParams) -> None:
        task = await self.storage.load_task(params["id"])
        if task is None:
            raise ValueError(f"Task {params['id']} not found")
        if task["status"]["state"] != "submitted":
            raise ValueError(
                f"Task {params['id']} already processed (state: {task['status']['state']})"
            )
        await self.storage.update_task(params["id"], state="working")

        with tempfile.TemporaryDirectory(prefix=f"a2a-analyst-{params['id']}-") as tmp:
            tdir = Path(tmp)
            dir_token = _task_dir.set(tdir)
            charts_token = _task_charts.set([])

            try:
                history = await self.storage.load_context(task["context_id"]) or []
                history.extend(self.build_message_history(task.get("history", [])))

                saved = _materialize_inbound_files(task, tdir)
                if saved:
                    note = (
                        "[a2a attachment] The caller attached file(s). Use these paths "
                        "with analyze_csv:\n" + "\n".join(f"- {p}" for p in saved)
                    )
                    history.append(ModelRequest(parts=[UserPromptPart(content=note)]))

                try:
                    result = await self.agent.run(message_history=history)  # type: ignore[arg-type]
                except Exception:
                    await self.storage.update_task(params["id"], state="failed")
                    raise

                await self.storage.update_context(task["context_id"], result.all_messages())

                # Standard text artifact from the agent's final reply.
                text_artifact = Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="analysis",
                    parts=[{"kind": "text", "text": str(result.output)}],  # type: ignore[arg-type]
                )
                artifacts = [text_artifact]

                # One additional artifact per generated chart, with inline bytes.
                for chart_path in _task_charts.get():
                    artifacts.append(_chart_artifact(chart_path))

                a2a_messages: list[Message] = []
                for message in result.new_messages():
                    if isinstance(message, ModelRequest):
                        continue
                    msg_parts = []
                    for mp in message.parts:
                        # Only carry text-ish parts back to the wire. Tool calls
                        # are internal; we don't leak them.
                        text = getattr(mp, "content", None)
                        if isinstance(text, str) and text.strip():
                            msg_parts.append({"kind": "text", "text": text})
                    if msg_parts:
                        a2a_messages.append(
                            Message(
                                role="agent",
                                parts=msg_parts,  # type: ignore[typeddict-item]
                                kind="message",
                                message_id=str(uuid.uuid4()),
                            )
                        )

                await self.storage.update_task(
                    params["id"],
                    state="completed",
                    new_artifacts=artifacts,
                    new_messages=a2a_messages,
                )
            finally:
                _task_dir.reset(dir_token)
                _task_charts.reset(charts_token)


def _materialize_inbound_files(task: dict[str, Any], target_dir: Path) -> list[str]:
    """Decode FileWithBytes parts from the latest user message; save to target_dir."""
    history = task.get("history") or []
    if not history:
        return []
    latest = history[-1]
    if not isinstance(latest, dict) or latest.get("role") != "user":
        return []
    parts = latest.get("parts") or []

    saved: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("kind") != "file":
            continue
        file_obj = part.get("file")
        if not isinstance(file_obj, dict):
            continue
        b64 = file_obj.get("bytes")
        if not isinstance(b64, str) or not b64:
            continue  # URI-only or empty — skip
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:  # noqa: BLE001
            continue
        # Filename: spec `file.name` first, then `metadata.filename`, then fallback.
        raw = file_obj.get("name") or (part.get("metadata") or {}).get("filename") or ""
        name = Path(str(raw)).name if raw else f"file-{uuid.uuid4().hex}.bin"
        # Crude sanitization — strip anything not alnum/dot/dash/underscore.
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).lstrip(".") or f"file-{uuid.uuid4().hex}.bin"
        out = target_dir / name
        out.write_bytes(data)
        saved.append(str(out))
    return saved


def _chart_artifact(chart_path: Path) -> Artifact:
    """Build an A2A Artifact containing a single PNG FilePart with inline bytes."""
    data = chart_path.read_bytes()
    return Artifact(
        artifact_id=str(uuid.uuid4()),
        name=chart_path.name,
        parts=[
            {  # type: ignore[list-item]
                "kind": "file",
                "file": {
                    "name": chart_path.name,
                    "mimeType": "image/png",
                    "bytes": base64.b64encode(data).decode("ascii"),
                },
                "metadata": {"filename": chart_path.name},
            }
        ],
    )


# ---------- Auth middleware ----------


class _BearerMiddleware(BaseHTTPMiddleware):
    """Bearer auth — same shape JAC's server uses, simplified for the demo."""

    def __init__(self, app, *, expected_token: str) -> None:
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        # Card endpoint stays public so peers can discover before authenticating.
        if request.url.path.startswith("/.well-known/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "unauthorized", "detail": "missing bearer"}, status_code=401
            )
        if not secrets.compare_digest(auth.removeprefix("Bearer ").strip(), self._expected):
            return JSONResponse(
                {"error": "unauthorized", "detail": "bad bearer"}, status_code=401
            )
        return await call_next(request)


# ---------- App wiring ----------


def build_app(model_id: str, *, base_url: str, bearer: str | None):
    """Return the Starlette ASGI app + the worker (for explicit lifespan)."""
    agent = make_agent(model_id)
    storage: InMemoryStorage = InMemoryStorage()
    broker = InMemoryBroker()
    worker = AnalystWorker(agent=agent, broker=broker, storage=storage)

    middleware = []
    if bearer is not None:
        middleware.append(Middleware(_BearerMiddleware, expected_token=bearer))

    skill = Skill(
        id="analyze-csv",
        name="CSV Analyst",
        description=(
            "Loads CSV attachments with pandas; computes describe()/shape/columns; "
            "produces a matplotlib chart when the question implies a visualization."
        ),
        tags=["data", "csv", "analytics", "pandas"],
        input_modes=["text/plain", "text/csv"],
        output_modes=["text/plain", "image/png"],
    )

    app = agent_to_a2a(
        agent,
        storage=storage,
        broker=broker,
        name="data-analyst",
        url=base_url,
        version="0.1.0",
        description="Pandas + matplotlib data analyst exposed over A2A (demo for JAC).",
        skills=[skill],
        middleware=middleware,
    )
    return app, worker


# ---------- CLI ----------


def main() -> None:
    parser = argparse.ArgumentParser(description="Data-analyst A2A agent (demo for JAC).")
    parser.add_argument("--model", default=os.getenv("ANALYST_MODEL", "anthropic:claude-sonnet-4-6"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Disable bearer auth — only for local demos.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bearer: str | None = None
    if not args.unsafe:
        bearer = os.environ.get("ANALYST_BEARER")
        if not bearer:
            bearer = secrets.token_urlsafe(32)
            print(f"\n  bearer token (set ANALYST_BEARER to pin): {bearer}\n", flush=True)

    base_url = f"http://{args.host}:{args.port}"
    app, _worker = build_app(args.model, base_url=base_url, bearer=bearer)

    print(f"\n=== data-analyst A2A agent ===\n  URL : {base_url}", flush=True)
    print(f"  card: {base_url}/.well-known/agent-card.json", flush=True)
    print(f"  auth: {'disabled (--unsafe)' if bearer is None else 'bearer'}", flush=True)
    print(f"  model: {args.model}", flush=True)
    print("\n  Ctrl-C to stop\n", flush=True)

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)

    async def _run():
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _trip(*_):
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _trip)
            except NotImplementedError:
                pass  # Windows

        serve_task = asyncio.create_task(server.serve())
        await stop_event.wait()
        server.should_exit = True
        await serve_task

    asyncio.run(_run())


if __name__ == "__main__":
    main()
