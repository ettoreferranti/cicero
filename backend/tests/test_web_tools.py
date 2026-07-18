"""Tests for the sandboxed web-evidence tools (Epic G) — no real network."""

from __future__ import annotations

import httpx
import pytest

from cicero.tools.web import (
    DuckDuckGoBackend,
    EvidenceService,
    SafeWebClient,
    SearchHit,
    SsrfBlockedError,
    WebEvidenceError,
    extract_text,
    host_matches,
    parse_ddg_results,
    resolve_ddg_link,
    validate_public_url,
)

PUBLIC_IP = "93.184.216.34"

#: hostname -> resolved addresses used by every test (no real DNS).
_FAKE_DNS = {
    "example.org": [PUBLIC_IP],
    "sub.example.org": [PUBLIC_IP],
    "evil.example": ["10.0.0.5"],
    "half-evil.example": [PUBLIC_IP, "127.0.0.1"],  # any private address blocks
    "localhost": ["127.0.0.1"],
    "metadata.internal": ["169.254.169.254"],
    "cgnat.example": ["100.64.0.1"],
    "v6-local.example": ["::1"],
}


def fake_resolver(host: str) -> list[str]:
    try:
        return _FAKE_DNS[host]
    except KeyError:
        raise WebEvidenceError(f"cannot resolve host: {host}") from None


# --- URL validation (SSRF policy) --------------------------------------------


def test_host_matches_domain_and_subdomains() -> None:
    assert host_matches("example.org", "example.org") is True
    assert host_matches("a.b.example.org", "example.org") is True
    assert host_matches("Example.ORG", "example.org") is True
    assert host_matches("notexample.org", "example.org") is False
    assert host_matches("example.org.evil.com", "example.org") is False
    assert host_matches("example.org", "") is False


def test_public_url_accepted() -> None:
    validate_public_url("https://example.org/page", resolver=fake_resolver)
    validate_public_url("http://example.org:80/", resolver=fake_resolver)
    validate_public_url(f"https://{PUBLIC_IP}/x", resolver=fake_resolver)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/",
        "gopher://example.org/",
        "https://user:pass@example.org/",
        "https://example.org:8080/",  # non-default port
        "http://localhost/",
        "http://metadata.internal/latest",  # cloud metadata via DNS
        "http://169.254.169.254/latest",  # cloud metadata as IP literal
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://cgnat.example/",  # carrier-grade NAT (100.64/10)
        "http://evil.example/",  # resolves to RFC-1918
        "http://half-evil.example/",  # one public + one private address
        "http://v6-local.example/",
    ],
)
def test_non_public_targets_blocked(url: str) -> None:
    with pytest.raises(SsrfBlockedError):
        validate_public_url(url, resolver=fake_resolver)


def test_allowlist_and_denylist() -> None:
    validate_public_url(
        "https://sub.example.org/", allowlist=["example.org"], resolver=fake_resolver
    )
    with pytest.raises(SsrfBlockedError, match="allowlist"):
        validate_public_url(
            f"https://{PUBLIC_IP}/", allowlist=["example.org"], resolver=fake_resolver
        )
    with pytest.raises(SsrfBlockedError, match="denylisted"):
        validate_public_url(
            "https://sub.example.org/", denylist=["example.org"], resolver=fake_resolver
        )


def test_unresolvable_host_errors() -> None:
    with pytest.raises(WebEvidenceError, match="cannot resolve"):
        validate_public_url("https://nxdomain.example.net/", resolver=fake_resolver)


# --- Sanitisation -------------------------------------------------------------


def test_extract_text_strips_markup_and_scripts() -> None:
    html = (
        "<html><head><title>My Page</title><style>b{color:red}</style></head>"
        "<body><h1>Heading</h1><script>alert('x')</script>"
        "<p>Visible &amp; important.</p><noscript>hidden</noscript></body></html>"
    )
    title, text = extract_text(html)
    assert title == "My Page"
    assert "Heading" in text
    assert "Visible & important." in text
    assert "alert" not in text
    assert "color:red" not in text
    assert "hidden" not in text


def test_extract_text_collapses_whitespace_and_control_chars() -> None:
    _, text = extract_text("<p>a\n\n   b\x00c</p>")
    assert text == "a b c"


# --- SafeWebClient ------------------------------------------------------------


def _client(handler: httpx.MockTransport, **kwargs: object) -> SafeWebClient:
    defaults: dict[str, object] = {
        "timeout_seconds": 5.0,
        "max_response_bytes": 1000,
        "resolver": fake_resolver,
        "transport": handler,
    }
    defaults.update(kwargs)
    return SafeWebClient(**defaults)  # type: ignore[arg-type]


async def test_fetch_page_sanitises_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<title>T</title><p>Body text</p><script>x()</script>",
        )

    page = await _client(httpx.MockTransport(handler)).fetch_page("https://example.org/a")
    assert page.title == "T"
    assert page.text == "Body text"


async def test_fetch_rejects_disallowed_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    with pytest.raises(WebEvidenceError, match="content type"):
        await _client(httpx.MockTransport(handler)).fetch_page("https://example.org/doc")


async def test_fetch_caps_response_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"x" * 5000
        )

    page = await _client(
        httpx.MockTransport(handler), max_response_bytes=100
    ).fetch_page("https://example.org/big")
    assert len(page.text) <= 100


async def test_redirect_to_private_host_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://metadata.internal/steal"})

    with pytest.raises(SsrfBlockedError):
        await _client(httpx.MockTransport(handler)).fetch_page("https://example.org/redir")


async def test_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.org/loop"})

    with pytest.raises(WebEvidenceError, match="too many redirects"):
        await _client(httpx.MockTransport(handler)).fetch_page("https://example.org/loop")


async def test_http_error_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, text="nope")

    with pytest.raises(WebEvidenceError, match="HTTP 404"):
        await _client(httpx.MockTransport(handler)).fetch_page("https://example.org/miss")


# --- DuckDuckGo parsing -------------------------------------------------------

_DDG_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fone&rut=abc">
    First <b>Result</b></a>
</div>
<div class="result">
  <a class="result__a" href="https://example.org/two">Second Result</a>
</div>
<a href="https://ignored.example/nav">nav link</a>
<div class="result">
  <a class="result__a" href="javascript:alert(1)">Bad Result</a>
</div>
"""


def test_resolve_ddg_link_unwraps_redirects() -> None:
    assert (
        resolve_ddg_link("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fone")
        == "https://example.org/one"
    )
    assert resolve_ddg_link("https://example.org/two") == "https://example.org/two"
    assert resolve_ddg_link("javascript:alert(1)") is None


def test_parse_ddg_results_extracts_hits_in_order() -> None:
    hits = parse_ddg_results(_DDG_HTML, max_results=5)
    assert [h.url for h in hits] == ["https://example.org/one", "https://example.org/two"]
    assert hits[0].title == "First Result"
    assert parse_ddg_results(_DDG_HTML, max_results=1) == hits[:1]


async def test_ddg_backend_searches_via_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "mars colonisation"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_DDG_HTML)

    backend = DuckDuckGoBackend(transport=httpx.MockTransport(handler))
    hits = await backend.search("mars colonisation", max_results=5)
    assert len(hits) == 2


# --- EvidenceService ----------------------------------------------------------


class StubSearch:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        return self._hits[:max_results]


async def test_gather_skips_failures_and_caps_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/broken":
            return httpx.Response(500, headers={"content-type": "text/html"}, text="boom")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<title>T</title><p>content of {request.url.path}</p>",
        )

    search = StubSearch(
        [
            SearchHit(url="https://example.org/broken", title="Broken"),
            SearchHit(url="http://localhost/private", title="Blocked"),
            SearchHit(url="https://example.org/a", title="A"),
            SearchHit(url="https://example.org/b", title="B"),
            SearchHit(url="https://example.org/c", title="C"),
        ]
    )
    service = EvidenceService(
        search, _client(httpx.MockTransport(handler)), max_results=2
    )
    citations = await service.gather("Should we colonise Mars?")
    assert [c.url for c in citations] == ["https://example.org/a", "https://example.org/b"]
    assert all(c.excerpt for c in citations)
    assert citations[0].title == "T"
