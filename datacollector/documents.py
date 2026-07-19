"""Safe, deterministic retrieval and text extraction for public documents.

The module deliberately has no dependency on the agent schemas.  It is a small
boundary around untrusted URLs and untrusted HTML/PDF bytes which can be mapped
to the versioned Pydantic contracts by the Extractor agent.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import multiprocessing
import re
import socket
import ssl
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from html.parser import HTMLParser
from io import BytesIO
from time import monotonic
from typing import Protocol
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit


class FetchStatus(StrEnum):
    """Stable result codes understood by the future Extractor contract."""

    FETCHED = "fetched"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    HTTP_ERROR = "http_error"
    INVALID_URL = "invalid_url"
    SSRF_BLOCKED = "ssrf_blocked"
    REDIRECT_BLOCKED = "redirect_blocked"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    TOO_LARGE = "too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    CONTENT_TYPE_MISMATCH = "content_type_mismatch"
    ANTI_BOT = "anti_bot"
    TIMEOUT = "timeout"
    TLS_ERROR = "tls_error"
    NETWORK_ERROR = "network_error"
    PARSE_FAILED = "parse_failed"
    ENCRYPTED_PDF = "encrypted_pdf"
    OCR_REQUIRED = "ocr_required"
    OFFICIAL_RESOLUTION_FAILED = "official_resolution_failed"


@dataclass(frozen=True)
class FetchPolicy:
    """Hard resource and network limits for one public document."""

    allowed_ports: tuple[int, ...] = (80, 443)
    max_redirects: int = 5
    max_html_bytes: int = 5 * 1024 * 1024
    max_pdf_bytes: int = 40 * 1024 * 1024
    max_json_bytes: int = 1024 * 1024
    max_text_chars: int = 3_000_000
    max_pdf_pages: int = 1_000
    pdf_parse_timeout_seconds: float = 30.0
    pdf_worker_memory_bytes: int = 512 * 1024 * 1024
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    total_timeout_seconds: float = 45.0
    user_agent: str = "SaaSFranchiseResearchBot/0.1"
    allow_http: bool = True
    allow_ip_literals: bool = False

    def __post_init__(self) -> None:
        if not self.allowed_ports or any(
            port < 1 or port > 65_535 for port in self.allowed_ports
        ):
            raise ValueError("allowed_ports must contain valid TCP ports.")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative.")
        positive_values = (
            self.max_html_bytes,
            self.max_pdf_bytes,
            self.max_json_bytes,
            self.max_text_chars,
            self.max_pdf_pages,
            self.pdf_parse_timeout_seconds,
            self.pdf_worker_memory_bytes,
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.total_timeout_seconds,
        )
        if any(value <= 0 for value in positive_values):
            raise ValueError("Fetch limits and timeouts must be positive.")
        if not self.user_agent.strip() or any(
            character in self.user_agent for character in "\r\n"
        ):
            raise ValueError("user_agent must be a non-empty single line.")


@dataclass(frozen=True)
class RedirectHop:
    status_code: int
    from_url: str
    to_url: str


MetadataValue = str | int | float | bool | None


@dataclass(frozen=True)
class FetchedDocument:
    """Immutable result of fetching and locally parsing one source URL."""

    source_id: str
    requested_url: str
    final_url: str | None
    status: FetchStatus
    fetched_at: datetime
    http_status: int | None = None
    media_type: str | None = None
    content: bytes | None = None
    text: str = ""
    title: str = ""
    page_text: tuple[str, ...] = ()
    page_count: int | None = None
    parsed_pages: int | None = None
    byte_count: int = 0
    content_sha256: str | None = None
    text_sha256: str | None = None
    response_headers: tuple[tuple[str, str], ...] = ()
    redirects: tuple[RedirectHop, ...] = ()
    resolved_via: str = "direct"
    official_metadata: tuple[tuple[str, MetadataValue], ...] = ()
    warnings: tuple[str, ...] = ()
    error_code: str | None = None

    def official_metadata_dict(self) -> dict[str, MetadataValue]:
        return dict(self.official_metadata)


@dataclass(frozen=True)
class ParsedContent:
    status: FetchStatus
    text: str = ""
    title: str = ""
    page_text: tuple[str, ...] = ()
    page_count: int | None = None
    parsed_pages: int | None = None
    warnings: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class URLPolicyError(ValueError):
    def __init__(self, message: str, *, code: str, ssrf: bool = False):
        super().__init__(message)
        self.code = code
        self.ssrf = ssrf


class TransportTimeoutError(TimeoutError):
    pass


class TransportTLSError(ConnectionError):
    pass


class TransportNetworkError(ConnectionError):
    pass


class Resolver(Protocol):
    def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class SystemResolver:
    """Resolve TCP addresses; policy validation happens after resolution."""

    def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            raise TransportNetworkError("DNS resolution failed.") from exc
        return tuple(dict.fromkeys(record[4][0] for record in records))


@dataclass
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    chunks: Iterable[bytes]
    close_callback: Callable[[], None] = lambda: None

    def close(self) -> None:
        self.close_callback()


class PinnedTransport(Protocol):
    def request(
        self,
        target: ValidatedTarget,
        *,
        headers: Mapping[str, str],
        connect_timeout: float,
        read_timeout: float,
    ) -> TransportResponse: ...


class Urllib3PinnedTransport:
    """Connect to a validated IP while preserving HTTP Host and TLS SNI."""

    def request(
        self,
        target: ValidatedTarget,
        *,
        headers: Mapping[str, str],
        connect_timeout: float,
        read_timeout: float,
    ) -> TransportResponse:
        try:
            from urllib3 import HTTPConnectionPool, HTTPSConnectionPool
            from urllib3.exceptions import (
                ConnectTimeoutError,
                MaxRetryError,
                NewConnectionError,
                ReadTimeoutError,
                SSLError,
            )
            from urllib3.util import Timeout
        except ImportError as exc:  # pragma: no cover - dependency is installed
            raise TransportNetworkError("urllib3 is not installed.") from exc

        parsed = urlsplit(target.url)
        request_target = urlunsplit(
            (
                "",
                "",
                quote(
                    parsed.path or "/",
                    safe="/%:@!$&'()*+,;=-._~",
                ),
                quote(
                    parsed.query,
                    safe="=&%:@!$'()*+,;/?-._~",
                ),
                "",
            )
        )
        host_header = target.hostname
        if (target.scheme, target.port) not in {("http", 80), ("https", 443)}:
            host_header = f"{host_header}:{target.port}"
        request_headers = {**headers, "Host": host_header}
        timeout = Timeout(connect=connect_timeout, read=read_timeout)
        last_error: Exception | None = None

        for address in target.addresses:
            pool = None
            try:
                if target.scheme == "https":
                    pool = HTTPSConnectionPool(
                        address,
                        port=target.port,
                        maxsize=1,
                        block=True,
                        retries=False,
                        cert_reqs=ssl.CERT_REQUIRED,
                        assert_hostname=target.hostname,
                        server_hostname=target.hostname,
                    )
                else:
                    pool = HTTPConnectionPool(
                        address,
                        port=target.port,
                        maxsize=1,
                        block=True,
                        retries=False,
                    )
                response = pool.urlopen(
                    "GET",
                    request_target,
                    headers=request_headers,
                    redirect=False,
                    retries=False,
                    preload_content=False,
                    decode_content=False,
                    timeout=timeout,
                )

                def chunks() -> Iterable[bytes]:
                    try:
                        while True:
                            piece = response.read(64 * 1024, decode_content=False)
                            if not piece:
                                break
                            yield piece
                    except ReadTimeoutError as exc:
                        raise TransportTimeoutError(
                            "HTTP response stream timed out."
                        ) from exc
                    except SSLError as exc:
                        raise TransportTLSError(
                            "TLS response stream failed."
                        ) from exc
                    except OSError as exc:
                        raise TransportNetworkError(
                            "HTTP response stream failed."
                        ) from exc

                def close() -> None:
                    response.release_conn()
                    pool.close()

                return TransportResponse(
                    status_code=response.status,
                    headers={key: value for key, value in response.headers.items()},
                    chunks=chunks(),
                    close_callback=close,
                )
            except (ConnectTimeoutError, ReadTimeoutError) as exc:
                last_error = TransportTimeoutError("HTTP request timed out.")
                if pool is not None:
                    pool.close()
            except SSLError as exc:
                if pool is not None:
                    pool.close()
                raise TransportTLSError("TLS validation failed.") from exc
            except (MaxRetryError, NewConnectionError, OSError) as exc:
                last_error = exc
                if pool is not None:
                    pool.close()

        if isinstance(last_error, TransportTimeoutError):
            raise last_error
        raise TransportNetworkError("No validated address could be reached.") from last_error


_BLOCKED_HOST_SUFFIXES = (
    ".home",
    ".internal",
    ".lan",
    ".local",
    ".localhost",
)
_ISAP_ID = re.compile(r"^W(DU|MP)(\d{4})(\d{3})(\d{4})$", re.IGNORECASE)
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_HTML_MEDIA = {"text/html", "application/xhtml+xml"}
_PDF_MEDIA = {"application/pdf"}
_JSON_MEDIA = {"application/json", "application/ld+json"}
_PLAIN_MEDIA = {"text/plain"}
_SAFE_RESPONSE_HEADERS = {
    "content-language",
    "content-length",
    "content-type",
    "date",
    "etag",
    "last-modified",
}
_ANTI_BOT_MARKERS = (
    "access denied",
    "are you a robot",
    "attention required",
    "bot verification",
    "captcha",
    "cf-chl-",
    "cf-turnstile",
    "checking your browser",
    "cloudflare ray id",
    "enable javascript and cookies",
    "g-recaptcha",
    "hcaptcha",
    "human verification",
    "incapsula incident id",
    "just a moment",
    "security verification",
    "verify you are human",
)


def _contains_anti_bot_marker(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _ANTI_BOT_MARKERS)


def _bytes_contain_anti_bot_marker(content: bytes) -> bool:
    prefix = content[:16_384].decode("utf-8", errors="ignore")
    return _contains_anti_bot_marker(prefix)


def _is_pdf_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        return urlsplit(value).path.casefold().endswith(".pdf")
    except ValueError:
        return False


def validate_public_url(
    value: str,
    resolver: Resolver,
    policy: FetchPolicy = FetchPolicy(),
) -> ValidatedTarget:
    """Validate a URL and pin all DNS results used by the transport."""

    if not isinstance(value, str) or not value or len(value) > 4_000:
        raise URLPolicyError("URL is empty or too long.", code="invalid_url")
    if "\\" in value or any(character.isspace() or ord(character) < 32 for character in value):
        raise URLPolicyError("URL contains unsafe characters.", code="unsafe_characters")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise URLPolicyError("URL cannot be parsed.", code="invalid_url") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or (scheme == "http" and not policy.allow_http):
        raise URLPolicyError("URL scheme is not allowed.", code="unsupported_scheme")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("URL credentials are not allowed.", code="userinfo")
    if not parsed.hostname:
        raise URLPolicyError("URL has no hostname.", code="missing_hostname")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
        port = parsed.port or (443 if scheme == "https" else 80)
    except (UnicodeError, ValueError) as exc:
        raise URLPolicyError("URL hostname or port is invalid.", code="invalid_host") from exc
    if port not in policy.allowed_ports:
        raise URLPolicyError("URL port is not allowed.", code="blocked_port", ssrf=True)
    if hostname == "localhost" or hostname.endswith(_BLOCKED_HOST_SUFFIXES):
        raise URLPolicyError("Local hostname is not allowed.", code="local_hostname", ssrf=True)

    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not policy.allow_ip_literals:
        raise URLPolicyError("IP-literal URLs are not allowed.", code="ip_literal", ssrf=True)
    if literal is None and re.fullmatch(r"[0-9.]+", hostname):
        raise URLPolicyError("Ambiguous numeric hostname is not allowed.", code="numeric_host", ssrf=True)

    try:
        addresses = (str(literal),) if literal is not None else resolver.resolve(hostname, port)
    except TransportNetworkError:
        raise
    if not addresses:
        raise TransportNetworkError("DNS returned no addresses.")
    normalized_addresses: list[str] = []
    for address_text in addresses:
        if "%" in address_text:
            raise URLPolicyError("Scoped IP addresses are not allowed.", code="scoped_ip", ssrf=True)
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise URLPolicyError("DNS returned an invalid address.", code="invalid_dns_address", ssrf=True) from exc
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if not address.is_global or (mapped is not None and not mapped.is_global):
            raise URLPolicyError("DNS resolved to a non-public address.", code="non_public_address", ssrf=True)
        normalized = address.compressed
        if normalized not in normalized_addresses:
            normalized_addresses.append(normalized)

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme, port) in {("http", 80), ("https", 443)}
    netloc = rendered_host if default_port else f"{rendered_host}:{port}"
    canonical = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    return ValidatedTarget(
        url=canonical,
        scheme=scheme,
        hostname=hostname,
        port=port,
        addresses=tuple(normalized_addresses),
    )


@dataclass(frozen=True)
class _RawFetch:
    status: FetchStatus
    requested_url: str
    final_url: str | None
    fetched_at: datetime
    http_status: int | None = None
    media_type: str | None = None
    content: bytes | None = None
    headers: tuple[tuple[str, str], ...] = ()
    redirects: tuple[RedirectHop, ...] = ()
    warnings: tuple[str, ...] = ()
    error_code: str | None = None


class _VisibleHTMLParser(HTMLParser):
    _SKIPPED = {"canvas", "noscript", "script", "style", "svg", "template"}
    _BLOCKS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "figcaption", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._title_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._SKIPPED:
            self._skip_depth += 1
        if tag == "title":
            self._title_depth += 1
        if not self._skip_depth and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skip_depth and tag.casefold() in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self._SKIPPED and self._skip_depth:
            self._skip_depth -= 1
        if not self._skip_depth and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self.title_parts.append(data)
        else:
            self.parts.append(data)


def _normalize_visible_text(parts: Iterable[str]) -> str:
    rendered = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in rendered.split("\n")]
    normalized: list[str] = []
    previous_blank = True
    for line in lines:
        if line:
            normalized.append(line)
            previous_blank = False
        elif not previous_blank:
            normalized.append("")
            previous_blank = True
    return "\n".join(normalized).strip()


def _declared_charset(content_type: str) -> str | None:
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.IGNORECASE)
    return match.group(1) if match else None


def _decode_text(content: bytes, content_type: str) -> tuple[str, tuple[str, ...]]:
    charset = _declared_charset(content_type)
    if charset:
        try:
            return content.decode(charset), ()
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return content.decode("utf-8-sig"), (() if charset is None else ("declared_charset_failed",))
    except UnicodeDecodeError:
        try:
            from charset_normalizer import from_bytes

            match = from_bytes(content[: 256 * 1024]).best()
            if match is not None and match.encoding:
                return content.decode(match.encoding, errors="replace"), ("charset_detected",)
        except ImportError:  # pragma: no cover - optional fallback
            pass
        return content.decode("utf-8", errors="replace"), ("charset_replacement_used",)


def extract_html(content: bytes, content_type: str, policy: FetchPolicy) -> ParsedContent:
    decoded, warnings = _decode_text(content, content_type)
    parser = _VisibleHTMLParser()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception:
        return ParsedContent(status=FetchStatus.PARSE_FAILED, error_code="html_parse_failed")
    text = _normalize_visible_text(parser.parts)
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    status = FetchStatus.FETCHED
    if len(text) > policy.max_text_chars:
        text = text[: policy.max_text_chars]
        status = FetchStatus.PARTIAL
        warnings = (*warnings, "text_truncated")
    short_challenge = len(text) < 20_000
    if _contains_anti_bot_marker(title) or (
        short_challenge and _contains_anti_bot_marker(decoded[:16_384])
    ):
        return ParsedContent(
            status=FetchStatus.ANTI_BOT,
            title=title,
            warnings=(*warnings, "anti_bot_page_detected"),
            error_code="anti_bot_page",
        )
    return ParsedContent(
        status=status,
        text=text,
        title=title,
        warnings=warnings,
    )


def _extract_pdf_in_process(content: bytes, policy: FetchPolicy) -> ParsedContent:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ParsedContent(
            status=FetchStatus.PARSE_FAILED,
            warnings=("pypdf_not_installed",),
            error_code="pypdf_not_installed",
        )
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            return ParsedContent(
                status=FetchStatus.ENCRYPTED_PDF,
                error_code="encrypted_pdf",
            )
        total_pages = len(reader.pages)
        page_limit = min(total_pages, policy.max_pdf_pages)
        pages: list[str] = []
        text_length = 0
        truncated = total_pages > page_limit
        for page in reader.pages[:page_limit]:
            page_value = page.extract_text() or ""
            remaining = policy.max_text_chars - text_length
            if len(page_value) > remaining:
                page_value = page_value[: max(remaining, 0)]
                truncated = True
            pages.append(page_value.strip())
            text_length += len(page_value)
            if text_length >= policy.max_text_chars:
                break
    except MemoryError:
        return ParsedContent(
            status=FetchStatus.PARSE_FAILED,
            warnings=("pdf_worker_memory_limit",),
            error_code="pdf_worker_memory_limit",
        )
    except Exception:
        return ParsedContent(
            status=FetchStatus.PARSE_FAILED,
            error_code="pdf_parse_failed",
        )
    text = "\n\f\n".join(pages).strip()
    if not text:
        return ParsedContent(
            status=FetchStatus.OCR_REQUIRED,
            page_text=tuple(pages),
            page_count=total_pages,
            parsed_pages=len(pages),
            error_code="pdf_has_no_extractable_text",
        )
    return ParsedContent(
        status=FetchStatus.PARTIAL if truncated else FetchStatus.FETCHED,
        text=text,
        page_text=tuple(pages),
        page_count=total_pages,
        parsed_pages=len(pages),
        warnings=(("pdf_text_truncated",) if truncated else ()),
    )


def _apply_pdf_worker_limits(policy: FetchPolicy) -> bool:
    """Apply best-effort Unix hard limits inside the disposable PDF worker."""

    try:
        import resource
    except ImportError:  # pragma: no cover - Windows fails closed in the parent
        return False
    try:
        memory_hard = resource.getrlimit(resource.RLIMIT_AS)[1]
        memory_limit = policy.pdf_worker_memory_bytes
        if memory_hard != resource.RLIM_INFINITY:
            memory_limit = min(memory_limit, memory_hard)
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))

        cpu_soft = max(1, math.ceil(policy.pdf_parse_timeout_seconds))
        cpu_hard = cpu_soft + 1
        existing_cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
        if existing_cpu_hard != resource.RLIM_INFINITY:
            cpu_hard = min(cpu_hard, existing_cpu_hard)
            cpu_soft = min(cpu_soft, cpu_hard)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_soft, cpu_hard))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def _pdf_worker(
    connection,
    content: bytes,
    policy: FetchPolicy,
) -> None:
    """Parse one PDF in a child process and return a bounded data object."""

    try:
        logging.getLogger("pypdf").setLevel(logging.CRITICAL)
        if not _apply_pdf_worker_limits(policy):
            parsed = ParsedContent(
                status=FetchStatus.PARSE_FAILED,
                warnings=("pdf_worker_limits_unavailable",),
                error_code="pdf_worker_limits_unavailable",
            )
        else:
            parsed = _extract_pdf_in_process(content, policy)
        connection.send(parsed)
    except BaseException:
        # The parent treats EOF or a dead worker as a parse failure.  Avoid
        # propagating untrusted-parser failures into the Extractor process.
        pass
    finally:
        connection.close()


def extract_pdf(content: bytes, policy: FetchPolicy) -> ParsedContent:
    """Parse a PDF in a disposable process with wall-clock and memory caps."""

    receiving = None
    sending = None
    process = None
    try:
        context = multiprocessing.get_context("spawn")
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(
            target=_pdf_worker,
            args=(sending, content, policy),
            name="datacollector-pdf-parser",
        )
        started = monotonic()
        process.start()
        sending.close()
    except (OSError, RuntimeError, ValueError):
        if receiving is not None:
            receiving.close()
        if sending is not None:
            sending.close()
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        return ParsedContent(
            status=FetchStatus.PARSE_FAILED,
            warnings=("pdf_worker_start_failed",),
            error_code="pdf_worker_start_failed",
        )

    parsed: ParsedContent | None = None
    timed_out = False
    try:
        remaining = max(
            0.0,
            policy.pdf_parse_timeout_seconds - (monotonic() - started),
        )
        if receiving.poll(remaining):
            try:
                candidate = receiving.recv()
            except (EOFError, OSError):
                candidate = None
            if isinstance(candidate, ParsedContent):
                parsed = candidate
        else:
            timed_out = True
    finally:
        receiving.close()
        process.join(timeout=0.2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        if process.is_alive():  # pragma: no cover - defensive OS fallback
            process.kill()
            process.join(timeout=1.0)

    if timed_out:
        return ParsedContent(
            status=FetchStatus.PARSE_FAILED,
            warnings=("pdf_parse_timeout",),
            error_code="pdf_parse_timeout",
        )
    if parsed is None:
        return ParsedContent(
            status=FetchStatus.PARSE_FAILED,
            warnings=("pdf_worker_failed",),
            error_code="pdf_worker_failed",
        )
    return parsed


PDFParser = Callable[[bytes, FetchPolicy], ParsedContent]


class DocumentFetcher:
    """Fetch exact public URLs without crawling or executing active content."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        transport: PinnedTransport | None = None,
        policy: FetchPolicy = FetchPolicy(),
        pdf_parser: PDFParser | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self.resolver = resolver or SystemResolver()
        self.transport = transport or Urllib3PinnedTransport()
        self.policy = policy
        self.pdf_parser = pdf_parser or extract_pdf
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock or monotonic

    def fetch(self, url: str, *, source_id: str = "") -> FetchedDocument:
        deadline = self.monotonic_clock() + self.policy.total_timeout_seconds
        isap = self._parse_isap_reference(url)
        if isap is not None:
            return self._fetch_isap(url, source_id, isap, deadline=deadline)
        raw = self._download(url, allowed="document", deadline=deadline)
        return self._build_document(source_id, raw)

    def _download(
        self,
        url: str,
        *,
        allowed: str,
        deadline: float | None = None,
    ) -> _RawFetch:
        fetched_at = self.clock()
        if deadline is None:
            deadline = self.monotonic_clock() + self.policy.total_timeout_seconds
        redirects: list[RedirectHop] = []
        seen: set[str] = set()
        if self.monotonic_clock() >= deadline:
            return self._raw_error(
                url,
                None,
                fetched_at,
                redirects,
                FetchStatus.TIMEOUT,
                "total_timeout",
            )
        try:
            target = validate_public_url(url, self.resolver, self.policy)
        except URLPolicyError as exc:
            return _RawFetch(
                status=FetchStatus.SSRF_BLOCKED if exc.ssrf else FetchStatus.INVALID_URL,
                requested_url=url,
                final_url=None,
                fetched_at=fetched_at,
                error_code=exc.code,
            )
        except TransportNetworkError:
            if self.monotonic_clock() >= deadline:
                return self._raw_error(
                    url,
                    None,
                    fetched_at,
                    redirects,
                    FetchStatus.TIMEOUT,
                    "total_timeout",
                )
            return _RawFetch(
                status=FetchStatus.NETWORK_ERROR,
                requested_url=url,
                final_url=None,
                fetched_at=fetched_at,
                error_code="dns_failed",
            )

        if self.monotonic_clock() >= deadline:
            return self._raw_error(
                url,
                target.url,
                fetched_at,
                redirects,
                FetchStatus.TIMEOUT,
                "total_timeout",
            )
        while True:
            remaining = deadline - self.monotonic_clock()
            if remaining <= 0:
                return self._raw_error(
                    url,
                    target.url,
                    fetched_at,
                    redirects,
                    FetchStatus.TIMEOUT,
                    "total_timeout",
                )
            if target.url in seen:
                return _RawFetch(
                    status=FetchStatus.TOO_MANY_REDIRECTS,
                    requested_url=url,
                    final_url=target.url,
                    fetched_at=fetched_at,
                    redirects=tuple(redirects),
                    error_code="redirect_loop",
                )
            seen.add(target.url)
            try:
                response = self.transport.request(
                    target,
                    headers={
                        "Accept": (
                            "application/json"
                            if allowed == "json"
                            else "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9"
                        ),
                        "Accept-Encoding": "identity",
                        "User-Agent": self.policy.user_agent,
                    },
                    connect_timeout=min(
                        self.policy.connect_timeout_seconds,
                        remaining,
                    ),
                    read_timeout=min(
                        self.policy.read_timeout_seconds,
                        remaining,
                    ),
                )
            except TransportTimeoutError:
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.TIMEOUT, "request_timeout")
            except TransportTLSError:
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.TLS_ERROR, "tls_error")
            except (TransportNetworkError, OSError):
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.NETWORK_ERROR, "network_error")

            if self.monotonic_clock() >= deadline:
                response.close()
                return self._raw_error(
                    url,
                    target.url,
                    fetched_at,
                    redirects,
                    FetchStatus.TIMEOUT,
                    "total_timeout",
                )
            headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
            status_code = response.status_code
            if status_code in _REDIRECT_CODES:
                location = headers.get("location")
                response.close()
                if not location:
                    return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.HTTP_ERROR, "redirect_without_location", status_code)
                if len(redirects) >= self.policy.max_redirects:
                    return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.TOO_MANY_REDIRECTS, "redirect_limit", status_code)
                next_value = urljoin(target.url, location)
                try:
                    next_target = validate_public_url(next_value, self.resolver, self.policy)
                except (URLPolicyError, TransportNetworkError) as exc:
                    if self.monotonic_clock() >= deadline:
                        return self._raw_error(
                            url,
                            target.url,
                            fetched_at,
                            redirects,
                            FetchStatus.TIMEOUT,
                            "total_timeout",
                            status_code,
                        )
                    code = exc.code if isinstance(exc, URLPolicyError) else "redirect_dns_failed"
                    return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.REDIRECT_BLOCKED, f"redirect_{code}", status_code)
                if self.monotonic_clock() >= deadline:
                    return self._raw_error(
                        url,
                        target.url,
                        fetched_at,
                        redirects,
                        FetchStatus.TIMEOUT,
                        "total_timeout",
                        status_code,
                    )
                if target.scheme == "https" and next_target.scheme != "https":
                    return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.REDIRECT_BLOCKED, "redirect_https_downgrade", status_code)
                redirects.append(RedirectHop(status_code, target.url, next_target.url))
                target = next_target
                continue

            if status_code in {401, 403, 407}:
                response.close()
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.ACCESS_DENIED, "access_denied", status_code)
            if status_code in {404, 410}:
                response.close()
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.NOT_FOUND, "not_found", status_code)
            if status_code == 429:
                response.close()
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.RATE_LIMITED, "rate_limited", status_code)
            if not 200 <= status_code < 300:
                response.close()
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.HTTP_ERROR, "http_error", status_code)

            content_type = headers.get("content-type", "")
            declared_media = content_type.split(";", 1)[0].strip().casefold()
            if headers.get("content-encoding", "identity").casefold() not in {"", "identity"}:
                response.close()
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_content_encoding", status_code)
            limit = self._response_limit(allowed, declared_media, target.url)
            content_length = self._content_length(headers.get("content-length"))
            if content_length is not None and content_length > limit:
                response.close()
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.TOO_LARGE, "content_length_limit", status_code)

            body = bytearray()
            timed_out = False
            too_large = False
            try:
                for chunk in response.chunks:
                    if self.monotonic_clock() >= deadline:
                        timed_out = True
                        break
                    if not isinstance(chunk, bytes):
                        raise TransportNetworkError("Transport returned a non-byte chunk.")
                    if len(body) + len(chunk) > limit:
                        too_large = True
                        break
                    body.extend(chunk)
            except TransportTimeoutError:
                timed_out = True
            except TransportTLSError:
                return self._raw_error(
                    url,
                    target.url,
                    fetched_at,
                    redirects,
                    FetchStatus.TLS_ERROR,
                    "tls_stream_error",
                    status_code,
                )
            except Exception:
                return self._raw_error(
                    url,
                    target.url,
                    fetched_at,
                    redirects,
                    FetchStatus.NETWORK_ERROR,
                    "response_stream_failed",
                    status_code,
                )
            finally:
                response.close()
            if self.monotonic_clock() >= deadline:
                timed_out = True
            if timed_out:
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.TIMEOUT, "total_timeout", status_code)
            if too_large:
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.TOO_LARGE, "stream_size_limit", status_code)

            content = bytes(body)
            detected, mismatch = self._detect_media(content, declared_media, target.url, allowed)
            if mismatch:
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.CONTENT_TYPE_MISMATCH, "content_type_mismatch", status_code)
            if detected is None:
                return self._raw_error(url, target.url, fetched_at, redirects, FetchStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type", status_code)
            actual_limit = {
                "application/json": self.policy.max_json_bytes,
                "application/pdf": self.policy.max_pdf_bytes,
                "text/html": self.policy.max_html_bytes,
                "text/plain": self.policy.max_html_bytes,
            }[detected]
            if len(content) > actual_limit:
                return self._raw_error(
                    url,
                    target.url,
                    fetched_at,
                    redirects,
                    FetchStatus.TOO_LARGE,
                    "detected_media_size_limit",
                    status_code,
                )
            safe_headers = tuple(
                sorted(
                    (key, value[:2_000])
                    for key, value in headers.items()
                    if key in _SAFE_RESPONSE_HEADERS
                )
            )
            return _RawFetch(
                status=FetchStatus.FETCHED,
                requested_url=url,
                final_url=target.url,
                fetched_at=fetched_at,
                http_status=status_code,
                media_type=detected,
                content=content,
                headers=safe_headers,
                redirects=tuple(redirects),
            )

    def _build_document(self, source_id: str, raw: _RawFetch) -> FetchedDocument:
        if raw.status != FetchStatus.FETCHED or raw.content is None:
            return FetchedDocument(
                source_id=source_id,
                requested_url=raw.requested_url,
                final_url=raw.final_url,
                status=raw.status,
                fetched_at=raw.fetched_at,
                http_status=raw.http_status,
                media_type=raw.media_type,
                response_headers=raw.headers,
                redirects=raw.redirects,
                warnings=raw.warnings,
                error_code=raw.error_code,
            )
        if raw.media_type in _HTML_MEDIA:
            content_type = dict(raw.headers).get("content-type", raw.media_type)
            parsed = extract_html(raw.content, content_type, self.policy)
            if (
                parsed.status != FetchStatus.ANTI_BOT
                and (
                    _is_pdf_url(raw.requested_url)
                    or _is_pdf_url(raw.final_url)
                )
            ):
                parsed = ParsedContent(
                    status=FetchStatus.CONTENT_TYPE_MISMATCH,
                    title=parsed.title,
                    warnings=(*parsed.warnings, "pdf_url_returned_html"),
                    error_code="pdf_url_returned_html",
                )
        elif raw.media_type == "text/plain":
            content_type = dict(raw.headers).get("content-type", raw.media_type)
            text, warnings = _decode_text(raw.content, content_type)
            truncated = len(text) > self.policy.max_text_chars
            parsed = ParsedContent(
                status=FetchStatus.PARTIAL if truncated else FetchStatus.FETCHED,
                text=text[: self.policy.max_text_chars],
                warnings=(*warnings, *(("text_truncated",) if truncated else ())),
            )
        else:
            parsed = self.pdf_parser(raw.content, self.policy)
        content_hash = hashlib.sha256(raw.content).hexdigest()
        text_hash = hashlib.sha256(parsed.text.encode("utf-8")).hexdigest() if parsed.text else None
        return FetchedDocument(
            source_id=source_id,
            requested_url=raw.requested_url,
            final_url=raw.final_url,
            status=parsed.status,
            fetched_at=raw.fetched_at,
            http_status=raw.http_status,
            media_type=raw.media_type,
            content=None if parsed.status == FetchStatus.ANTI_BOT else raw.content,
            text=parsed.text,
            title=parsed.title,
            page_text=parsed.page_text,
            page_count=parsed.page_count,
            parsed_pages=parsed.parsed_pages,
            byte_count=len(raw.content),
            content_sha256=content_hash,
            text_sha256=text_hash,
            response_headers=raw.headers,
            redirects=raw.redirects,
            warnings=(*raw.warnings, *parsed.warnings),
            error_code=parsed.error_code,
        )

    def _fetch_isap(
        self,
        original_url: str,
        source_id: str,
        reference: tuple[str, int, int, str],
        *,
        deadline: float,
    ) -> FetchedDocument:
        publisher, year, position, address = reference
        metadata_url = f"https://api.sejm.gov.pl/eli/acts/{publisher}/{year}/{position}"
        metadata_raw = self._download(
            metadata_url,
            allowed="json",
            deadline=deadline,
        )
        if metadata_raw.status != FetchStatus.FETCHED or metadata_raw.content is None:
            return FetchedDocument(
                source_id=source_id,
                requested_url=original_url,
                final_url=metadata_raw.final_url,
                status=metadata_raw.status,
                fetched_at=metadata_raw.fetched_at,
                http_status=metadata_raw.http_status,
                media_type=metadata_raw.media_type,
                response_headers=metadata_raw.headers,
                redirects=metadata_raw.redirects,
                resolved_via="eli_api",
                warnings=(
                    f"metadata_fetch_status:{metadata_raw.status.value}",
                    *metadata_raw.warnings,
                ),
                error_code=metadata_raw.error_code or "eli_metadata_fetch_failed",
            )
        try:
            metadata = json.loads(metadata_raw.content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._official_failure(original_url, source_id, metadata_raw, "eli_metadata_invalid_json")
        if not isinstance(metadata, dict):
            return self._official_failure(original_url, source_id, metadata_raw, "eli_metadata_not_object")
        returned_address = str(metadata.get("address", "")).upper()
        if (
            returned_address != address
            or str(metadata.get("publisher", "")).upper() != publisher
            or metadata.get("year") != year
            or metadata.get("pos") != position
        ):
            return self._official_failure(original_url, source_id, metadata_raw, "eli_metadata_identity_mismatch")
        if metadata.get("textHTML") is True:
            text_url = f"{metadata_url}/text.html"
        elif metadata.get("textPDF") is True:
            text_url = f"{metadata_url}/text.pdf"
        else:
            return self._official_failure(original_url, source_id, metadata_raw, "eli_text_unavailable")
        document = self._build_document(
            source_id,
            self._download(text_url, allowed="document", deadline=deadline),
        )
        selected_keys = (
            "ELI", "address", "publisher", "year", "pos", "title", "type",
            "status", "inForce", "legalStatusDate", "entryIntoForce", "validFrom",
            "repealDate", "expirationDate", "promulgation", "announcementDate",
        )
        safe_metadata = tuple(
            (key, value)
            for key in selected_keys
            if key in metadata
            and (
                isinstance((value := metadata.get(key)), (str, int, float, bool))
                or value is None
            )
        )
        return replace(
            document,
            requested_url=original_url,
            resolved_via="eli_api",
            official_metadata=safe_metadata,
            warnings=("resolved_from_isap_via_eli_api", *document.warnings),
        )

    @staticmethod
    def _parse_isap_reference(value: str) -> tuple[str, int, int, str] | None:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 80, 443}
        ):
            return None
        if (parsed.hostname or "").casefold().rstrip(".") != "isap.sejm.gov.pl":
            return None
        if parsed.path.casefold() != "/isap.nsf/docdetails.xsp":
            return None
        values = parse_qs(parsed.query, keep_blank_values=True)
        if set(values) != {"id"}:
            return None
        identifiers = values.get("id")
        if not identifiers or len(identifiers) != 1:
            return None
        match = _ISAP_ID.fullmatch(identifiers[0])
        if not match:
            return None
        publisher, year_text, _volume, position_text = match.groups()
        position = int(position_text)
        if position < 1:
            return None
        return publisher.upper(), int(year_text), position, identifiers[0].upper()

    def _response_limit(self, allowed: str, declared_media: str, url: str) -> int:
        if allowed == "json":
            return self.policy.max_json_bytes
        if declared_media in _HTML_MEDIA | _PLAIN_MEDIA:
            return self.policy.max_html_bytes
        if declared_media in _PDF_MEDIA or urlsplit(url).path.casefold().endswith(".pdf"):
            return self.policy.max_pdf_bytes
        return max(self.policy.max_html_bytes, self.policy.max_pdf_bytes)

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _detect_media(content: bytes, declared: str, url: str, allowed: str) -> tuple[str | None, bool]:
        prefix = content[:4_096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
        is_pdf = b"%pdf-" in content[:1_024].lower()
        is_html = (
            prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
            or b"<html" in prefix
        )
        if allowed == "json":
            if declared and declared not in _JSON_MEDIA:
                return None, True
            try:
                json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, True
            return "application/json", False
        if is_pdf:
            return ("application/pdf", declared in _HTML_MEDIA | _PLAIN_MEDIA)
        if is_html or _bytes_contain_anti_bot_marker(content):
            mismatch = declared in _PDF_MEDIA and not _bytes_contain_anti_bot_marker(content)
            return "text/html", mismatch
        if declared in _PDF_MEDIA:
            return None, True
        if declared in _HTML_MEDIA:
            return "text/html", False
        if declared in _PLAIN_MEDIA:
            return "text/plain", False
        if declared == "application/octet-stream" and urlsplit(url).path.casefold().endswith(".pdf"):
            return None, True
        return None, False

    @staticmethod
    def _raw_error(
        requested_url: str,
        final_url: str | None,
        fetched_at: datetime,
        redirects: list[RedirectHop],
        status: FetchStatus,
        code: str,
        http_status: int | None = None,
    ) -> _RawFetch:
        return _RawFetch(
            status=status,
            requested_url=requested_url,
            final_url=final_url,
            fetched_at=fetched_at,
            http_status=http_status,
            redirects=tuple(redirects),
            error_code=code,
        )

    @staticmethod
    def _official_failure(
        original_url: str,
        source_id: str,
        metadata_raw: _RawFetch,
        code: str,
    ) -> FetchedDocument:
        return FetchedDocument(
            source_id=source_id,
            requested_url=original_url,
            final_url=metadata_raw.final_url,
            status=FetchStatus.OFFICIAL_RESOLUTION_FAILED,
            fetched_at=metadata_raw.fetched_at,
            http_status=metadata_raw.http_status,
            resolved_via="eli_api",
            error_code=code,
        )
