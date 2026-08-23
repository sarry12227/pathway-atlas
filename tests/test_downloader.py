import ipaddress
import os
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from scripts import downloader
from scripts.downloader import (
    DownloadHttpError,
    DownloadMediaTypeError,
    DownloadRedirectError,
    DownloadSecurityError,
    DownloadStorageError,
    DownloadTimeout,
    DownloadTooLarge,
    download_public_file,
    validate_public_url,
)


class FakeHttpResponse:
    """Complete response double for the downloader's narrow transport seam."""

    def __init__(self, status, *, headers=None, body=b"", peer_ip="93.184.216.34"):
        self.status = status
        self.reason = "Synthetic response"
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.body = body
        self.peer_ip = peer_ip
        self.offset = 0
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, amount=-1):
        if amount < 0:
            amount = len(self.body) - self.offset
        result = self.body[self.offset : self.offset + amount]
        self.offset += len(result)
        return result

    def close(self):
        self.closed = True


class AbortableSocket:
    def __init__(self):
        self.aborted = threading.Event()

    def getpeername(self):
        return ("93.184.216.34", 443)

    def settimeout(self, timeout):
        self.timeout = timeout

    def shutdown(self, how):
        self.aborted.set()

    def close(self):
        self.aborted.set()


class SlowHeaderConnection:
    def __init__(self, hostname, port, address, timeout):
        self.sock = AbortableSocket()

    def connect(self):
        pass

    def request(self, method, path, headers):
        pass

    def getresponse(self):
        self.sock.aborted.wait(0.3)
        raise OSError("synthetic blocked response headers")

    def close(self):
        self.sock.close()


class SlowBodyResponse:
    status = 200
    reason = "Synthetic response"

    def __init__(self, peer_socket):
        self.peer_socket = peer_socket
        self.closed = False

    def getheader(self, name, default=None):
        if name.lower() == "content-type":
            return "text/csv"
        return default

    def read(self, amount=-1):
        if self.peer_socket.aborted.wait(0.3):
            raise OSError("synthetic interrupted body")
        return b"x"

    def close(self):
        self.closed = True


class SlowBodyConnection(SlowHeaderConnection):
    def getresponse(self):
        return SlowBodyResponse(self.sock)


class DownloaderUrlSecurityTest(unittest.TestCase):
    def test_blocks_non_http_urls_userinfo_and_missing_hosts(self):
        blocked = (
            "file:///etc/passwd",
            "ftp://public.example.test/file.csv",
            "https:///missing-host.csv",
            "https://user:secret@public.example.test/file.csv",
        )
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(DownloadSecurityError):
                validate_public_url(url)

    def test_blocks_non_public_ip_literals_for_ipv4_and_ipv6(self):
        blocked = (
            "http://0.0.0.0/x",
            "http://127.0.0.1/x",
            "http://10.0.0.1/x",
            "http://169.254.169.254/x",
            "http://192.0.2.1/x",
            "http://224.0.0.1/x",
            "http://[::]/x",
            "http://[::1]/x",
            "http://[fc00::1]/x",
            "http://[fe80::1]/x",
            "http://[2001:db8::1]/x",
            "http://[ff02::1]/x",
        )
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(DownloadSecurityError):
                validate_public_url(url)

    @patch("scripts.downloader.socket.getaddrinfo")
    def test_rejects_hostname_when_any_dns_result_is_not_public(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

        with self.assertRaises(DownloadSecurityError):
            validate_public_url("https://public.example.test/file.csv")

    @patch("scripts.downloader.socket.getaddrinfo")
    def test_accepts_hostname_only_when_all_dns_results_are_public(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1::", 443, 0, 0)),
        ]

        self.assertIsNone(validate_public_url("https://public.example.test/file.csv"))

    def test_blocks_special_ranges_when_legacy_ipaddress_reports_global(self):
        blocked = (
            "http://192.0.0.8/x",
            "http://[64:ff9b:1::1]/x",
            "http://[2002::1]/x",
            "http://[2001::1]/x",
            "http://[::ffff:192.0.0.8]/x",
        )
        patches = (
            patch.object(
                ipaddress.IPv4Address,
                "is_global",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                ipaddress.IPv4Address,
                "is_private",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                ipaddress.IPv4Address,
                "is_reserved",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                ipaddress.IPv6Address,
                "is_global",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                ipaddress.IPv6Address,
                "is_private",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                ipaddress.IPv6Address,
                "is_reserved",
                new_callable=PropertyMock,
                return_value=False,
            ),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            for url in blocked:
                with self.subTest(url=url), self.assertRaises(DownloadSecurityError):
                    validate_public_url(url)

    def test_allows_registry_exceptions_when_legacy_ipaddress_reports_private(self):
        allowed = (
            "http://192.0.0.9/x",
            "http://192.0.0.10/x",
            "http://[2001:1::1]/x",
            "http://[2001:1::2]/x",
            "http://[2001:3::1]/x",
            "http://[2001:4:112::1]/x",
            "http://[2001:20::1]/x",
            "http://[2001:30::1]/x",
        )
        patches = (
            patch.object(
                ipaddress.IPv4Address,
                "is_global",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                ipaddress.IPv4Address,
                "is_private",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                ipaddress.IPv4Address,
                "is_reserved",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                ipaddress.IPv6Address,
                "is_global",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(
                ipaddress.IPv6Address,
                "is_private",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch.object(
                ipaddress.IPv6Address,
                "is_reserved",
                new_callable=PropertyMock,
                return_value=True,
            ),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            for url in allowed:
                with self.subTest(url=url):
                    self.assertIsNone(validate_public_url(url))


class DownloaderTransportSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name).resolve()

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_revalidates_redirect_dns_before_following(self, open_request, getaddrinfo):
        first = FakeHttpResponse(
            302,
            headers={"Location": "https://redirected.example.test/file.csv"},
        )
        open_request.return_value = first
        getaddrinfo.side_effect = [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]

        with self.assertRaises(DownloadSecurityError):
            download_public_file("https://public.example.test/start", self.workspace)

        self.assertTrue(first.closed)

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_rejects_peer_outside_prevalidated_dns_results(
        self, open_request, getaddrinfo
    ):
        response = FakeHttpResponse(200, peer_ip="93.184.216.35")
        open_request.return_value = response
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

        with self.assertRaises(DownloadSecurityError):
            download_public_file("https://public.example.test/file.csv", self.workspace)

        self.assertTrue(response.closed)

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_stops_after_five_redirects(self, open_request, getaddrinfo):
        responses = [
            FakeHttpResponse(302, headers={"Location": f"/redirect-{index}"})
            for index in range(6)
        ]
        open_request.side_effect = responses
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

        with self.assertRaises(DownloadRedirectError):
            download_public_file("https://public.example.test/start", self.workspace)

        self.assertTrue(all(response.closed for response in responses))


class DownloaderPinnedConnectionTest(unittest.TestCase):
    @patch("scripts.downloader.socket.create_connection")
    @patch("scripts.downloader.ssl.create_default_context")
    def test_https_connects_to_validated_ip_with_original_hostname_sni(
        self, create_context, create_connection
    ):
        raw_socket = MagicMock()
        wrapped_socket = MagicMock()
        context = create_context.return_value
        context.wrap_socket.return_value = wrapped_socket
        create_connection.return_value = raw_socket
        connection = downloader._PinnedHTTPSConnection(
            "public.example.test", 443, "93.184.216.34", 12.5
        )

        connection.connect()

        create_connection.assert_called_once_with(
            ("93.184.216.34", 443), 12.5, None
        )
        context.wrap_socket.assert_called_once_with(
            raw_socket, server_hostname="public.example.test"
        )
        self.assertIs(connection.sock, wrapped_socket)
        connection.deadline_watchdog.cancel()

    @patch("scripts.downloader.socket.create_connection")
    @patch("scripts.downloader.ssl.create_default_context")
    def test_tls_failure_closes_pinned_raw_socket(
        self, create_context, create_connection
    ):
        raw_socket = MagicMock()
        create_connection.return_value = raw_socket
        create_context.return_value.wrap_socket.side_effect = ssl.SSLError(
            "synthetic TLS failure"
        )
        connection = downloader._PinnedHTTPSConnection(
            "public.example.test", 443, "93.184.216.34", 12.5
        )

        with self.assertRaises(ssl.SSLError):
            connection.connect()

        raw_socket.close.assert_called_once_with()


class DownloaderHardDeadlineTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name).resolve()
        self.public_dns = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("scripts.downloader._open_pinned_request")
    @patch("scripts.downloader.socket.getaddrinfo")
    def test_slow_redirect_dns_is_cut_off_by_wall_clock_deadline(
        self, getaddrinfo, open_request
    ):
        release_dns = threading.Event()

        def dns(hostname, *args, **kwargs):
            if hostname == "redirected.example.test":
                release_dns.wait(0.3)
            return self.public_dns

        getaddrinfo.side_effect = dns
        open_request.return_value = FakeHttpResponse(
            302, headers={"Location": "https://redirected.example.test/final.csv"}
        )

        started = time.monotonic()
        try:
            with self.assertRaises(DownloadTimeout):
                download_public_file(
                    "https://public.example.test/start",
                    self.workspace,
                    timeout=0.03,
                )
        finally:
            elapsed = time.monotonic() - started
            release_dns.set()

        self.assertLess(elapsed, 0.18)
        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader._PinnedHTTPSConnection", SlowHeaderConnection)
    @patch("scripts.downloader.socket.getaddrinfo")
    def test_slow_response_headers_are_actively_interrupted(self, getaddrinfo):
        getaddrinfo.return_value = self.public_dns

        started = time.monotonic()
        with self.assertRaises(DownloadTimeout):
            download_public_file(
                "https://public.example.test/data.csv",
                self.workspace,
                timeout=0.03,
            )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.18)
        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader._PinnedHTTPSConnection", SlowBodyConnection)
    @patch("scripts.downloader.socket.getaddrinfo")
    def test_slow_body_read_is_actively_interrupted(self, getaddrinfo):
        getaddrinfo.return_value = self.public_dns

        started = time.monotonic()
        with self.assertRaises(DownloadTimeout):
            download_public_file(
                "https://public.example.test/data.csv",
                self.workspace,
                timeout=0.03,
            )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.18)
        self.assertEqual(list(self.workspace.iterdir()), [])


class DownloaderResponseTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name).resolve()
        self.public_dns = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_success_uses_internal_name_and_media_type_extension(
        self, open_request, getaddrinfo
    ):
        response = FakeHttpResponse(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": "7",
                "Content-Disposition": 'attachment; filename="../../evil.exe"',
            },
            body=b"PDFDATA",
        )
        open_request.return_value = response
        getaddrinfo.return_value = self.public_dns

        result = download_public_file(
            "https://public.example.test/untrusted.exe", self.workspace
        )

        self.assertEqual(result.path.parent, self.workspace)
        self.assertEqual(result.path.suffix, ".pdf")
        self.assertNotIn("untrusted", result.path.name)
        self.assertNotIn("evil", result.path.name)
        self.assertEqual(result.path.read_bytes(), b"PDFDATA")
        self.assertEqual(result.media_type, "application/pdf")
        self.assertEqual(result.size_bytes, 7)
        self.assertTrue(response.closed)

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_all_supported_media_types_are_downloadable(self, open_request, getaddrinfo):
        media_types = {
            "text/csv": ".csv",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/pdf": ".pdf",
            "text/html; charset=utf-8": ".html",
            "application/json": ".json",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/svg+xml": ".svg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/tiff": ".tiff",
            "image/bmp": ".bmp",
        }
        getaddrinfo.return_value = self.public_dns
        open_request.side_effect = [
            FakeHttpResponse(200, headers={"Content-Type": media_type}, body=b"x")
            for media_type in media_types
        ]

        for media_type, extension in media_types.items():
            with self.subTest(media_type=media_type):
                result = download_public_file(
                    "https://public.example.test/resource", self.workspace
                )
                self.assertEqual(result.path.suffix, extension)
                self.assertEqual(result.path.read_bytes(), b"x")

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_rejects_missing_or_disallowed_media_type(self, open_request, getaddrinfo):
        getaddrinfo.return_value = self.public_dns
        responses = [
            FakeHttpResponse(200),
            FakeHttpResponse(200, headers={"Content-Type": "application/zip"}),
        ]
        open_request.side_effect = responses

        for _ in responses:
            with self.assertRaises(DownloadMediaTypeError):
                download_public_file(
                    "https://public.example.test/resource", self.workspace
                )

        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertTrue(all(response.closed for response in responses))

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_content_length_preflight_rejects_oversized_response(
        self, open_request, getaddrinfo
    ):
        response = FakeHttpResponse(
            200,
            headers={"Content-Type": "text/csv", "Content-Length": "9"},
            body=b"123456789",
        )
        open_request.return_value = response
        getaddrinfo.return_value = self.public_dns

        with self.assertRaises(DownloadTooLarge):
            download_public_file(
                "https://public.example.test/data.csv", self.workspace, max_bytes=8
            )

        self.assertEqual(response.offset, 0)
        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_stream_limit_removes_partial_file(self, open_request, getaddrinfo):
        response = FakeHttpResponse(
            200,
            headers={"Content-Type": "text/csv"},
            body=b"123456789",
        )
        open_request.return_value = response
        getaddrinfo.return_value = self.public_dns

        with self.assertRaises(DownloadTooLarge):
            download_public_file(
                "https://public.example.test/data.csv", self.workspace, max_bytes=8
            )

        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_relative_redirect_downloads_final_response(self, open_request, getaddrinfo):
        first = FakeHttpResponse(302, headers={"Location": "/final"})
        final = FakeHttpResponse(
            200,
            headers={"Content-Type": "application/json"},
            body=b"{}",
        )
        open_request.side_effect = [first, final]
        getaddrinfo.return_value = self.public_dns

        result = download_public_file(
            "https://public.example.test/start", self.workspace
        )

        self.assertEqual(result.source_url, "https://public.example.test/final")
        self.assertEqual(result.path.read_bytes(), b"{}")
        self.assertTrue(first.closed)
        self.assertTrue(final.closed)

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_rejects_http_error_without_writing(self, open_request, getaddrinfo):
        response = FakeHttpResponse(404, headers={"Content-Type": "text/html"})
        open_request.return_value = response
        getaddrinfo.return_value = self.public_dns

        with self.assertRaises(DownloadHttpError):
            download_public_file("https://public.example.test/missing", self.workspace)

        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_requires_caller_to_pass_an_existing_resolved_directory(
        self, open_request, getaddrinfo
    ):
        getaddrinfo.return_value = self.public_dns
        open_request.return_value = FakeHttpResponse(
            200, headers={"Content-Type": "text/csv"}, body=b"x"
        )
        relative_workspace = Path(os.path.relpath(self.workspace, Path.cwd()))

        with self.assertRaises(DownloadSecurityError):
            download_public_file(
                "https://public.example.test/data.csv", relative_workspace
            )

        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader.os.replace")
    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_atomic_publish_failure_removes_temporary_file(
        self, open_request, getaddrinfo, replace
    ):
        open_request.return_value = FakeHttpResponse(
            200, headers={"Content-Type": "text/csv"}, body=b"x"
        )
        getaddrinfo.return_value = self.public_dns
        replace.side_effect = OSError("synthetic rename failure")

        with self.assertRaises(DownloadStorageError):
            download_public_file("https://public.example.test/data.csv", self.workspace)

        self.assertEqual(list(self.workspace.iterdir()), [])

    @patch("scripts.downloader.time.monotonic")
    @patch("scripts.downloader.socket.getaddrinfo")
    @patch("scripts.downloader._open_pinned_request", create=True)
    def test_total_timeout_removes_partial_file(
        self, open_request, getaddrinfo, monotonic
    ):
        response = FakeHttpResponse(
            200, headers={"Content-Type": "text/csv"}, body=b"not-read"
        )
        open_request.return_value = response
        getaddrinfo.return_value = self.public_dns
        monotonic.side_effect = [
            100.0,
            100.1,
            100.2,
            100.3,
            100.4,
            100.5,
            100.6,
            101.0,
        ]

        with self.assertRaises(DownloadTimeout):
            download_public_file(
                "https://public.example.test/data.csv", self.workspace, timeout=1
            )

        self.assertEqual(response.offset, 0)
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
