import asyncio
import time

import httpcore
import httpx
import pytest

from siftdog.config import Settings
from siftdog.extract import extract
from siftdog.fetch import Fetcher, FetchError, Page, PublicOnlyBackend, public_only_transport


def fetcher(handler) -> Fetcher:
    settings = Settings(_env_file=None)
    return Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)), settings)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://10.0.0.5/admin", "http://[::1]/", "file:///etc/passwd"]
)
async def test_blocks_private_and_non_http(url):
    with pytest.raises(FetchError):
        await fetcher(lambda r: httpx.Response(200))._fetch(url)


async def test_blocks_redirect_to_private_address():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    with pytest.raises(FetchError, match="non-public"):
        await fetcher(handler).fetch("http://93.184.216.34/")


async def test_rejects_binary_content():
    def handler(request):
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with pytest.raises(FetchError, match="content type"):
        await fetcher(handler).fetch("http://93.184.216.34/doc.pdf")


def test_extract_resolves_links_and_images():
    html = """<html><head><title>T</title><base href="https://site.com/dir/"></head><body>
      <article><p>Enough body text here to be kept by the extractor as main content of
      this page, repeated so it clears the minimum length threshold comfortably.</p>
      <a href="page#frag">x</a><a href="javascript:void(0)">y</a><img src="img.png"></article>
      </body></html>"""
    doc = extract(Page(url="https://site.com/dir/index", html=html, content_type="text/html"))
    assert "main content" in doc.content
    assert doc.links == ["https://site.com/dir/page"]
    assert doc.images == ["https://site.com/dir/img.png"]


class RecordingBackend(httpcore.AsyncMockBackend):
    """Mock network: records the address connected to and the TLS server_hostname."""

    def __init__(self):
        super().__init__([b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n", b"\r\n"])
        self.connected: list[str] = []
        self.tls_hostnames: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        stream = await super().connect_tcp(host, port)
        tls_hostnames = self.tls_hostnames

        async def start_tls(ssl_context, server_hostname=None, timeout=None):
            tls_hostnames.append(server_hostname)
            return stream

        stream.start_tls = start_tls
        return stream


def guarded(answers: list[list[str]]) -> tuple[httpx.AsyncClient, RecordingBackend]:
    """Client over public_only_transport whose resolver returns `answers` in turn."""
    inner = RecordingBackend()

    async def resolve(host, port):
        return answers.pop(0)

    transport = public_only_transport(PublicOnlyBackend(inner, resolve=resolve))
    return httpx.AsyncClient(transport=transport), inner


async def test_connects_to_the_checked_address_with_original_hostname_for_tls():
    client, inner = guarded([["93.184.216.34"]])
    async with client:
        resp = await client.get("https://example.com/")
    assert resp.status_code == 200
    assert inner.connected == ["93.184.216.34"]
    assert inner.tls_hostnames == ["example.com"]


@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.1"], ["93.184.216.34", "127.0.0.1"], ["::ffff:169.254.169.254"], []],
)
async def test_refuses_non_public_addresses_at_connect_time(addresses):
    client, inner = guarded([addresses])
    async with client:
        with pytest.raises(httpx.ConnectError):
            await client.get("http://rebind.test/")
    assert inner.connected == []


async def test_dns_rebinding_after_precheck_is_blocked():
    # Fetcher's pre-check sees a public IP literal; the connect-time lookup (simulating a
    # rebinding DNS answer) returns a private one, and the connection must be refused.
    client, inner = guarded([["10.0.0.1"]])
    fetcher = Fetcher(client, Settings(_env_file=None))
    with pytest.raises(FetchError, match="non-public"):
        await fetcher.fetch("http://93.184.216.34/")
    assert inner.connected == []


@pytest.mark.parametrize("url", ["http://[bad", "http://h:abc/", "http://h:99999/"])
async def test_malformed_url_is_a_fetch_error(url):
    with pytest.raises(FetchError, match="invalid URL"):
        await fetcher(lambda r: httpx.Response(200)).fetch(url)


async def test_url_rejected_by_httpx_is_a_fetch_error():
    with pytest.raises(FetchError, match="InvalidURL"):
        await fetcher(lambda r: httpx.Response(200)).fetch("http://93.184.216.34/\x00x")


async def test_redirect_to_malformed_location_is_a_fetch_error():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://[bad"})

    with pytest.raises(FetchError, match="invalid redirect"):
        await fetcher(handler).fetch("http://93.184.216.34/")


def test_extract_skips_malformed_links_and_images():
    html = """<html><body><article><p>Enough body text here to be kept by the extractor as
      the main content of this page, repeated so it clears the minimum length threshold.</p>
      <a href="http://[bad">x</a><a href="/ok">y</a><img src="http://[bad"><img src="/i.png">
      </article></body></html>"""
    doc = extract(Page(url="https://site.com/", html=html, content_type="text/html"))
    assert doc.links == ["https://site.com/ok"]
    assert doc.images == ["https://site.com/i.png"]


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        for _ in range(8):
            await asyncio.sleep(0.1)
            yield b"x"


async def test_total_timeout_bounds_a_slow_body():
    settings = Settings(_env_file=None, fetch_timeout=5, fetch_total_timeout=0.3)

    def handler(request):
        return httpx.Response(200, stream=SlowStream(), headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    start = time.perf_counter()
    with pytest.raises(FetchError, match="exceeded"):
        await Fetcher(client, settings).fetch("http://93.184.216.34/")
    assert time.perf_counter() - start < 1


HEBREW = "שלום עולם"


def cp1255_page(head: str, body: str = HEBREW) -> bytes:
    return f"<html><head>{head}</head><body><p>{body}</p></body></html>".encode("cp1255")


@pytest.mark.parametrize(
    "meta",
    [
        '<meta charset="windows-1255">',
        '<meta http-equiv="Content-Type" content="text/html; charset=windows-1255">',
    ],
)
async def test_meta_charset_is_used_when_the_header_has_none(meta):
    def handler(request):
        return httpx.Response(200, content=cp1255_page(meta), headers={"content-type": "text/html"})

    page = await fetcher(handler).fetch("http://93.184.216.34/")
    assert HEBREW in page.html


async def test_header_charset_wins_over_meta():
    def handler(request):
        headers = {"content-type": "text/html; charset=windows-1255"}
        return httpx.Response(200, content=cp1255_page('<meta charset="utf-8">'), headers=headers)

    page = await fetcher(handler).fetch("http://93.184.216.34/")
    assert HEBREW in page.html


async def test_unknown_charsets_fall_back_to_utf8():
    def handler(request):
        body = f'<html><head><meta charset="x-unknown"></head><body><p>{HEBREW}</p></body></html>'
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/html; charset=utf8mb4"}
        )

    page = await fetcher(handler).fetch("http://93.184.216.34/")
    assert HEBREW in page.html


async def test_nul_in_header_charset_is_ignored():
    def handler(request):  # RFC 2231 encoding lets a NUL into the charset name
        headers = {"content-type": "text/html; charset*=utf-8''ab%00c"}
        return httpx.Response(200, content=HEBREW.encode(), headers=headers)

    page = await fetcher(handler).fetch("http://93.184.216.34/")
    assert HEBREW in page.html


async def test_iso_8859_8_i_is_read_as_iso_8859_8():
    def handler(request):
        body = f'<html><head><meta charset="iso-8859-8-i"></head><body>{HEBREW}</body></html>'
        return httpx.Response(200, content=body.encode("iso-8859-8"))

    page = await fetcher(handler).fetch("http://93.184.216.34/")
    assert HEBREW in page.html


def test_extract_ignores_a_malformed_base_href():
    html = """<html><head><base href="http://[bad"></head><body><article><p>Enough body text
      here to be kept by the extractor as the main content of this page, repeated so it clears
      the minimum length threshold.</p><a href="/ok">y</a></article></body></html>"""
    doc = extract(Page(url="https://site.com/dir/", html=html, content_type="text/html"))
    assert doc.links == ["https://site.com/ok"]


@pytest.mark.parametrize("url", ["https://a..b/", f"https://{'x' * 64}.example/"])
async def test_host_that_idna_cannot_encode_is_a_fetch_error(url):
    # The pre-check encodes the host for getaddrinfo before any DNS query is sent.
    with pytest.raises(FetchError, match="invalid host name"):
        await fetcher(lambda r: httpx.Response(200)).fetch(url)


@pytest.mark.parametrize("charset", ["undefined", "idna", "rot13", "base64", "zlib"])
async def test_charset_that_cannot_decode_text_falls_back_to_utf8(charset):
    def handler(request):
        body = f'<html><head><meta charset="{charset}"></head><body>{HEBREW}</body></html>'
        headers = {"content-type": f"text/html; charset={charset}"}
        return httpx.Response(200, content=body.encode(), headers=headers)

    page = await fetcher(handler).fetch("http://93.184.216.34/")
    assert HEBREW in page.html
