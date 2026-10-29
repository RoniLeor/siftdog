"""/search pipeline: provider hits -> domain filtering -> (optional) page fetch + extraction
-> BM25 chunk selection and scoring -> (optional) images and LLM answer."""

import asyncio
import logging
from urllib.parse import urlsplit

from .answer import AnswerGenerator
from .extract import Document, extract
from .fetch import Fetcher, FetchError
from .models import SearchRequest, SearchResult
from .providers import Hit, SearchProvider
from .rank import chunk, combine_scores, score_groups, select_chunks

log = logging.getLogger(__name__)


def domain_matches(url: str, domains: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in (d.lower().strip() for d in domains))


def provider_query(req: SearchRequest) -> str:
    if not req.include_domains:
        return req.query
    sites = " OR ".join(f"site:{d}" for d in req.include_domains)
    return f"{req.query} ({sites})" if len(req.include_domains) > 1 else f"{req.query} {sites}"


def resolve_time_range(req: SearchRequest) -> str | None:
    if req.time_range or req.days is None:
        return req.time_range
    if req.days <= 1:
        return "day"
    if req.days <= 7:
        return "week"
    if req.days <= 31:
        return "month"
    return "year"


def resolve_locale(req: SearchRequest) -> str | None:
    """SearXNG locale tag: "he-IL" from language + country, "he" from language alone."""
    if not req.language:
        return None
    return f"{req.language}-{req.country.upper()}" if req.country else req.language


class SearchService:
    def __init__(
        self, provider: SearchProvider, fetcher: Fetcher, answerer: AnswerGenerator | None
    ):
        self.provider = provider
        self.fetcher = fetcher
        self.answerer = answerer

    async def search(self, req: SearchRequest) -> tuple[list[SearchResult], list[str]]:
        images_task = (
            asyncio.create_task(self.provider.images(req.query, max_results=req.max_results or 5))
            if req.include_images
            else None
        )
        hits = await self.provider.search(
            provider_query(req),
            topic=req.topic,
            time_range=resolve_time_range(req),
            # Over-fetch so domain filtering and dead pages don't leave us short.
            max_results=max(req.max_results * 2, 10),
            locale=resolve_locale(req),
        )
        if req.include_domains:
            hits = [h for h in hits if domain_matches(h.url, req.include_domains)]
        if req.exclude_domains:
            hits = [h for h in hits if not domain_matches(h.url, req.exclude_domains)]
        hits = hits[: req.max_results]

        results = await self._build_results(req, hits)
        images: list[str] = []
        if images_task:
            try:
                images = await images_task
            except Exception:  # images are best-effort; never fail the search over them
                log.exception("image search failed")
        return results, images

    async def _build_results(self, req: SearchRequest, hits: list[Hit]) -> list[SearchResult]:
        advanced = req.search_depth == "advanced"
        raw_fmt = "text" if req.include_raw_content == "text" else "markdown"
        docs: list[Document | None] = [None] * len(hits)
        if advanced or req.include_raw_content:
            docs = await asyncio.gather(*(self._fetch_doc(h.url, raw_fmt, advanced) for h in hits))

        # Fetched pages are scored by their chunks, the rest by title + snippet, all in one
        # BM25 corpus so every hit shares the same statistics.
        pages = [doc.content if advanced and doc and doc.content else None for doc in docs]
        groups = [
            chunk(text) if text else [f"{hit.title}\n{hit.snippet}"]
            for hit, text in zip(hits, pages, strict=True)
        ]
        group_scores = score_groups(req.query, groups)
        relevance = [max(group, default=0.0) for group in group_scores]
        contents = [
            " [...] ".join(select_chunks(group, scores, req.chunks_per_source))
            if text
            else hit.snippet
            for hit, text, group, scores in zip(hits, pages, groups, group_scores, strict=True)
        ]

        scores = combine_scores(relevance)
        results = [
            SearchResult(
                title=hit.title or (doc.title if doc else ""),
                url=hit.url,
                content=content,
                score=score,
                raw_content=(doc.content if doc else None) if req.include_raw_content else None,
                published_date=hit.published_date or (doc.published_date if doc else None),
            )
            for hit, doc, content, score in zip(hits, docs, contents, scores, strict=True)
        ]
        return sorted(results, key=lambda r: r.score, reverse=True)

    async def _fetch_doc(self, url: str, fmt: str, advanced: bool) -> Document | None:
        try:
            page = await self.fetcher.fetch(url)
        except FetchError as e:
            log.info("fetch failed for %s: %s", url, e)
            return None
        return await asyncio.to_thread(extract, page, fmt=fmt, advanced=advanced)
