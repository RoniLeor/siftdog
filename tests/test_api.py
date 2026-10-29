import pytest
from conftest import FakeAnswerer


def test_search_basic(make_client):
    with make_client() as client:
        body = client.post("/search", json={"query": "install example", "max_results": 3}).json()
    assert [r["url"] for r in body["results"]][0] == "https://docs.example.com/install"
    assert body["answer"] is None and body["images"] == []
    assert all(r["raw_content"] is None for r in body["results"])


def test_search_advanced_uses_page_chunks(make_client):
    with make_client() as client:
        body = client.post(
            "/search",
            json={"query": "pip install", "search_depth": "advanced", "include_raw_content": True},
        ).json()
    install = next(r for r in body["results"] if r["url"].endswith("/install"))
    assert "Python 3.11" in install["content"]
    assert "Installation" in install["raw_content"]
    other = next(r for r in body["results"] if r["url"] == "https://other.org/x")
    assert other["content"] == "Something unrelated."  # fetch failed -> snippet fallback


def test_search_domain_filters(make_client):
    with make_client() as client:
        body = client.post(
            "/search",
            json={
                "query": "x",
                "include_domains": ["example.com"],
                "exclude_domains": ["other.org"],
            },
        ).json()
    assert {r["url"] for r in body["results"]} == {
        "https://docs.example.com/usage",
        "https://docs.example.com/install",
    }


def test_search_answer_and_images(make_client):
    with make_client(answerer=FakeAnswerer()) as client:
        body = client.post(
            "/search", json={"query": "q", "include_answer": True, "include_images": True}
        ).json()
    assert body["answer"] == "answer to q from 3 results"
    assert body["images"] == ["https://img.example.com/a.png"]


def test_answer_disabled_is_400(make_client):
    with make_client() as client:
        resp = client.post("/search", json={"query": "q", "include_answer": True})
    assert resp.status_code == 400


def test_auth(make_client):
    with make_client(api_keys=["tvly-good"]) as client:
        assert client.post("/search", json={"query": "q"}).status_code == 401
        ok = client.post(
            "/search", json={"query": "q"}, headers={"Authorization": "Bearer tvly-good"}
        )
        legacy = client.post("/search", json={"query": "q", "api_key": "tvly-good"})
    assert ok.status_code == 200 and legacy.status_code == 200


def test_extract(make_client):
    with make_client() as client:
        body = client.post(
            "/extract",
            json={"urls": ["https://docs.example.com/usage", "https://docs.example.com/missing"]},
        ).json()
    assert [r["url"] for r in body["results"]] == ["https://docs.example.com/usage"]
    assert "run()" in body["results"][0]["raw_content"]
    assert body["failed_results"] == [
        {"url": "https://docs.example.com/missing", "error": "HTTP 404"}
    ]


def test_map_lists_links_without_leaving_domain(make_client):
    with make_client() as client:
        body = client.post("/map", json={"url": "docs.example.com", "max_depth": 1}).json()
    assert body["base_url"] == "https://docs.example.com/"
    assert "https://docs.example.com/install" in body["results"]
    assert "https://docs.example.com/usage" in body["results"]
    assert not any("other.org" in u for u in body["results"])


def test_crawl_fetches_content_and_respects_exclude(make_client):
    with make_client() as client:
        body = client.post(
            "/crawl",
            json={"url": "https://docs.example.com/", "max_depth": 1, "exclude_paths": ["^/usage"]},
        ).json()
    urls = [r["url"] for r in body["results"]]
    assert urls == ["https://docs.example.com/", "https://docs.example.com/install"]
    assert "pip install" in body["results"][1]["raw_content"]


def test_crawl_bad_regex_is_400(make_client):
    with make_client() as client:
        resp = client.post(
            "/crawl", json={"url": "https://docs.example.com/", "select_paths": ["("]}
        )
    assert resp.status_code == 400


def test_search_locale_reaches_the_provider(make_client):
    with make_client() as client:
        provider = client.app.state.service.provider
        client.post("/search", json={"query": "q", "language": "he", "country": "il"})
        client.post("/search", json={"query": "q", "language": "he"})
        client.post("/search", json={"query": "q"})
    assert provider.locales == ["he-IL", "he", None]


@pytest.mark.parametrize("body", [{"language": "HE"}, {"country": "isr"}, {"country": "IL"}])
def test_search_rejects_bad_locale(make_client, body):
    with make_client() as client:
        assert client.post("/search", json={"query": "q", **body}).status_code == 422


def test_extract_reports_a_malformed_url_as_failed(make_client):
    with make_client() as client:
        body = client.post("/extract", json={"urls": ["http://[bad"]}).json()
    assert body["results"] == []
    assert "invalid URL" in body["failed_results"][0]["error"]


@pytest.mark.parametrize("path", ["/map", "/crawl"])
@pytest.mark.parametrize("url", ["http://[bad", "[bad"])  # with and without a scheme
def test_map_and_crawl_reject_a_malformed_start_url(make_client, path, url):
    with make_client() as client:
        assert client.post(path, json={"url": url}).status_code == 422
