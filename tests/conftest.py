import httpx
import pytest
from fastapi.testclient import TestClient

from siftdog.app import create_app
from siftdog.config import Settings
from siftdog.fetch import Fetcher
from siftdog.providers import Hit
from siftdog.search import SearchService

PAGES = {
    "https://docs.example.com/": """<html><head><title>Example Docs</title></head><body>
        <main><h1>Example Docs</h1>
        <p>Welcome to the example documentation. It covers installation and usage in depth,
        with enough prose for the extractor to consider this the main content of the page.</p>
        <a href="/install">Install</a> <a href="/usage#top">Usage</a>
        <a href="https://other.org/x">External</a> <a href="mailto:a@b.c">Mail</a>
        <img src="/logo.png"></main></body></html>""",
    "https://docs.example.com/install": """<html><head><title>Install</title></head><body>
        <article><h1>Installation</h1>
        <p>Run pip install example to install the package. Python 3.11 or newer is required
        and the installer will pull in every dependency automatically for you.</p>
        <p>Unrelated paragraph about the weather, which is sunny with a light breeze today
        across most of the region according to the latest forecasts available.</p>
        <a href="/install/advanced">Advanced install</a></article></body></html>""",
    "https://docs.example.com/usage": """<html><head><title>Usage</title></head><body>
        <article><h1>Usage</h1><p>Import example and call run() to start processing your
        data. The run function accepts a configuration object with many options.</p>
        </article></body></html>""",
}


def mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in PAGES:
            return httpx.Response(200, html=PAGES[url])
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


class FakeProvider:
    def __init__(self, hits: list[Hit]):
        self.hits = hits
        self.queries: list[str] = []
        self.locales: list[str | None] = []

    async def search(self, query, *, topic, time_range, max_results, locale=None):
        self.queries.append(query)
        self.locales.append(locale)
        return self.hits[:max_results]

    async def images(self, query, *, max_results):
        return ["https://img.example.com/a.png"][:max_results]


class FakeAnswerer:
    async def generate(self, query, results, depth):
        return f"answer to {query} from {len(results)} results"


@pytest.fixture
def hits() -> list[Hit]:
    return [
        Hit("Usage", "https://docs.example.com/usage", "Call run() to process data."),
        Hit("Install", "https://docs.example.com/install", "How to pip install example."),
        Hit("Other", "https://other.org/x", "Something unrelated."),
    ]


@pytest.fixture
def make_client(hits):
    def _make(api_keys: list[str] | None = None, answerer=None) -> TestClient:
        settings = Settings(api_keys=api_keys or [], allow_private_networks=True, _env_file=None)
        fetcher = Fetcher(httpx.AsyncClient(transport=mock_transport()), settings)
        service = SearchService(FakeProvider(hits), fetcher, answerer)
        return TestClient(create_app(settings, service))

    return _make
