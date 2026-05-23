"""Web tools — search and fetch.

Two read-only tools that give Gru (and eventually the researcher minion)
access to the open web:

- ``web_search(reason, query, max_results=5)`` — text search; returns
  ``{title, url, snippet}``. Backend is selected at call time:

  - **Tavily** when ``TAVILY_API_KEY`` is in the environment (preferred,
    higher quality results, no rate-limit surprises).
  - **DuckDuckGo** otherwise (free, no key, ships as the default).

  The tool's signature and return shape are identical across backends so
  Gru never has to know which one ran. Tavily's ``content`` field is
  mapped to ``snippet`` to match DDG's ``body``.

- ``fetch_url(reason, url)`` — fetch a URL and return its main content
  as Markdown. Reuses pydantic-ai's ``WebFetchLocalTool`` so we inherit
  its SSRF protection, response-size cap, and HTML-to-markdown pipeline.

Why we wrap the upstream tools instead of using them directly: every
JAC tool must accept ``reason: str`` as its first parameter
(docs/architecture.md §6a). ``duckduckgo_search_tool()``,
``tavily_search_tool()``, and ``web_fetch_tool()`` ship as bare ``Tool``
objects without that contract, so we re-implement the small surface and
delegate to the upstream pieces (``DDGS``, ``AsyncTavilyClient``,
``WebFetchLocalTool``) for the heavy lifting.

**No approval required.** Both tools are read-only; if abuse becomes a
problem we'll revisit (e.g. an allowlist for ``fetch_url``).

Backend errors are **not** silently swallowed. If the user explicitly
opted into Tavily (set the API key) and the request fails, we surface
the error rather than falling back to DDG — a silent fallback would
mask quota / auth / network issues.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from typing import Any, TypedDict

import anyio.to_thread
from ddgs import DDGS
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.common_tools.web_fetch import WebFetchLocalTool, WebFetchResult
from pydantic_ai.messages import BinaryContent

from jac.tools import jac_function_toolset, jac_tool

_DEFAULT_MAX_RESULTS = 5
_MAX_RESULTS_HARD_CAP = 10
_FETCH_MAX_CHARS = 50_000
_FETCH_TIMEOUT_S = 30
_TAVILY_KEY_ENV = "TAVILY_API_KEY"


class _SearchHit(TypedDict):
    title: str
    url: str
    snippet: str


async def _search_tavily(query: str, max_results: int, api_key: str) -> list[_SearchHit]:
    """Run a Tavily search and map results to the shared ``_SearchHit`` shape.

    Uses ``AsyncTavilyClient`` directly (pydantic-ai's ``tavily_search_tool``
    is a bare ``Tool`` factory — we want the client, not the tool wrapper —
    so we keep our own ``@jac_tool`` shape with ``reason:``).
    """
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key)
    results: dict[str, Any] = await client.search(query, max_results=max_results)
    return [
        _SearchHit(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            snippet=str(r.get("content", "")),
        )
        for r in results.get("results", [])
    ]


async def _search_ddg(query: str, max_results: int) -> list[_SearchHit]:
    """Run a DuckDuckGo search via the synchronous ``DDGS`` client.

    The client is sync; we offload to a thread so the agent loop isn't
    blocked. Result keys are mapped to ``_SearchHit`` (``href`` → ``url``,
    ``body`` → ``snippet``).
    """
    client = DDGS()
    search = functools.partial(client.text, max_results=max_results)
    raw = await anyio.to_thread.run_sync(search, query)
    return [
        _SearchHit(
            title=str(r.get("title", "")),
            url=str(r.get("href", "")),
            snippet=str(r.get("body", "")),
        )
        for r in raw
    ]


@jac_tool
async def web_search(
    reason: str, query: str, max_results: int = _DEFAULT_MAX_RESULTS
) -> list[_SearchHit]:
    """Search the web — Tavily when configured, DuckDuckGo otherwise.

    Use when you need information that isn't in this repo — checking
    library APIs, verifying error messages, finding documentation, etc.
    Don't use it for facts the user has already given you or that you
    can derive from local files.

    Backend selection is automatic and invisible: if ``TAVILY_API_KEY``
    is set, Tavily runs; otherwise DDG. Return shape is identical.

    Args:
        reason: One-sentence justification.
        query: Search query.
        max_results: How many results to return (1-10). Default 5.

    Returns:
        A list of ``{title, url, snippet}`` dicts.
    """
    q = query.strip()
    if not q:
        raise ValueError("`query` must not be empty.")
    if not 1 <= max_results <= _MAX_RESULTS_HARD_CAP:
        raise ValueError(f"`max_results` must be 1-{_MAX_RESULTS_HARD_CAP}; got {max_results}.")
    api_key = os.environ.get(_TAVILY_KEY_ENV)
    if api_key:
        return await _search_tavily(q, max_results, api_key)
    return await _search_ddg(q, max_results)


@jac_tool
async def fetch_url(reason: str, url: str) -> str:
    """Fetch ``url`` and return the page content as Markdown.

    SSRF-protected (won't follow redirects to private IPs). HTML is
    converted to Markdown via ``markdownify``; JSON is returned in a
    fenced code block; binary payloads are rejected.

    Args:
        reason: One-sentence justification.
        url: The URL to fetch.

    Returns:
        Markdown text prefixed with the page title (when available).
        Content over ~50k characters is truncated with a notice.
    """
    if not url.strip():
        raise ValueError("`url` must not be empty.")
    impl = WebFetchLocalTool(
        max_content_length=_FETCH_MAX_CHARS,
        allow_local_urls=False,
        timeout=_FETCH_TIMEOUT_S,
    )
    result = await impl(url)
    if isinstance(result, BinaryContent):
        raise ValueError(
            f"refusing to return binary content from {url} "
            f"(media-type={result.media_type}); fetch a different URL."
        )
    assert isinstance(result, dict)  # WebFetchResult is a TypedDict
    typed: WebFetchResult = result
    header = f"# {typed['title']}\n\n" if typed["title"] else ""
    return f"{header}{typed['content']}"


@dataclass
class WebCapability(AbstractCapability[Any]):
    """Read-only web tools: ``web_search`` and ``fetch_url``."""

    def get_toolset(self) -> Any:
        return jac_function_toolset(web_search, fetch_url)
