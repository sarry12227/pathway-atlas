"""Offline contract tests for the optional official-root health smoke."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import ast
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.downloader import (
    DownloadHttpError,
    DownloadMediaTypeError,
    DownloadNetworkError,
    DownloadRedirectError,
    DownloadResult,
    DownloadSecurityError,
    DownloadStorageError,
    DownloadTimeout,
    DownloadTooLarge,
)
from scripts.live_smoke import (
    MAX_RESPONSE_BYTES,
    TOTAL_TIMEOUT_SECONDS,
    LiveSmokeResult,
    check_official_root,
    main,
)


class LiveSmokeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = lambda: datetime(2026, 8, 24, tzinfo=timezone.utc)
        self.roots = ("https://www.hljea.org.cn/",)

    def _result(self, workspace: Path, *chain: str) -> DownloadResult:
        path = workspace / "response.html"
        path.write_bytes(b"<html></html>")
        return DownloadResult(
            path=path,
            source_url=chain[-1],
            media_type="text/html",
            size_bytes=13,
            redirect_chain=chain,
        )

    def test_same_site_response_is_healthy_and_uses_bounded_downloader(self) -> None:
        downloaded: DownloadResult | None = None
        calls: list[tuple[str, int, float]] = []

        def downloader(url: str, workspace: Path, *, max_bytes: int, timeout: float) -> DownloadResult:
            nonlocal downloaded
            calls.append((url, max_bytes, timeout))
            self.assertTrue(workspace.is_dir())
            downloaded = self._result(
                workspace,
                "https://www.hljea.org.cn/?student=secret@example.test",
                "https://hljea.org.cn/index.html",
            )
            return downloaded

        result = check_official_root(
            "黑龙江", self.roots, downloader=downloader, clock=self.clock
        )

        self.assertEqual("healthy", result.status)
        self.assertEqual("www.hljea.org.cn", result.requested_domain)
        self.assertEqual("hljea.org.cn", result.final_domain)
        self.assertEqual(("www.hljea.org.cn", "hljea.org.cn"), result.redirect_domains)
        self.assertEqual("2026-08-24T00:00:00Z", result.checked_at)
        self.assertEqual(("https://www.hljea.org.cn/", MAX_RESPONSE_BYTES, TOTAL_TIMEOUT_SECONDS), calls[0])
        self.assertEqual(1, len(calls))
        self.assertIsNotNone(downloaded)
        self.assertFalse(downloaded.path.exists())
        self.assertEqual(
            ["checked_at", "content_type", "final_domain", "province", "reason_code", "redirect_domains", "requested_domain", "schema_version", "size_bytes", "status"],
            list(result.to_dict()),
        )

    def test_unlisted_redirect_is_review_but_timeout_is_data(self) -> None:
        review = check_official_root(
            "黑龙江", self.roots,
            downloader=lambda _url, workspace, **kwargs: self._result(
                workspace, "https://www.hljea.org.cn/", "https://publisher.example.test/a"
            ),
            clock=self.clock,
        )
        unavailable = check_official_root(
            "黑龙江", self.roots,
            downloader=lambda *args, **kwargs: (_ for _ in ()).throw(DownloadTimeout("token=secret")),
            clock=self.clock,
        )

        self.assertEqual(("redirect_review", "unlisted_redirect_domain"), (review.status, review.reason_code))
        self.assertEqual(("unavailable", "timeout"), (unavailable.status, unavailable.reason_code))
        self.assertEqual(("www.hljea.org.cn",), unavailable.redirect_domains)
        self.assertNotIn("secret", repr(unavailable))

    def test_controlled_downloader_errors_have_finite_private_reason_codes(self) -> None:
        cases = [
            (DownloadTimeout("query=secret"), "timeout"),
            (DownloadNetworkError("query=secret"), "dns_or_network"),
            (DownloadHttpError("query=secret"), "http_error"),
            (DownloadMediaTypeError("query=secret"), "unsupported_content_type"),
            (DownloadTooLarge("query=secret"), "response_too_large"),
            (DownloadSecurityError("query=secret"), "security_rejection"),
            (DownloadStorageError("query=secret"), "storage_error"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                result = check_official_root(
                    "黑龙江", self.roots,
                    downloader=lambda *args, error=error, **kwargs: (_ for _ in ()).throw(error),
                    clock=self.clock,
                )
                self.assertEqual(("unavailable", expected), (result.status, result.reason_code))
                self.assertNotIn("secret", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_direct_result_invariants_and_cli_unknown_province_are_controlled(self) -> None:
        with self.assertRaises(ValueError):
            LiveSmokeResult(
                province="黑龙江", status="healthy", requested_domain="a.test",
                final_domain=None, redirect_domains=("a.test",), checked_at="2026-08-24T00:00:00Z",
                content_type=None, size_bytes=None, reason_code=None,
            )
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["--province", "unknown@example.test?secret=abc"])
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("live-smoke: invalid input\n", stderr.getvalue())

    def test_forged_metadata_and_unsafe_chain_fail_closed_without_leaking_query(self) -> None:
        def forged(_url: str, workspace: Path, **_kwargs: object) -> DownloadResult:
            result = self._result(workspace, "https://www.hljea.org.cn/?token=secret", "https://localhost.invalid/next?token=secret")
            object.__setattr__(result, "media_type", "application/x-forged")
            object.__setattr__(result, "size_bytes", 1_048_577)
            return result

        result = check_official_root("黑龙江", self.roots, downloader=forged, clock=self.clock)
        self.assertEqual(("unavailable", "security_rejection"), (result.status, result.reason_code))
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("localhost", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_result_constructor_and_replace_enforce_the_complete_public_contract(self) -> None:
        healthy = LiveSmokeResult(
            province="黑龙江", status="healthy", requested_domain="www.hljea.org.cn",
            final_domain="hljea.org.cn", redirect_domains=("www.hljea.org.cn", "hljea.org.cn"),
            checked_at="2026-08-24T00:00:00Z", content_type="text/html", size_bytes=0,
            reason_code=None,
        )
        for changes in (
            {"checked_at": "2026-08-24 00:00:00"},
            {"province": "https://secret.example.test/a?token=secret"},
            {"redirect_domains": ("www.hljea.org.cn", "hljea.org.cn", "hljea.org.cn")},
            {"final_domain": "other.example.test"},
            {"size_bytes": True},
            {"content_type": "application/x-forged"},
            {"reason_code": "timeout"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(healthy, **changes)

    def test_strict_catalog_rejects_duplicate_alias_and_unsafe_root_without_echoing_input(self) -> None:
        valid = {
            "schema_version": "1.0", "verified_at": "2026-08-24", "coverage_note": "synthetic",
            "mode_authority_urls": ["https://authority.example.test/source"],
            "provinces": [
                {"province": "甲", "aliases": ["甲", "Ａ"], "mode": "3+3", "authority_name": "甲院",
                 "official_roots": ["https://official.example.test/"], "mode_source_url": "https://authority.example.test/source",
                 "verified_at": "2026-08-24", "notes": "synthetic"},
                {"province": "乙", "aliases": ["A"], "mode": "3+1+2", "authority_name": "乙院",
                 "official_roots": ["https://127.0.0.1/"], "mode_source_url": "https://authority.example.test/source",
                 "verified_at": "2026-08-24", "notes": "synthetic"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Path(temporary) / "catalog.json"
            catalog.write_text(json.dumps(valid, ensure_ascii=False), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("scripts.live_smoke._CATALOG_PATH", catalog, create=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(["--province", "token=secret"])
            self.assertEqual(2, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("live-smoke: invalid input\n", stderr.getvalue())
            self.assertNotIn("secret", stderr.getvalue())

    def test_redirect_error_and_duplicate_province_are_controlled_data_or_input_errors(self) -> None:
        result = check_official_root(
            "黑龙江", self.roots,
            downloader=lambda *args, **kwargs: (_ for _ in ()).throw(DownloadRedirectError("token=secret")),
            clock=self.clock,
        )
        self.assertEqual(("unavailable", "redirect_error"), (result.status, result.reason_code))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["--province", "黑龙江", "--province", "黑龙江"])
        self.assertEqual(2, exit_code)
        self.assertEqual("live-smoke: invalid input\n", stderr.getvalue())

    def test_cleanup_failure_is_path_neutral_storage_observation(self) -> None:
        with patch("scripts.live_smoke.shutil.rmtree", side_effect=[OSError("C:/secret/path"), None]):
            result = check_official_root(
                "黑龙江", self.roots,
                downloader=lambda _url, workspace, **kwargs: self._result(workspace, "https://www.hljea.org.cn/"),
                clock=self.clock,
            )
        self.assertEqual(("unavailable", "storage_error"), (result.status, result.reason_code))
        self.assertNotIn("secret", repr(result))

    def test_complete_network_sentinel_permits_import_and_help(self) -> None:
        import socket
        import runpy

        blocked = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network"))
        patches = [
            patch.object(socket, "getaddrinfo", blocked), patch.object(socket, "gethostbyname", blocked),
            patch.object(socket, "gethostbyname_ex", blocked), patch.object(socket, "create_connection", blocked),
            patch.object(socket.socket, "connect", blocked), patch.object(socket.socket, "connect_ex", blocked),
            patch.object(socket.socket, "sendto", blocked),
        ]
        for item in patches: item.start()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["--help"]))
            runpy.run_path("scripts/live_smoke.py", run_name="not_main")
        finally:
            for item in reversed(patches): item.stop()

    def test_all_tracked_catalog_entries_and_hunan_discovery_path_are_accepted(self) -> None:
        healthy = LiveSmokeResult(
            province="湖南", status="healthy", requested_domain="jyt.hunan.gov.cn",
            final_domain="jyt.hunan.gov.cn", redirect_domains=("jyt.hunan.gov.cn",),
            checked_at="2026-08-24T00:00:00Z", content_type="text/html", size_bytes=0,
            reason_code=None,
        )
        with patch("scripts.live_smoke.check_official_root", return_value=healthy) as check:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(0, main(["--province", "湖南"])); self.assertEqual(0, main(["--province", "黑龙江"] ))
        self.assertIn("jyt.hunan.gov.cn", check.call_args_list[0].args[1][0])
        self.assertEqual("", stderr.getvalue())

    def test_same_site_http_redirect_is_healthy_but_internal_host_is_not_serialized(self) -> None:
        healthy = check_official_root(
            "黑龙江", self.roots,
            downloader=lambda _url, workspace, **kwargs: self._result(workspace, "https://www.hljea.org.cn/", "http://hljea.org.cn/path?token=secret"),
            clock=self.clock,
        )
        internal = check_official_root(
            "黑龙江", self.roots,
            downloader=lambda _url, workspace, **kwargs: self._result(workspace, "https://www.hljea.org.cn/", "https://metadata.internal/x?token=secret"),
            clock=self.clock,
        )
        self.assertEqual("healthy", healthy.status)
        self.assertEqual(("unavailable", "security_rejection"), (internal.status, internal.reason_code))
        self.assertNotIn("internal", repr(internal))

    def test_result_domains_are_normalized_and_source_surface_has_no_evidence_engines(self) -> None:
        valid = LiveSmokeResult(
            province="黑龙江", status="healthy", requested_domain="www.hljea.org.cn",
            final_domain="hljea.org.cn", redirect_domains=("www.hljea.org.cn", "hljea.org.cn"),
            checked_at="2026-08-24T00:00:00Z", content_type="text/html", size_bytes=0, reason_code=None,
        )
        for changes in ({"requested_domain": "WWW.hljea.org.cn"}, {"final_domain": "C:\\secret"}, {"redirect_domains": ("www.hljea.org.cn", "hljea.org.cn?token=secret")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(valid, **changes)
        source = Path("scripts/live_smoke.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        self.assertFalse(any("evidence" in name.casefold() or "validator" in name.casefold() or "report" in name.casefold() for name in imported))

    def test_unavailable_cli_is_observation_exit_zero(self) -> None:
        unavailable = LiveSmokeResult(
            province="黑龙江", status="unavailable", requested_domain="www.hljea.org.cn",
            final_domain=None, redirect_domains=("www.hljea.org.cn",),
            checked_at="2026-08-24T00:00:00Z", content_type=None, size_bytes=None,
            reason_code="timeout",
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("scripts.live_smoke.check_official_root", return_value=unavailable), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(0, main(["--province", "黑龙江"]))
        self.assertEqual("", stderr.getvalue())
        self.assertEqual("unavailable", json.loads(stdout.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
