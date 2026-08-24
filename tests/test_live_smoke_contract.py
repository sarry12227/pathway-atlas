"""Offline contract tests for the optional official-root health smoke."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.downloader import (
    DownloadHttpError,
    DownloadMediaTypeError,
    DownloadNetworkError,
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


if __name__ == "__main__":
    unittest.main()
