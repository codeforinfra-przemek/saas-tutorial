import json
from datetime import datetime, timezone
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from datacollector.documents import (
    DiskCachedDocumentFetcher,
    DocumentFetcher,
    FetchPolicy,
    FetchStatus,
    FetchedDocument,
    ParsedContent,
    ResilientDocumentFetcher,
    TransportNetworkError,
    TransportResponse,
    Urllib3PinnedTransport,
    URLPolicyError,
    ValidatedTarget,
    _apply_pdf_worker_limits,
    extract_pdf,
    validate_public_url,
)


class CountingFetcher:
    def __init__(self):
        self.calls = 0

    def fetch(self, url, *, source_id=""):
        self.calls += 1
        content = b"same content"
        return FetchedDocument(
            source_id=source_id,
            requested_url=url,
            final_url=url,
            status=FetchStatus.FETCHED,
            fetched_at=NOW,
            http_status=200,
            media_type="text/html",
            text="same content",
            byte_count=len(content),
            content_sha256="a" * 64,
            text_sha256="b" * 64,
        )


class DiskCachedDocumentFetcherTests(TestCase):
    def test_reuses_parsed_content_and_rebinds_source_id(self):
        base = CountingFetcher()
        with TemporaryDirectory() as directory:
            cached = DiskCachedDocumentFetcher(base, directory, clock=lambda: NOW)
            first = cached.fetch("https://example.com/", source_id="source-one")
            second = cached.fetch("https://example.com/", source_id="source-two")

        self.assertEqual(base.calls, 1)
        self.assertEqual(first.source_id, "source-one")
        self.assertEqual(second.source_id, "source-two")
        self.assertIn("document_cache_hit", second.warnings)

    def test_resilient_fetcher_recovers_from_www_access_denied(self):
        base = MagicMock()

        def fetch(url, *, source_id=""):
            if url.startswith("https://example.com/"):
                return FetchedDocument(
                    source_id=source_id,
                    requested_url=url,
                    final_url=url,
                    status=FetchStatus.FETCHED,
                    fetched_at=NOW,
                    media_type="text/html",
                    text="Public franchise offer",
                )
            return FetchedDocument(
                source_id=source_id,
                requested_url=url,
                final_url=url,
                status=FetchStatus.ACCESS_DENIED,
                fetched_at=NOW,
                error_code="access_denied",
            )

        base.fetch.side_effect = fetch
        resilient = ResilientDocumentFetcher(base)

        document = resilient.fetch(
            "https://www.example.com/franchise/",
            source_id="source-test",
        )

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(
            document.requested_url,
            "https://www.example.com/franchise/",
        )
        self.assertEqual(document.resolved_via, "alternate_public_url")
        self.assertIn("alternate_public_url_recovery", document.warnings)


PUBLIC_IP = "93.184.216.34"
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeResolver:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def resolve(self, hostname, port):
        self.calls.append((hostname, port))
        if hostname not in self.values:
            raise TransportNetworkError("No fixture DNS result.")
        return tuple(self.values[hostname])


class FakeTransport:
    def __init__(self, responses):
        self.responses = {
            url: list(items) if isinstance(items, list) else [items]
            for url, items in responses.items()
        }
        self.calls = []
        self.closed = 0

    def request(self, target, *, headers, connect_timeout, read_timeout):
        self.calls.append(
            {
                "url": target.url,
                "hostname": target.hostname,
                "addresses": target.addresses,
                "headers": dict(headers),
                "connect_timeout": connect_timeout,
                "read_timeout": read_timeout,
            }
        )
        response = self.responses[target.url].pop(0)
        return TransportResponse(
            status_code=response.status_code,
            headers=response.headers,
            chunks=response.chunks,
            close_callback=self._close,
        )

    def _close(self):
        self.closed += 1


def response(status=200, headers=None, chunks=()):
    return TransportResponse(
        status_code=status,
        headers=headers or {},
        chunks=chunks,
    )


def fetcher(resolver, transport, **kwargs):
    return DocumentFetcher(
        resolver=resolver,
        transport=transport,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
        **kwargs,
    )


class PublicURLPolicyTests(TestCase):
    def test_pins_all_public_dns_results(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"]})

        target = validate_public_url("HTTPS://Example.com/path#fragment", resolver)

        self.assertEqual(target.url, "https://example.com/path")
        self.assertEqual(target.hostname, "example.com")
        self.assertEqual(target.port, 443)
        self.assertEqual(target.addresses[0], PUBLIC_IP)
        self.assertEqual(resolver.calls, [("example.com", 443)])

    def test_rejects_dns_set_containing_private_address(self):
        resolver = FakeResolver({"mixed.example": [PUBLIC_IP, "10.0.0.8"]})

        with self.assertRaises(URLPolicyError) as raised:
            validate_public_url("https://mixed.example/", resolver)

        self.assertTrue(raised.exception.ssrf)
        self.assertEqual(raised.exception.code, "non_public_address")

    def test_rejects_ambiguous_and_direct_ip_hosts(self):
        resolver = FakeResolver({})
        for url in (
            "http://127.0.0.1/",
            "http://2130706433/",
            "http://127.1/",
            "http://[::1]/",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(URLPolicyError):
                validate_public_url(url, resolver)


class ProductionTransportTests(TestCase):
    def test_https_connects_to_pinned_ip_but_uses_origin_for_host_and_sni(self):
        raw_response = MagicMock()
        raw_response.status = 200
        raw_response.headers = {"Content-Type": "text/plain"}
        raw_response.read.side_effect = [b"body", b""]
        pool = MagicMock()
        pool.urlopen.return_value = raw_response
        target = ValidatedTarget(
            url="https://example.com/report?q=1",
            scheme="https",
            hostname="example.com",
            port=443,
            addresses=(PUBLIC_IP,),
        )

        with patch("urllib3.HTTPSConnectionPool", return_value=pool) as pool_class:
            result = Urllib3PinnedTransport().request(
                target,
                headers={"User-Agent": "test"},
                connect_timeout=1,
                read_timeout=2,
            )
            self.assertEqual(b"".join(result.chunks), b"body")
            result.close()

        _, kwargs = pool_class.call_args
        self.assertEqual(pool_class.call_args.args[0], PUBLIC_IP)
        self.assertEqual(kwargs["server_hostname"], "example.com")
        self.assertEqual(kwargs["assert_hostname"], "example.com")
        request_kwargs = pool.urlopen.call_args.kwargs
        self.assertEqual(request_kwargs["headers"]["Host"], "example.com")
        self.assertFalse(request_kwargs["redirect"])


class DocumentFetcherTests(TestCase):
    def test_transport_receives_pinned_address_and_no_proxy_configuration(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/": response(
                    headers={"Content-Type": "text/html"},
                    chunks=(b"<html><body>Public</body></html>",),
                )
            }
        )

        document = fetcher(resolver, transport).fetch("https://example.com/")

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(transport.calls[0]["addresses"], (PUBLIC_IP,))
        self.assertNotIn("Proxy", transport.calls[0]["headers"])
        self.assertEqual(resolver.calls, [("example.com", 443)])

    def test_blocks_redirect_to_cloud_metadata_before_second_request(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/start": response(
                    status=302,
                    headers={"Location": "http://169.254.169.254/latest/meta-data/"},
                )
            }
        )

        document = fetcher(resolver, transport).fetch("https://example.com/start")

        self.assertEqual(document.status, FetchStatus.REDIRECT_BLOCKED)
        self.assertEqual(document.error_code, "redirect_ip_literal")
        self.assertEqual(len(transport.calls), 1)

    def test_follows_relative_redirect_and_records_it(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/start": response(
                    status=301,
                    headers={"Location": "/final"},
                ),
                "https://example.com/final": response(
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                    chunks=("gotowe".encode(),),
                ),
            }
        )

        document = fetcher(resolver, transport).fetch("https://example.com/start")

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(document.final_url, "https://example.com/final")
        self.assertEqual(document.text, "gotowe")
        self.assertEqual(len(document.redirects), 1)
        self.assertEqual(resolver.calls, [("example.com", 443), ("example.com", 443)])

    def test_total_deadline_includes_initial_dns_resolution(self):
        monotonic_clock = MutableClock()

        class SlowResolver(FakeResolver):
            def resolve(self, hostname, port):
                result = super().resolve(hostname, port)
                monotonic_clock.advance(3)
                return result

        resolver = SlowResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport({})
        document_fetcher = DocumentFetcher(
            resolver=resolver,
            transport=transport,
            policy=FetchPolicy(total_timeout_seconds=2),
            clock=lambda: NOW,
            monotonic_clock=monotonic_clock,
        )

        document = document_fetcher.fetch("https://example.com/")

        self.assertEqual(document.status, FetchStatus.TIMEOUT)
        self.assertEqual(document.error_code, "total_timeout")
        self.assertEqual(transport.calls, [])

    def test_deadline_is_shared_with_redirect_dns_resolution(self):
        monotonic_clock = MutableClock()

        class SlowResolver(FakeResolver):
            def resolve(self, hostname, port):
                result = super().resolve(hostname, port)
                monotonic_clock.advance(3)
                return result

        resolver = SlowResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/start": response(
                    status=302,
                    headers={"Location": "/final"},
                ),
            }
        )
        document_fetcher = DocumentFetcher(
            resolver=resolver,
            transport=transport,
            policy=FetchPolicy(total_timeout_seconds=5),
            clock=lambda: NOW,
            monotonic_clock=monotonic_clock,
        )

        document = document_fetcher.fetch("https://example.com/start")

        self.assertEqual(document.status, FetchStatus.TIMEOUT)
        self.assertEqual(document.error_code, "total_timeout")
        self.assertEqual(len(transport.calls), 1)

    def test_transport_timeouts_are_clamped_to_remaining_deadline(self):
        monotonic_clock = MutableClock()

        class SlowResolver(FakeResolver):
            def resolve(self, hostname, port):
                result = super().resolve(hostname, port)
                monotonic_clock.advance(4)
                return result

        resolver = SlowResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/": response(
                    headers={"Content-Type": "text/plain"},
                    chunks=(b"body",),
                )
            }
        )
        document_fetcher = DocumentFetcher(
            resolver=resolver,
            transport=transport,
            policy=FetchPolicy(
                connect_timeout_seconds=5,
                read_timeout_seconds=15,
                total_timeout_seconds=5,
            ),
            clock=lambda: NOW,
            monotonic_clock=monotonic_clock,
        )

        document = document_fetcher.fetch("https://example.com/")

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(transport.calls[0]["connect_timeout"], 1)
        self.assertEqual(transport.calls[0]["read_timeout"], 1)

    def test_rejects_oversized_content_length_without_reading_body(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        chunks_read = []

        def chunks():
            chunks_read.append(True)
            yield b"not read"

        transport = FakeTransport(
            {
                "https://example.com/page": response(
                    headers={"Content-Type": "text/html", "Content-Length": "11"},
                    chunks=chunks(),
                )
            }
        )
        policy = FetchPolicy(max_html_bytes=10)

        document = fetcher(resolver, transport, policy=policy).fetch("https://example.com/page")

        self.assertEqual(document.status, FetchStatus.TOO_LARGE)
        self.assertEqual(document.error_code, "content_length_limit")
        self.assertEqual(chunks_read, [])

    def test_enforces_stream_limit_when_content_length_is_missing(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/page": response(
                    headers={"Content-Type": "text/html"},
                    chunks=(b"123456", b"789012"),
                )
            }
        )
        policy = FetchPolicy(max_html_bytes=10)

        document = fetcher(resolver, transport, policy=policy).fetch("https://example.com/page")

        self.assertEqual(document.status, FetchStatus.TOO_LARGE)
        self.assertEqual(document.error_code, "stream_size_limit")
        self.assertIsNone(document.content)

    def test_enforces_html_limit_after_sniffing_unknown_content_type(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/download": response(
                    chunks=(b"<html><body>1234567890</body></html>",),
                )
            }
        )
        policy = FetchPolicy(max_html_bytes=10, max_pdf_bytes=100)

        document = fetcher(resolver, transport, policy=policy).fetch(
            "https://example.com/download"
        )

        self.assertEqual(document.status, FetchStatus.TOO_LARGE)
        self.assertEqual(document.error_code, "detected_media_size_limit")

    def test_extracts_visible_html_and_preserves_polish_text(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        html = """<!doctype html><html><head><title> Żabka — franczyza </title>
        <style>secret-style</style><script>secret-script</script></head>
        <body><h1>Oferta</h1><p>Wpłata własna: 5 000 zł.</p>
        <ul><li>Szkolenie</li><li>Wsparcie</li></ul></body></html>""".encode()
        transport = FakeTransport(
            {
                "https://example.com/franchise": response(
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    chunks=(html,),
                )
            }
        )

        document = fetcher(resolver, transport).fetch(
            "https://example.com/franchise", source_id="source-test"
        )

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(document.source_id, "source-test")
        self.assertEqual(document.title, "Żabka — franczyza")
        self.assertIn("Wpłata własna: 5 000 zł.", document.text)
        self.assertIn("Szkolenie", document.text)
        self.assertIn("Wsparcie", document.text)
        self.assertLess(document.text.index("Szkolenie"), document.text.index("Wsparcie"))
        self.assertNotIn("secret-script", document.text)
        self.assertNotIn("secret-style", document.text)
        self.assertIsNotNone(document.content_sha256)
        self.assertIsNotNone(document.text_sha256)

    def test_extracts_json_ld_and_next_data_without_executing_javascript(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        html = b"""<!doctype html><html><head>
        <script type="application/ld+json">
        {"@type":"Organization","name":"Marka Testowa",
         "description":"Franczyza mobilna bez lokalu.",
         "telephone":"+48 123 456 789"}
        </script>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"content":{"title":"Oferta franczyzowa",
         "text":"Szkolenie startowe i wsparcie operacyjne."}}},
         "buildId":"secret-build-id"}
        </script>
        <script>window.secret = "do-not-extract";</script>
        </head><body><main id="app"></main></body></html>"""
        transport = FakeTransport(
            {
                "https://example.com/franchise": response(
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    chunks=(html,),
                )
            }
        )

        document = fetcher(resolver, transport).fetch(
            "https://example.com/franchise"
        )

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertIn("Marka Testowa", document.text)
        self.assertIn("Franczyza mobilna bez lokalu.", document.text)
        self.assertIn("Szkolenie startowe", document.text)
        self.assertNotIn("secret-build-id", document.text)
        self.assertNotIn("do-not-extract", document.text)
        self.assertIn("embedded_structured_data_extracted", document.warnings)

    def test_classifies_short_incapsula_page_as_anti_bot(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/report.pdf": response(
                    headers={"Content-Type": "text/html"},
                    chunks=(b"<html><title>Human Verification</title><body>Incapsula incident ID 123</body></html>",),
                )
            }
        )

        document = fetcher(resolver, transport).fetch("https://example.com/report.pdf")

        self.assertEqual(document.status, FetchStatus.ANTI_BOT)
        self.assertEqual(document.error_code, "anti_bot_page")
        self.assertIsNone(document.content)
        self.assertIsNotNone(document.content_sha256)

    def test_rejects_ordinary_html_returned_by_pdf_url(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/report.pdf": response(
                    headers={"Content-Type": "text/html"},
                    chunks=(
                        b"<html><title>Download</title><body>Sign in to continue.</body></html>",
                    ),
                )
            }
        )

        document = fetcher(resolver, transport).fetch(
            "https://example.com/report.pdf"
        )

        self.assertEqual(document.status, FetchStatus.CONTENT_TYPE_MISMATCH)
        self.assertEqual(document.error_code, "pdf_url_returned_html")
        self.assertEqual(document.text, "")

    def test_preserves_access_denied_challenge_even_when_declared_as_pdf(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/report.pdf": response(
                    headers={"Content-Type": "application/pdf"},
                    chunks=(
                        b"<html><title>Access Denied</title><body>Security verification required.</body></html>",
                    ),
                )
            }
        )

        document = fetcher(resolver, transport).fetch(
            "https://example.com/report.pdf"
        )

        self.assertEqual(document.status, FetchStatus.ANTI_BOT)
        self.assertEqual(document.error_code, "anti_bot_page")
        self.assertIsNone(document.content)

    def test_uses_injected_pdf_parser(self):
        resolver = FakeResolver({"example.com": [PUBLIC_IP]})
        transport = FakeTransport(
            {
                "https://example.com/report.pdf": response(
                    headers={"Content-Type": "application/pdf"},
                    chunks=(b"%PDF-1.7 fixture",),
                )
            }
        )

        def parse_pdf(content, policy):
            self.assertTrue(content.startswith(b"%PDF-"))
            return ParsedContent(
                status=FetchStatus.FETCHED,
                text="Page one",
                page_text=("Page one",),
            )

        document = fetcher(resolver, transport, pdf_parser=parse_pdf).fetch(
            "https://example.com/report.pdf"
        )

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(document.media_type, "application/pdf")
        self.assertEqual(document.page_text, ("Page one",))

    def test_default_pdf_parser_runs_in_resource_limited_worker(self):
        try:
            import resource  # noqa: F401
        except ImportError:
            self.skipTest("Unix resource limits are unavailable.")
        from pypdf import PdfWriter

        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)

        parsed = extract_pdf(stream.getvalue(), FetchPolicy())

        self.assertEqual(parsed.status, FetchStatus.OCR_REQUIRED)
        self.assertEqual(parsed.page_count, 1)
        self.assertEqual(parsed.parsed_pages, 1)

    def test_pdf_worker_applies_configured_address_space_cap(self):
        try:
            import resource
        except ImportError:
            self.skipTest("Unix resource limits are unavailable.")

        policy = FetchPolicy(pdf_worker_memory_bytes=256 * 1024 * 1024)
        with (
            patch(
                "resource.getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            patch("resource.setrlimit") as set_limit,
        ):
            applied = _apply_pdf_worker_limits(policy)

        self.assertTrue(applied)
        set_limit.assert_any_call(
            resource.RLIMIT_AS,
            (policy.pdf_worker_memory_bytes,) * 2,
        )

    def test_pdf_worker_wall_clock_timeout_fails_closed(self):
        parsed = extract_pdf(
            b"%PDF-1.7 deliberately incomplete",
            FetchPolicy(pdf_parse_timeout_seconds=0.000001),
        )

        self.assertEqual(parsed.status, FetchStatus.PARSE_FAILED)
        self.assertEqual(parsed.error_code, "pdf_parse_timeout")


class OfficialELIResolverTests(TestCase):
    def test_resolves_isap_identifier_through_verified_eli_metadata_and_html(self):
        resolver = FakeResolver(
            {
                "api.sejm.gov.pl": [PUBLIC_IP],
            }
        )
        metadata_url = "https://api.sejm.gov.pl/eli/acts/DU/2025/1714"
        text_url = f"{metadata_url}/text.html"
        metadata = {
            "address": "WDU20250001714",
            "publisher": "DU",
            "year": 2025,
            "pos": 1714,
            "title": "Kodeks cywilny",
            "status": "obowiązujący",
            "inForce": "IN_FORCE",
            "legalStatusDate": "2026-07-19",
            "textHTML": True,
            "textPDF": True,
        }
        transport = FakeTransport(
            {
                metadata_url: response(
                    headers={"Content-Type": "application/json"},
                    chunks=(json.dumps(metadata).encode(),),
                ),
                text_url: response(
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    chunks=("<html><title>Kodeks cywilny</title><body>Art. 1.</body></html>".encode(),),
                ),
            }
        )
        original = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20250001714"

        document = fetcher(resolver, transport).fetch(original, source_id="source-law")

        self.assertEqual(document.status, FetchStatus.FETCHED)
        self.assertEqual(document.requested_url, original)
        self.assertEqual(document.final_url, text_url)
        self.assertEqual(document.resolved_via, "eli_api")
        self.assertEqual(document.official_metadata_dict()["address"], "WDU20250001714")
        self.assertEqual(document.official_metadata_dict()["inForce"], "IN_FORCE")
        self.assertIn("Art. 1.", document.text)
        self.assertEqual([call["url"] for call in transport.calls], [metadata_url, text_url])

    def test_rejects_eli_metadata_that_does_not_match_isap_identity(self):
        resolver = FakeResolver({"api.sejm.gov.pl": [PUBLIC_IP]})
        metadata_url = "https://api.sejm.gov.pl/eli/acts/DU/2007/1206"
        metadata = {
            "address": "WDU20071719999",
            "publisher": "DU",
            "year": 2007,
            "pos": 9999,
            "textPDF": True,
        }
        transport = FakeTransport(
            {
                metadata_url: response(
                    headers={"Content-Type": "application/json"},
                    chunks=(json.dumps(metadata).encode(),),
                )
            }
        )
        original = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=wdu20071711206"

        document = fetcher(resolver, transport).fetch(original)

        self.assertEqual(document.status, FetchStatus.OFFICIAL_RESOLUTION_FAILED)
        self.assertEqual(document.error_code, "eli_metadata_identity_mismatch")
        self.assertEqual(len(transport.calls), 1)

    def test_preserves_eli_metadata_fetch_status(self):
        original = (
            "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?"
            "id=WDU20250001714"
        )
        metadata_url = "https://api.sejm.gov.pl/eli/acts/DU/2025/1714"
        cases = (
            (404, FetchStatus.NOT_FOUND, "not_found"),
            (429, FetchStatus.RATE_LIMITED, "rate_limited"),
            (403, FetchStatus.ACCESS_DENIED, "access_denied"),
        )
        for http_status, expected_status, expected_error in cases:
            with self.subTest(http_status=http_status):
                resolver = FakeResolver({"api.sejm.gov.pl": [PUBLIC_IP]})
                transport = FakeTransport(
                    {metadata_url: response(status=http_status)}
                )

                document = fetcher(resolver, transport).fetch(original)

                self.assertEqual(document.status, expected_status)
                self.assertEqual(document.error_code, expected_error)
                self.assertEqual(document.resolved_via, "eli_api")
                self.assertIn(
                    f"metadata_fetch_status:{expected_status.value}",
                    document.warnings,
                )
