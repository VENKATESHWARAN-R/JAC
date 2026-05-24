"""Tests for Phase 1.7.h — Tavily/DDG backend selection in ``web_search``.

The tool's *signature* and *return shape* are identical across backends —
that's the contract. We assert two things:

1. When ``TAVILY_API_KEY`` is in the env, the Tavily branch runs and DDG
   doesn't.
2. When it's absent, the DDG branch runs and Tavily doesn't.

We monkeypatch both clients so the tests never hit the network.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest

from jac.capabilities import web as web_mod


class _FakeAsyncTavilyClient:
    """Records the search query + returns canned ``{title, url, content}`` hits."""

    instances: ClassVar[list[_FakeAsyncTavilyClient]] = []

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.queries: list[str] = []
        _FakeAsyncTavilyClient.instances.append(self)

    async def search(self, query: str, **_kw: Any) -> dict[str, Any]:
        self.queries.append(query)
        return {
            "results": [
                {
                    "title": "Tavily Result",
                    "url": "https://tavily.example/1",
                    "content": "tavily snippet",
                    "score": 0.9,
                }
            ]
        }


class _FakeDDGS:
    """Records the search query + returns canned DDG-shaped hits."""

    instances: ClassVar[list[_FakeDDGS]] = []

    def __init__(self) -> None:
        self.queries: list[str] = []
        _FakeDDGS.instances.append(self)

    def text(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
        self.queries.append(query)
        return [
            {
                "title": "DDG Result",
                "href": "https://ddg.example/1",
                "body": "ddg snippet",
            }
        ]


@pytest.fixture(autouse=True)
def _reset_recorders() -> None:
    _FakeAsyncTavilyClient.instances.clear()
    _FakeDDGS.instances.clear()


@pytest.fixture
def patched_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap real backends for the fakes for the duration of one test."""
    # Patch the deferred import inside _search_tavily.
    import tavily

    monkeypatch.setattr(tavily, "AsyncTavilyClient", _FakeAsyncTavilyClient)
    # DDGS is imported at module scope in web.py.
    monkeypatch.setattr(web_mod, "DDGS", _FakeDDGS)
    # Make sure no stray TAVILY_API_KEY leaks across tests.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


# ---------- backend selection ----------


def test_web_search_uses_ddg_when_no_tavily_key(
    patched_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    hits = asyncio.run(web_mod.web_search(reason="checking the docs", query="pydantic ai"))
    assert len(_FakeDDGS.instances) == 1
    assert _FakeAsyncTavilyClient.instances == []
    assert _FakeDDGS.instances[0].queries == ["pydantic ai"]
    assert hits == [
        {
            "title": "DDG Result",
            "url": "https://ddg.example/1",
            "snippet": "ddg snippet",
        }
    ]


def test_web_search_uses_tavily_when_key_set(
    patched_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key-123")
    hits = asyncio.run(web_mod.web_search(reason="checking the docs", query="pydantic ai"))
    assert len(_FakeAsyncTavilyClient.instances) == 1
    assert _FakeDDGS.instances == []
    assert _FakeAsyncTavilyClient.instances[0].api_key == "test-key-123"
    assert _FakeAsyncTavilyClient.instances[0].queries == ["pydantic ai"]
    # Tavily's `content` is mapped to our `snippet` so the shape stays uniform.
    assert hits == [
        {
            "title": "Tavily Result",
            "url": "https://tavily.example/1",
            "snippet": "tavily snippet",
        }
    ]


def test_web_search_validates_empty_query(
    patched_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    # Validation happens BEFORE backend selection — no client is constructed.
    with pytest.raises(ValueError, match="must not be empty"):
        asyncio.run(web_mod.web_search(reason="x", query="   "))
    assert _FakeAsyncTavilyClient.instances == []
    assert _FakeDDGS.instances == []


def test_web_search_validates_max_results_range(
    patched_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="max_results"):
        asyncio.run(web_mod.web_search(reason="x", query="q", max_results=0))
    with pytest.raises(ValueError, match="max_results"):
        asyncio.run(web_mod.web_search(reason="x", query="q", max_results=11))


def test_tavily_errors_surface_not_silently_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User opted into Tavily; an upstream failure must not silently fall
    back to DDG — that would mask quota / auth / network issues."""

    class _BrokenTavilyClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        async def search(self, query: str, **_kw: Any) -> dict[str, Any]:
            raise RuntimeError("tavily upstream is down")

    import tavily

    monkeypatch.setattr(tavily, "AsyncTavilyClient", _BrokenTavilyClient)
    monkeypatch.setattr(web_mod, "DDGS", _FakeDDGS)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    with pytest.raises(RuntimeError, match="tavily upstream is down"):
        asyncio.run(web_mod.web_search(reason="x", query="anything"))
    # DDG was NOT consulted as a fallback.
    assert _FakeDDGS.instances == []
