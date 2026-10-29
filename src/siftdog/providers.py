"""Web search backends. A provider only returns raw hits (title/url/snippet); ranking,
content extraction and answers happen in search.py so every backend gets them for free."""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class ProviderError(Exception):
    pass


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    published_date: str | None = None


class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        topic: str,
        time_range: str | None,
        max_results: int,
        locale: str | None = None,
    ) -> list[Hit]: ...

    async def images(self, query: str, *, max_results: int) -> list[str]: ...


_TIME_RANGES = {"d": "day", "w": "week", "m": "month", "y": "year"}


class SearxngProvider:
    """Queries a SearXNG instance's JSON API (requires `json` in search.formats)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        *,
        topic: str,
        time_range: str | None,
        max_results: int,
        locale: str | None = None,
    ) -> list[Hit]:
        params = {"q": query, "categories": "news" if topic == "news" else "general"}
        if time_range:
            params["time_range"] = _TIME_RANGES.get(time_range, time_range)
        if locale:  # SearXNG locale tag such as "he" or "he-IL"; engines map it to their own
            params["language"] = locale
        hits: list[Hit] = []
        seen: set[str] = set()
        failed: dict[str, None] = {}  # engines SearXNG reported as failing (ordered set)
        # SearXNG pages hold ~10 results; fetch more pages until we have enough.
        for page in range(1, 4):
            results, page_failed = await self._query({**params, "pageno": page})
            failed.update(dict.fromkeys(page_failed))
            if not results:
                break
            for r in results:
                url = r.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                hits.append(
                    Hit(
                        title=r.get("title") or "",
                        url=url,
                        snippet=r.get("content") or "",
                        published_date=r.get("publishedDate"),
                    )
                )
            if len(hits) >= max_results:
                break
        if failed:
            engines = ", ".join(failed)
            if not hits:
                # Engines rate-limit or block SearXNG's IP; without this an agent would get an
                # empty 200 and conclude nothing exists.
                raise ProviderError(f"SearXNG returned no results; failing engines: {engines}")
            log.warning("SearXNG engines failing, results may be incomplete: %s", engines)
        return hits

    async def images(self, query: str, *, max_results: int) -> list[str]:
        results, failed = await self._query({"q": query, "categories": "images"})
        if failed:
            log.warning("SearXNG image engines failing: %s", ", ".join(failed))
        urls = [r["img_src"] for r in results if r.get("img_src")]
        return list(dict.fromkeys(urls))[:max_results]

    async def _query(self, params: dict) -> tuple[list[dict], list[str]]:
        """One SearXNG request: (results, failing engines as "name (reason)")."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/search", params={**params, "format": "json"}, timeout=20
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"SearXNG request failed: {e}") from e
        failed = [
            f"{entry[0]} ({entry[1]})" if len(entry) > 1 else str(entry[0])
            for entry in data.get("unresponsive_engines") or []
            if isinstance(entry, list | tuple) and entry
        ]
        return data.get("results", []), failed
