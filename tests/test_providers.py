import logging

import httpx
import pytest
from conftest import mock_transport
from fastapi.testclient import TestClient

from siftdog.app import create_app
from siftdog.config import Settings
from siftdog.fetch import Fetcher
from siftdog.providers import ProviderError, SearxngProvider
from siftdog.search import SearchService

BLOCKED = [["brave", "Suspended: too many requests"], ["duckduckgo", "Suspended: access denied"]]


def result(n: int) -> dict:
    return {"title": f"T{n}", "url": f"https://site{n}.example/", "content": f"snippet {n}"}


def searxng(pages: dict[int, dict]) -> SearxngProvider:
    """Provider over a fake SearXNG returning `pages[pageno]` as its JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("pageno", 1))
        return httpx.Response(200, json=pages.get(page, {"results": []}))

    return SearxngProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "http://sx")


async def search(provider: SearxngProvider, query: str = "q"):
    return await provider.search(query, topic="general", time_range=None, max_results=5)


async def test_every_engine_failing_is_an_error_not_empty_results():
    provider = searxng({1: {"results": [], "unresponsive_engines": BLOCKED}})
    with pytest.raises(ProviderError) as exc:
        await search(provider)
    assert "brave (Suspended: too many requests)" in str(exc.value)
    assert "duckduckgo (Suspended: access denied)" in str(exc.value)


async def test_partial_failure_returns_results_and_logs(caplog):
    provider = searxng(
        {1: {"results": [result(i) for i in range(6)], "unresponsive_engines": BLOCKED[:1]}}
    )
    with caplog.at_level(logging.WARNING, logger="siftdog.providers"):
        hits = await search(provider, query="private-query-text")
    assert len(hits) == 6
    assert "brave (Suspended: too many requests)" in caplog.text
    assert "private-query-text" not in caplog.text  # engines only; user queries stay out of logs


async def test_failure_on_a_later_page_keeps_earlier_results(caplog):
    provider = searxng(
        {
            1: {"results": [result(1), result(2)]},
            2: {"results": [], "unresponsive_engines": BLOCKED},
        }
    )
    with caplog.at_level(logging.WARNING, logger="siftdog.providers"):
        hits = await search(provider)
    assert [h.url for h in hits] == ["https://site1.example/", "https://site2.example/"]
    assert "duckduckgo" in caplog.text


async def test_no_results_without_failing_engines_is_just_empty():
    assert await search(searxng({1: {"results": [], "unresponsive_engines": []}})) == []


async def test_malformed_unresponsive_entries_are_ignored():
    body = {"results": [result(1)], "unresponsive_engines": [[], "oops", ["bing"]]}
    hits = await search(searxng({1: body}))
    assert len(hits) == 1


def test_search_endpoint_returns_502_when_engines_fail():
    settings = Settings(allow_private_networks=True, _env_file=None)
    provider = searxng({1: {"results": [], "unresponsive_engines": BLOCKED}})
    fetcher = Fetcher(httpx.AsyncClient(transport=mock_transport()), settings)
    app = create_app(settings, SearchService(provider, fetcher, None))
    with TestClient(app) as client:
        resp = client.post("/search", json={"query": "q"})
    assert resp.status_code == 502
    assert "failing engines: brave (Suspended: too many requests)" in resp.json()["detail"]["error"]


async def test_locale_is_sent_as_the_searxng_language():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"results": [result(i) for i in range(5)]})  # one page

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SearxngProvider(client, "http://sx")
    await provider.search("q", topic="general", time_range=None, max_results=5, locale="he-IL")
    await provider.search("q", topic="general", time_range=None, max_results=5)
    assert seen[0]["language"] == "he-IL"
    assert "language" not in seen[1]
