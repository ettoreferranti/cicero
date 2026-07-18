"""Controlled web search + fetch for debate evidence (FR-26..29, NFR-SEC-4/5).

Everything here is **off by default** and only reachable when the operator sets
``WEB_ACCESS_ENABLED`` *and* a chamber opts in. The security controls:

- **SSRF protection** (``validate_public_url``): only http/https on default
  ports; the hostname is resolved and every address must be globally routable —
  loopback, RFC-1918, link-local (incl. cloud metadata ``169.254.169.254``),
  carrier-grade NAT, and reserved ranges are all rejected. Redirects are
  re-validated hop by hop.
- **Allow/deny lists**: an optional domain allowlist (only matching hosts may be
  fetched) and denylist (always blocked), matched on the host or any subdomain.
- **Caps**: request timeout, response size cap (enforced while streaming), an
  allowlisted set of text MIME types, and a redirect cap.
- **Sanitisation** (``extract_text``): fetched HTML is reduced to plain text
  (scripts/styles dropped, whitespace collapsed, control characters stripped)
  before it can enter a prompt — and even then only inside the delimited
  transcript block (NFR-SEC-5).

The pure parts (URL validation, host matching, text extraction, result-link
parsing) are deterministic and unit-tested; network calls go through injectable
``httpx`` transports so tests never touch the network.

Known limitation (documented in docs/security.md): validation resolves DNS
separately from the fetch, so a fast-flux DNS rebind between the two lookups is
theoretically possible. The allowlist is the recommended mitigation for strict
deployments.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

import httpx

from cicero.domain.models import Citation

ALLOWED_SCHEMES = frozenset({"http", "https"})
#: Only default web ports — anything else smells like an internal service.
ALLOWED_PORTS = frozenset({80, 443})
ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain", "application/json"}
)
MAX_REDIRECTS = 3
_USER_AGENT = "CiceroEvidence/0.1 (+https://github.com/ettoreferranti/cicero)"


class WebEvidenceError(RuntimeError):
    """A web-evidence operation failed (safe to surface; carries no secrets)."""


class SsrfBlockedError(WebEvidenceError):
    """The target URL was rejected by the SSRF policy."""


#: Resolves a hostname to its IP addresses (injectable for tests).
Resolver = Callable[[str], list[str]]


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WebEvidenceError(f"cannot resolve host: {host}") from exc
    return [str(info[4][0]) for info in infos]


def host_matches(host: str, domain: str) -> bool:
    """True if ``host`` is ``domain`` or a subdomain of it (case-insensitive)."""
    host = host.lower().rstrip(".")
    domain = domain.lower().lstrip("*").lstrip(".").rstrip(".")
    if not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _require_global_address(value: str, url: str) -> None:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:
        raise SsrfBlockedError(f"blocked non-public address for {url}")


def validate_public_url(
    url: str,
    allowlist: Sequence[str] = (),
    denylist: Sequence[str] = (),
    resolver: Resolver = _default_resolver,
) -> None:
    """Reject ``url`` unless it points at a public web host (NFR-SEC-4).

    Raises:
        SsrfBlockedError: if the URL violates the SSRF policy.
        WebEvidenceError: if the URL is malformed or unresolvable.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise SsrfBlockedError(f"scheme not allowed: {parts.scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise WebEvidenceError("URL has no host")
    if parts.username is not None or parts.password is not None:
        raise SsrfBlockedError("credentials in URLs are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise WebEvidenceError("invalid port") from exc
    if port is not None and port not in ALLOWED_PORTS:
        raise SsrfBlockedError(f"port not allowed: {port}")
    if any(host_matches(host, blocked) for blocked in denylist):
        raise SsrfBlockedError(f"host is denylisted: {host}")
    if allowlist and not any(host_matches(host, allowed) for allowed in allowlist):
        raise SsrfBlockedError(f"host not in allowlist: {host}")

    try:
        _require_global_address(host, url)  # IP-literal host
        return
    except ValueError:
        pass  # a domain name — resolve it
    for address in resolver(host):
        try:
            _require_global_address(address, url)
        except ValueError as exc:
            raise WebEvidenceError(f"unparsable resolved address for {host}") from exc


_SKIPPED_ELEMENTS = frozenset({"script", "style", "noscript", "template", "svg", "head"})


class _TextExtractor(HTMLParser):
    """Strips an HTML document down to its visible text and title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_ELEMENTS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_ELEMENTS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._skip_depth == 0:
            self.chunks.append(data)


def _clean_text(raw: str) -> str:
    """Collapse whitespace and drop control characters (keep line structure out)."""
    cleaned = "".join(ch if ch.isprintable() or ch.isspace() else " " for ch in raw)
    return " ".join(cleaned.split())


def extract_text(html: str) -> tuple[str, str]:
    """Sanitise an HTML document to ``(title, visible_text)`` plain text."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return _clean_text(parser.title), _clean_text(" ".join(parser.chunks))


@dataclass(frozen=True)
class FetchedPage:
    """A sanitised, size-capped page fetched from the public web."""

    url: str
    title: str
    text: str


class SafeWebClient:
    """Fetches public pages under the SSRF policy, size caps, and MIME checks."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        allowlist: Sequence[str] = (),
        denylist: Sequence[str] = (),
        resolver: Resolver = _default_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._allowlist = tuple(allowlist)
        self._denylist = tuple(denylist)
        self._resolver = resolver
        self._transport = transport

    async def fetch_page(self, url: str) -> FetchedPage:
        """Fetch ``url``, re-validating every redirect hop (NFR-SEC-4)."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            validate_public_url(current, self._allowlist, self._denylist, self._resolver)
            try:
                redirect, page = await self._fetch_once(current)
            except httpx.HTTPError as exc:
                raise WebEvidenceError(f"fetch failed for {current}: {exc}") from exc
            if redirect is None:
                assert page is not None  # noqa: S101 (invariant of _fetch_once)
                return page
            current = redirect
        raise WebEvidenceError(f"too many redirects fetching {url}")

    async def _fetch_once(self, url: str) -> tuple[str | None, FetchedPage | None]:
        """One GET without following redirects: (redirect_target, page)."""
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise WebEvidenceError(f"redirect without location from {url}")
                    return str(httpx.URL(url).join(location)), None
                if response.status_code != httpx.codes.OK:
                    raise WebEvidenceError(f"HTTP {response.status_code} from {url}")
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise WebEvidenceError(f"content type not allowed: {content_type or '?'}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= self._max_bytes:
                        del body[self._max_bytes :]  # size cap (NFR-SEC-4)
                        break
                encoding = response.charset_encoding or "utf-8"
                raw = bytes(body).decode(encoding, errors="replace")
        if content_type in ("text/html", "application/xhtml+xml"):
            title, text = extract_text(raw)
        else:
            title, text = "", _clean_text(raw)
        return None, FetchedPage(url=url, title=title, text=text)


@dataclass(frozen=True)
class SearchHit:
    """One search-engine result."""

    url: str
    title: str


class SearchBackend(Protocol):
    """A pluggable web-search implementation."""

    async def search(self, query: str, max_results: int) -> list[SearchHit]: ...


class _DuckDuckGoResultParser(HTMLParser):
    """Extracts result links (``<a class="result__a">``) from DDG's HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[SearchHit] = []
        self._current_href: str | None = None
        self._current_title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        href = attributes.get("href")
        if "result__a" in classes and href:
            self._current_href = href
            self._current_title = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            url = resolve_ddg_link(self._current_href)
            if url is not None:
                title = " ".join("".join(self._current_title).split())
                self.hits.append(SearchHit(url=url, title=title))
            self._current_href = None


def resolve_ddg_link(href: str) -> str | None:
    """Unwrap a DuckDuckGo result link to the target URL (or ``None``)."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.path.startswith("/l/"):  # DDG redirect wrapper: /l/?uddg=<target>
        target = parse_qs(parts.query).get("uddg", [""])[0]
        return target or None
    if parts.scheme in ALLOWED_SCHEMES and parts.hostname:
        return href
    return None


def parse_ddg_results(html: str, max_results: int) -> list[SearchHit]:
    """Parse DDG's HTML results page into hits (pure, unit-tested)."""
    parser = _DuckDuckGoResultParser()
    parser.feed(html)
    parser.close()
    return parser.hits[:max_results]


class DuckDuckGoBackend:
    """Key-less search via DuckDuckGo's HTML endpoint."""

    SEARCH_URL = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            try:
                response = await client.get(self.SEARCH_URL, params={"q": query})
            except httpx.HTTPError as exc:
                raise WebEvidenceError(f"search failed: {exc}") from exc
        if response.status_code != httpx.codes.OK:
            raise WebEvidenceError(f"search failed: HTTP {response.status_code}")
        return parse_ddg_results(response.text, max_results)


class EvidenceService:
    """Search + fetch + sanitise, producing citations for a debate (FR-28)."""

    #: Cap on the sanitised excerpt stored per citation.
    EXCERPT_CHARS = 1500

    def __init__(
        self, search: SearchBackend, client: SafeWebClient, max_results: int = 3
    ) -> None:
        self._search = search
        self._client = client
        self._max_results = max_results

    async def gather(self, topic: str) -> list[Citation]:
        """Best-effort evidence for ``topic``; unreachable/blocked sources are skipped."""
        query = " ".join(topic.split())[:400]
        hits = await self._search.search(query, self._max_results * 2)
        citations: list[Citation] = []
        for hit in hits:
            if len(citations) >= self._max_results:
                break
            if len(hit.url) > 2048:
                continue
            try:
                page = await self._client.fetch_page(hit.url)
            except WebEvidenceError:
                continue
            excerpt = page.text[: self.EXCERPT_CHARS].strip()
            if not excerpt:
                continue
            citations.append(
                Citation(
                    url=page.url,
                    title=(page.title or hit.title)[:512],
                    excerpt=excerpt,
                )
            )
        return citations
