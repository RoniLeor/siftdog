"""Turn fetched HTML into clean markdown/text, plus the images and links on the page."""

from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin

import lxml.html
import trafilatura

from .fetch import Page


@dataclass
class Document:
    url: str
    title: str
    content: str
    published_date: str | None = None
    images: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def extract(page: Page, *, fmt: str = "markdown", advanced: bool = False) -> Document:
    if page.content_type == "text/plain":
        return Document(url=page.url, title="", content=page.html.strip())

    content = trafilatura.extract(
        page.html,
        url=page.url,
        output_format="markdown" if fmt == "markdown" else "txt",
        include_tables=True,
        include_links=False,
        include_comments=False,
        # Advanced depth trades precision for recall: keeps more borderline blocks.
        favor_recall=advanced,
        favor_precision=not advanced,
    )
    meta = trafilatura.extract_metadata(page.html, default_url=page.url)
    images, links = _images_and_links(page)
    return Document(
        url=page.url,
        title=(meta.title if meta and meta.title else ""),
        content=(content or "").strip(),
        published_date=(meta.date if meta else None),
        images=images,
        links=links,
    )


def _images_and_links(page: Page) -> tuple[list[str], list[str]]:
    try:
        tree = lxml.html.fromstring(page.html)
    except (ValueError, lxml.etree.ParserError):
        return [], []
    base = page.url
    base_tag = tree.find(".//base[@href]")
    if base_tag is not None:
        base = _join(page.url, base_tag.get("href")) or page.url

    images = _unique(
        joined
        for src in tree.xpath("//img/@src")
        if src and not src.startswith("data:") and (joined := _join(base, src))
    )
    links = _unique(
        urldefrag(joined)[0]
        for href in tree.xpath("//a/@href")
        if href
        and not href.startswith(("mailto:", "javascript:", "tel:", "#"))
        and (joined := _join(base, href))
    )
    return images, [u for u in links if u.startswith(("http://", "https://"))]


def _join(base: str, ref: str) -> str | None:
    """urljoin that returns None instead of raising on a malformed reference."""
    try:
        return urljoin(base, ref)
    except ValueError:
        return None


def _unique(items) -> list[str]:
    return list(dict.fromkeys(items))
