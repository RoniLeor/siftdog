"""HTTP fetching of arbitrary user-supplied URLs.

/extract and /crawl let callers make this server fetch anything, so non-public addresses
(private, loopback, link-local, ...) are refused at two points:

1. `Fetcher._check_url` rejects non-http(s) URLs and hosts that resolve to non-public
   addresses before each request and redirect hop, for a clear early error.
2. `public_only_transport()` enforces the rule at connect time: it resolves the host itself,
   refuses if any address is non-public, and connects to the address it checked. A DNS answer
   that changes between check and connect (DNS rebinding) therefore can't reach an internal
   address. TLS still verifies the certificate against the URL's hostname.
"""

import asyncio
import codecs
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import anyio
import httpcore
import httpx

from .config import Settings

MAX_REDIRECTS = 5
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")
# <meta charset="..."> or <meta http-equiv="Content-Type" content="text/html; charset=...">
_META_CHARSET = re.compile(rb"<meta[^>]*charset=[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)


class FetchError(Exception):
    pass


@dataclass
class Page:
    url: str  # final URL after redirects
    html: str
    content_type: str


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global


async def _resolve(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Network backend that only opens TCP connections to public addresses, connecting to
    the exact address it validated rather than letting the socket layer resolve again."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None, resolve=_resolve):
        self._inner = inner or httpcore.AnyIOBackend()
        self._resolve = resolve

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ) -> httpcore.AsyncNetworkStream:
        try:
            with anyio.fail_after(timeout):
                ips = await self._resolve(host, port)
        except TimeoutError as e:
            raise httpcore.ConnectTimeout(f"DNS resolution timed out for {host}") from e
        except OSError as e:
            raise httpcore.ConnectError(f"DNS resolution failed for {host}: {e}") from e
        if not ips or not all(_is_public(ip) for ip in ips):
            raise httpcore.ConnectError(f"{host} resolves to a non-public address")
        error: Exception | None = None
        for ip in ips:
            try:
                return await self._inner.connect_tcp(
                    ip, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )  # fmt: skip
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                error = e
        raise error

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


_CHARSET_ALIASES = {"iso-8859-8-i": "iso-8859-8"}  # legacy Hebrew pages; same bytes, no codec


def _codec(name: str | None) -> str | None:
    """Canonical codec name for `name` (aliases applied); None unless it decodes text."""
    if not name:
        return None
    try:
        canonical = codecs.lookup(_CHARSET_ALIASES.get(name.lower(), name)).name
        # Known names that cannot decode text: "undefined", "idna", "rot13", "base64", ...
        b"\xff".decode(canonical, errors="replace")
    except (LookupError, ValueError):  # ValueError also covers UnicodeError and a NUL
        return None
    return canonical


def _meta_charset(body: bytes) -> str | None:
    match = _META_CHARSET.search(body[:4096])
    return match.group(1).decode("ascii") if match else None


def public_only_transport(backend: httpcore.AsyncNetworkBackend | None = None):
    """httpx transport for fetching user-supplied URLs. Passing an explicit transport also
    stops httpx from routing through HTTP(S)_PROXY env vars, which would bypass the check."""
    transport = httpx.AsyncHTTPTransport()
    # httpx has no public hook for the network backend, so swap in an equivalent pool
    # (same limits as httpx's defaults).
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=5.0,
        network_backend=backend or PublicOnlyBackend(),
    )
    return transport


class Fetcher:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self._client = client
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.fetch_concurrency)

    async def fetch(self, url: str) -> Page:
        total = self._settings.fetch_total_timeout
        async with self._semaphore:
            try:
                async with asyncio.timeout(total):  # httpx's timeout is per phase, not overall
                    return await self._fetch(url)
            except TimeoutError as e:
                raise FetchError(f"fetch exceeded {total} s") from e

    async def _fetch(self, url: str) -> Page:
        for _ in range(MAX_REDIRECTS + 1):
            await self._check_url(url)
            try:
                async with self._client.stream(
                    "GET",
                    url,
                    follow_redirects=False,
                    timeout=self._settings.fetch_timeout,
                    headers={"User-Agent": self._settings.user_agent},
                ) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError(f"redirect without location ({resp.status_code})")
                        try:
                            url = urljoin(url, location)
                        except ValueError as e:
                            raise FetchError(f"invalid redirect location: {e}") from e
                        continue
                    if resp.status_code >= 400:
                        raise FetchError(f"HTTP {resp.status_code}")
                    content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                    if content_type and not content_type.startswith(TEXT_TYPES):
                        raise FetchError(f"unsupported content type: {content_type}")
                    body = await self._read_limited(resp)
                    return Page(url=str(resp.url), html=body, content_type=content_type)
            except (httpx.HTTPError, httpx.InvalidURL) as e:
                raise FetchError(f"{type(e).__name__}: {e}") from e
        raise FetchError("too many redirects")

    async def _read_limited(self, resp: httpx.Response) -> str:
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > self._settings.fetch_max_bytes:
                raise FetchError("response too large")
            chunks.append(chunk)
        body = b"".join(chunks)
        # Header charset first; legacy pages (e.g. windows-1255) often declare it only in <meta>.
        encoding = _codec(resp.charset_encoding) or _codec(_meta_charset(body)) or "utf-8"
        return body.decode(encoding, errors="replace")

    async def _check_url(self, url: str) -> None:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as e:
            raise FetchError(f"invalid URL: {e}") from e
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError("only absolute http(s) URLs are supported")
        if self._settings.allow_private_networks:
            return
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                parts.hostname, port, type=socket.SOCK_STREAM
            )
        except socket.gaierror as e:
            raise FetchError(f"DNS resolution failed: {e}") from e
        except UnicodeError as e:  # IDNA encoding: empty label, label over 63 chars, bad bidi
            raise FetchError(f"invalid host name: {e}") from e
        if not all(_is_public(info[4][0]) for info in infos):
            raise FetchError("URL resolves to a non-public address")
