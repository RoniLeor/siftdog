# Siftdog — open-source, self-hosted Tavily alternative

[![CI](https://github.com/khsarvar/siftdog/actions/workflows/ci.yml/badge.svg)](https://github.com/khsarvar/siftdog/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/siftdog.svg)](https://pypi.org/project/siftdog/)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

**[Website](https://siftdog.com/) · [Benchmark](https://siftdog.com/benchmark/) · [Blog](https://siftdog.com/blog/) · [Hosted waitlist](https://siftdog.com/hosted/)**

**Siftdog is an open-source, self-hosted web search API for AI agents and LLM apps, and a drop-in
replacement for the [Tavily](https://tavily.com) API.** It serves the same `/search`,
`/extract`, `/crawl` and `/map` endpoints with the same request and response shapes, so code
written for Tavily (including the official Python SDK and the LangChain integration) works
against your own server by changing only the base URL. It needs no search API key.

<!-- mcp-name: io.github.khsarvar/siftdog -->

- **Search** through a [SearXNG](https://github.com/searxng/searxng) metasearch instance, with no search API keys
- **Extraction** of clean markdown or text from web pages with [trafilatura](https://github.com/adbar/trafilatura)
- **Ranking**: BM25 relevance blended with the upstream engine's order; `advanced` depth fetches each page and returns its most relevant chunks
- **Answers** (`include_answer`) written by Claude from the retrieved results
- **Crawling and site maps** with depth, breadth, limit and regex path/domain filters
- **MCP server** for Claude Code, Claude Desktop, Cursor and other MCP clients, over HTTP at `/mcp` or stdio with `siftdog mcp`
- **SSRF protection**: private and internal addresses are blocked, including via redirects and DNS rebinding
- **MIT licensed**

## Use it as a drop-in Tavily replacement

Point the official Tavily clients at your Siftdog server with `api_base_url`. The key can be any
string when auth is disabled, or one of your `API_KEYS`.

```python
from tavily import TavilyClient

client = TavilyClient(api_key="your-siftdog-key", api_base_url="http://localhost:8000")
results = client.search("latest python release", search_depth="advanced", max_results=5)
pages = client.extract(urls=["https://en.wikipedia.org/wiki/Okapi_BM25"])
```

LangChain, through [`langchain-tavily`](https://github.com/tavily-ai/langchain-tavily):

```python
from langchain_tavily import TavilySearch

search = TavilySearch(
    max_results=5, tavily_api_key="your-siftdog-key", api_base_url="http://localhost:8000"
)
search.invoke({"query": "what is BM25 ranking"})
```

Tested with `tavily-python` 0.8.4 (search, extract, crawl, map; sync and async) and
`langchain-tavily` 0.2.18 (search, extract).

## Quick start

### Docker Compose (includes SearXNG)

The full stack, with a SearXNG instance for `/search`, using the published image:

```bash
git clone https://github.com/khsarvar/siftdog && cd siftdog
cp .env.example .env          # optional: set API_KEYS and ANTHROPIC_API_KEY
docker compose up
```

Try a search:

```bash
curl -s localhost:8000/search -H "Content-Type: application/json" \
  -d '{"query": "latest python release", "search_depth": "advanced"}'
```

If you set `API_KEYS`, add `-H "Authorization: Bearer <key>"`. OpenAPI docs are served at
http://localhost:8000/docs.

### Docker image only

```bash
docker run -p 8000:8000 ghcr.io/khsarvar/siftdog
```

`/extract`, `/crawl` and `/map` work on their own. For `/search`, point it at a SearXNG
instance with the JSON format enabled: `-e SEARXNG_URL=http://your-searxng:8080`. Images are
published for `linux/amd64` and `linux/arm64`, tagged `latest` and by version (`0.1.0`, `0.1`).

### pip

```bash
pip install siftdog
SEARXNG_URL=http://your-searxng:8080 siftdog --port 8000
```

Configuration is read from environment variables or a `.env` file (see
[Configuration](#configuration)).

## Use with MCP clients (Claude, Cursor, ...)

Siftdog is also an [MCP](https://modelcontextprotocol.io) server with four read-only tools:
`siftdog_search`, `siftdog_extract`, `siftdog_crawl` and `siftdog_map`.

**Connect to a running Siftdog server** (Streamable HTTP at `/mcp`). With Claude Code:

```bash
claude mcp add --transport http siftdog http://localhost:8000/mcp \
  --header "Authorization: Bearer <key>"      # omit the header if API_KEYS is empty
```

**Or run it locally over stdio** with [uv](https://docs.astral.sh/uv/), no server needed.
`SEARXNG_URL` is only needed for `siftdog_search`:

```bash
claude mcp add siftdog -e SEARXNG_URL=http://your-searxng:8080 -- uvx siftdog mcp
```

Claude Desktop (`claude_desktop_config.json`), Cursor (`.cursor/mcp.json`) and most other clients
take the same command as JSON:

```json
{
  "mcpServers": {
    "siftdog": {
      "command": "uvx",
      "args": ["siftdog", "mcp"],
      "env": { "SEARXNG_URL": "http://your-searxng:8080" }
    }
  }
}
```

API keys work as for the REST API: send `Authorization: Bearer <key>`, or append
`?api_key=<key>` to the URL for clients that can't set headers (URLs can end up in logs, so
prefer the header). The HTTP endpoint only answers requests addressed to `localhost` unless you
list your hostname in `MCP_ALLOWED_HOSTS`.

## Siftdog vs Tavily, Firecrawl and crw

| | Siftdog | [Tavily](https://tavily.com) | [Firecrawl](https://github.com/firecrawl/firecrawl) | [crw](https://github.com/fastcrw/crw) |
|---|---|---|---|---|
| License | MIT | Proprietary (hosted service) | AGPL-3.0 | AGPL-3.0 |
| Self-hosted | Yes | No | Yes | Yes (also a managed API) |
| Tavily-compatible API | Yes | — | No (own API) | No (own API) |
| Search source | SearXNG metasearch | Proprietary | — | SearXNG (bundled) |
| JavaScript rendering | No (static HTML) | — | Yes | Yes (Lightpanda, Chrome fallback) |
| MCP server | Yes (HTTP and stdio) | Yes | Yes | Yes |
| Language | Python | — | TypeScript | Rust |

Measured results: [Siftdog vs Tavily search benchmark](https://siftdog.com/benchmark/)
(reproducible with [`bench/`](bench/)).

**When to pick something else:** if you'd rather not run infrastructure, or you want Tavily's
neural reranking, use hosted Tavily. If you need JavaScript-rendered pages or a scraping
platform with more features, look at Firecrawl or crw. Siftdog is for teams that want the
Tavily API on their own servers under a permissive license.

## FAQ

### What is Siftdog?
Siftdog is an open-source web search and extraction API for AI agents. It reproduces the Tavily
API (`/search`, `/extract`, `/crawl`, `/map`) on infrastructure you run yourself, using SearXNG
for search results, trafilatura for content extraction and BM25 for relevance ranking.

### Is Siftdog a drop-in replacement for Tavily?
For the four core endpoints, yes. The request and response fields match Tavily's, and the
official `tavily-python` SDK and `langchain-tavily` work by setting `api_base_url`. The
differences: relevance scores come from BM25 rather than a neural reranker, `instructions`
(crawl/map) and `include_image_descriptions` (search) are accepted but ignored, and Tavily's
`/research` endpoint isn't implemented.

### Do I need a search API key?
No. Search results come from SearXNG, which queries public search engines. The only optional
key is `ANTHROPIC_API_KEY`, used when a request sets `include_answer`.

### Does it work with LangChain?
Yes, through the official `langchain-tavily` package. Pass `api_base_url` pointing at your
Siftdog server, as in the example above.

### Does Siftdog have an MCP server?
Yes. The API server exposes MCP over Streamable HTTP at `/mcp`, and `uvx siftdog mcp` runs it
over stdio for local clients such as Claude Desktop and Cursor. See
[Use with MCP clients](#use-with-mcp-clients-claude-cursor-).

### Why does `/search` return 502 "failing engines"?
SearXNG gets its results by querying public search engines (Brave, DuckDuckGo, Google and
others), and those engines rate-limit or CAPTCHA an IP that sends many searches. When every
engine is failing, Siftdog returns `502` with the engines and reasons, for example
`failing engines: brave (Suspended: too many requests), duckduckgo (CAPTCHA)`, rather than an
empty result list your agent would mistake for "nothing found". Engines recover on their own,
from minutes to about a day. If some engines still work, you get their results and the server
logs which engines were down. To reduce blocking, enable more engines in
`docker/searxng/settings.yml` and avoid bursts of identical searches.

### Is it safe to expose Siftdog on a public server?
Set `API_KEYS` so only your clients can call it. `/extract` and `/crawl` fetch caller-supplied
URLs, so Siftdog refuses private, loopback and link-local addresses, checked on every redirect and
at connect time against the exact address used, which also stops DNS rebinding.

### Can I run it without Docker?
Yes: `pip install siftdog`, then run `siftdog` with `SEARXNG_URL` pointing at any SearXNG
instance with the JSON output format enabled. `/extract`, `/crawl` and `/map` work without
SearXNG.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker compose up searxng -d    # uncomment its `ports:` in docker-compose.yml first
.venv/bin/siftdog             # http://127.0.0.1:8000, docs at /docs
.venv/bin/pytest                # offline test suite
.venv/bin/ruff check . && .venv/bin/ruff format .
```

## API

All endpoints take JSON `POST` bodies and a `Authorization: Bearer <key>` header (a legacy
`api_key` body field is also accepted). If `API_KEYS` is empty, auth is disabled.

| Endpoint   | Purpose | Key parameters |
|------------|---------|----------------|
| `/search`  | Web search | `query`, `search_depth` (`basic`/`advanced`), `topic` (`general`/`news`), `time_range`, `language`, `country`, `max_results`, `chunks_per_source`, `include_answer`, `include_raw_content`, `include_images`, `include_domains`, `exclude_domains` |
| `/extract` | Clean content from up to 20 URLs | `urls`, `extract_depth`, `format` (`markdown`/`text`), `include_images` |
| `/crawl`   | Crawl a site and return page content | `url`, `max_depth`, `max_breadth`, `limit`, `select_paths`, `exclude_paths`, `select_domains`, `exclude_domains`, `allow_external`, `format` |
| `/map`     | List a site's URLs without content | same traversal parameters as `/crawl` |

Differences from hosted Tavily: `instructions` (crawl/map) and `include_image_descriptions` are
accepted but ignored; relevance scores come from BM25, not a neural reranker; `language` is an
ISO 639-1 code (`he`) and `country` an ISO 3166-1 alpha-2 code (`IL`) rather than a country
name, and `country` needs `language` (together they become the SearXNG locale `he-IL`).

## Configuration

Environment variables (see `.env.example`): `API_KEYS`, `SEARXNG_URL`, `ANSWER_ENABLED`,
`ANSWER_MODEL`, `ANSWER_EFFORT`, `FETCH_TIMEOUT`, `FETCH_TOTAL_TIMEOUT`, `FETCH_CONCURRENCY`,
`FETCH_MAX_BYTES`, `ALLOW_PRIVATE_NETWORKS`, `CRAWL_MAX_LIMIT`, `MCP_ALLOWED_HOSTS`.

`MCP_ALLOWED_HOSTS` lists the hostnames the `/mcp` endpoint answers besides `localhost`, for
example `search.example.com,search.example.com:*`. Requests addressed to any other host get
`421`, which protects a local server from DNS-rebinding attacks by web pages.

**Security:** `/extract` and `/crawl` make the server fetch caller-supplied URLs. Requests to
private, loopback and link-local addresses are blocked (including via redirects) unless
`ALLOW_PRIVATE_NETWORKS=true`. The check runs at connect time against the exact address being
connected to, so DNS rebinding can't get around it. Fetches of user URLs ignore
`HTTP(S)_PROXY`, since a proxy would hide the destination address. As defense in depth for
hostile multi-tenant deployments, also restrict egress at the network level.

## License

Siftdog is released under the [MIT License](LICENSE). The "Siftdog" name is covered
separately by the [trademark policy](TRADEMARKS.md): use the code freely, but forks and hosted
services need a different name.
