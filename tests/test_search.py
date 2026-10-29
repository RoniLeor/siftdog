import httpx
from conftest import FakeProvider
from fastapi.testclient import TestClient

from siftdog.app import create_app
from siftdog.config import Settings
from siftdog.fetch import Fetcher
from siftdog.providers import Hit
from siftdog.search import SearchService

HEAVY = "https://a.example/"  # the term in every paragraph, twice
LIGHT = "https://b.example/"  # the term once, in one of three paragraphs
FILLER = "unrelated text " * 20  # > 250 chars so each paragraph becomes its own chunk
PAGES = {
    HEAVY: "\n\n".join([f"install install {FILLER}"] * 3),
    LIGHT: "\n\n".join([f"install {FILLER}", f"other {FILLER}", f"more {FILLER}"]),
}


def test_advanced_ranking_scores_pages_in_one_corpus():
    def handler(request: httpx.Request) -> httpx.Response:
        text = PAGES[str(request.url)]
        return httpx.Response(200, text=text, headers={"content-type": "text/plain"})

    settings = Settings(allow_private_networks=True, _env_file=None)
    fetcher = Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)), settings)
    hits = [Hit("A", HEAVY, "a"), Hit("B", LIGHT, "b")]
    service = SearchService(FakeProvider(hits), fetcher, None)
    with TestClient(create_app(settings, service)) as client:
        body = client.post("/search", json={"query": "install", "search_depth": "advanced"}).json()
    # Scored page by page, LIGHT won: its lone matching chunk had a high in-page IDF.
    assert [r["url"] for r in body["results"]] == [HEAVY, LIGHT]
