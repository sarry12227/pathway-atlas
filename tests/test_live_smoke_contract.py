"""Offline contract tests for the optional official-root health smoke."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import ast
import subprocess
import sys
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


IANA_NON_WEB_HOSTS = (
    "service.alt",
    "1.0.0.127.in-addr.arpa",
    "address.ip6.arpa",
    "number.e164.arpa",
    "probe.ipv4only.arpa",
    "dns.resolver.arpa",
    "identifier.uri.arpa",
    "identifier.urn.arpa",
    "node.6tisch.arpa",
    "auth.eap.arpa",
    "sink.as112.arpa",
    "servers.in-addr-servers.arpa",
    "servers.ip6-servers.arpa",
    "registry.iris.arpa",
    "authority.ns.arpa",
    "discovery.service.arpa",
    "future.arpa",
)
UNICODE_DNS_SEPARATOR_NON_WEB_HOST = "service。alt"


def _contract_findings(source: str) -> list[str]:
    """Return the live-smoke contract mutations present in *source*."""
    tree = ast.parse(source)
    findings = []
    forbidden = {"evidencestore", "validator", "engine", "report"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (any(part in (node.module or "").casefold() for part in forbidden) or any(part in alias.name.casefold() for alias in node.names for part in forbidden)):
            findings.append("forbidden-import")
        if isinstance(node, ast.Import) and any(part in alias.name.casefold() for alias in node.names for part in forbidden):
            findings.append("forbidden-import")
    if "allowlisted.add(chain[-1])" in source:
        findings.append("redirect-auto-allow")
    if '"raw_url"' in source or '"query"' in source:
        findings.append("raw-url-output")
    if "MAX_RESPONSE_BYTES = 1_048_576" not in source:
        findings.append("byte-cap")
    if source.count("downloader(requested_url") != 1:
        findings.append("downloader-seam")
    if 'if output.status == "unavailable":\n            return 2' in source:
        findings.append("unavailable-exit")
    return findings


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
                workspace, "https://www.hljea.org.cn/", "https://publisher.example.com/a"
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

    def test_impossible_catalog_dates_fail_before_health_or_network_calls(self) -> None:
        tracked = json.loads(
            (Path("references") / "provinces" / "index.json").read_text(encoding="utf-8")
        )
        mutations = (
            lambda payload: payload.__setitem__("verified_at", "2026-99-99"),
            lambda payload: payload["provinces"][0].__setitem__(
                "verified_at", "2026-02-30"
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            for index, mutate in enumerate(mutations):
                payload = json.loads(json.dumps(tracked, ensure_ascii=False))
                mutate(payload)
                catalog = directory / f"impossible-date-{index}.json"
                catalog.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    self.subTest(index=index),
                    patch("scripts.live_smoke._CATALOG_PATH", catalog),
                    patch("scripts.live_smoke.check_official_root") as check,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(2, main(["--province", "黑龙江"]))
                check.assert_not_called()
                self.assertEqual("", stdout.getvalue())
                self.assertEqual("live-smoke: invalid input\n", stderr.getvalue())

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

    def test_special_use_hosts_and_impossible_timestamps_fail_closed(self) -> None:
        for host in ("a.invalid", "a.test", "a.example", "a.localhost", "a.local", "a.internal", "home.arpa", "a.onion"):
            with self.subTest(host=host):
                result = check_official_root(
                    "黑龙江", self.roots,
                    downloader=lambda _url, workspace, host=host, **kwargs: self._result(workspace, "https://www.hljea.org.cn/", f"https://{host}/x"),
                    clock=self.clock,
                )
                self.assertEqual(("unavailable", "security_rejection"), (result.status, result.reason_code))
        valid = LiveSmokeResult("黑龙江", "healthy", "www.hljea.org.cn", "hljea.org.cn", ("www.hljea.org.cn", "hljea.org.cn"), "2026-08-24T00:00:00Z", "text/html", 0, None)
        with self.assertRaises(ValueError): replace(valid, checked_at="2026-99-99T29:66:99Z")

    def test_iana_non_web_provenance_is_rejected_in_results_and_redirect_chains(self) -> None:
        for host in (*IANA_NON_WEB_HOSTS, UNICODE_DNS_SEPARATOR_NON_WEB_HOST):
            with self.subTest(host=host, boundary="result"), self.assertRaises(ValueError):
                LiveSmokeResult(
                    "黑龙江", "healthy", host, host, (host,),
                    "2026-08-24T00:00:00Z", "text/html", 0, None,
                )
            with self.subTest(host=host, boundary="redirect"):
                result = check_official_root(
                    "黑龙江", self.roots,
                    downloader=lambda _url, workspace, host=host, **kwargs: self._result(
                        workspace, "https://www.hljea.org.cn/", f"https://{host}/document"
                    ),
                    clock=self.clock,
                )
                self.assertEqual(
                    ("unavailable", "security_rejection"),
                    (result.status, result.reason_code),
                )

    def test_non_web_catalog_roots_fail_before_health_or_network_calls(self) -> None:
        import socket

        blocked = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network"))
        for host in (*IANA_NON_WEB_HOSTS, UNICODE_DNS_SEPARATOR_NON_WEB_HOST):
            catalog_payload = {
                "schema_version": "1.0",
                "verified_at": "2026-08-24",
                "coverage_note": "synthetic catalog",
                "mode_authority_urls": ["https://www.moe.gov.cn/source"],
                "provinces": [{
                    "province": "甲",
                    "aliases": ["甲"],
                    "mode": "3+3",
                    "authority_name": "甲院",
                    "official_roots": [f"https://{host}/discovery"],
                    "mode_source_url": "https://www.moe.gov.cn/source",
                    "verified_at": "2026-08-24",
                    "notes": "synthetic catalog",
                }],
            }
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                catalog = Path(temporary) / "catalog.json"
                catalog.write_text(json.dumps(catalog_payload, ensure_ascii=False), encoding="utf-8")
                with (
                    patch("scripts.live_smoke._CATALOG_PATH", catalog),
                    patch("scripts.live_smoke.check_official_root") as check,
                    patch.object(socket, "getaddrinfo", blocked),
                    patch.object(socket, "create_connection", blocked),
                    patch.object(socket.socket, "connect", blocked),
                    patch.object(socket.socket, "connect_ex", blocked),
                    patch.object(socket.socket, "send", blocked),
                    patch.object(socket.socket, "sendall", blocked),
                    patch.object(socket.socket, "sendto", blocked),
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(2, main(["--province", "甲"]))
                check.assert_not_called()

    def test_unicode_dns_separator_non_web_root_fails_before_downloader(self) -> None:
        calls: list[str] = []

        def downloader(url: str, _workspace: Path, **_kwargs: object) -> DownloadResult:
            calls.append(url)
            raise DownloadSecurityError("unexpected downloader call")

        with self.assertRaises(ValueError):
            check_official_root(
                "甲",
                (f"https://{UNICODE_DNS_SEPARATOR_NON_WEB_HOST}/",),
                downloader=downloader,
                clock=self.clock,
            )
        self.assertEqual([], calls)

    def test_public_numeric_dns_names_remain_valid_provenance(self) -> None:
        roots = ("https://exam2026.gov.cn/discovery",)
        result = check_official_root(
            "甲", roots,
            downloader=lambda _url, workspace, **kwargs: self._result(
                workspace, "https://exam2026.gov.cn/discovery", "http://exam2026.gov.cn/document"
            ),
            clock=self.clock,
        )
        self.assertEqual("healthy", result.status)
        self.assertEqual(("exam2026.gov.cn",), result.redirect_domains)

    def test_fresh_child_arms_network_sentinel_before_target_import(self) -> None:
        code = """import socket
from unittest.mock import patch
blocked=lambda *a,**k: (_ for _ in ()).throw(AssertionError('armed'))
patches=[patch.object(socket,'getaddrinfo',blocked),patch.object(socket,'gethostbyname',blocked),patch.object(socket,'gethostbyname_ex',blocked),patch.object(socket,'gethostbyaddr',blocked),patch.object(socket,'getfqdn',blocked),patch.object(socket,'create_connection',blocked),patch.object(socket.socket,'connect',blocked),patch.object(socket.socket,'connect_ex',blocked),patch.object(socket.socket,'send',blocked),patch.object(socket.socket,'sendall',blocked),patch.object(socket.socket,'sendto',blocked)]
[p.start() for p in patches]
import scripts.live_smoke as target
assert target.main(['--help']) == 0
for action in (lambda:socket.getaddrinfo('example.com',443),lambda:socket.gethostbyname('example.com'),lambda:socket.gethostbyname_ex('example.com'),lambda:socket.gethostbyaddr('127.0.0.1'),lambda:socket.getfqdn('example.com'),lambda:socket.create_connection(('example.com',443)),lambda:socket.socket().connect(('example.com',443)),lambda:socket.socket().connect_ex(('example.com',443)),lambda:socket.socket().send(b'x'),lambda:socket.socket().sendall(b'x'),lambda:socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'x',('8.8.8.8',53))):
    try: action()
    except AssertionError: pass
    else: raise AssertionError('canary not armed')
print('sentinel-ok')"""
        completed = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("sentinel-ok", completed.stdout)

    def test_source_mutation_oracle_rejects_all_six_contract_breaks(self) -> None:
        source = Path("scripts/live_smoke.py").read_text(encoding="utf-8")
        self.assertEqual([], _contract_findings(source))
        mutations = {
            "redirect-auto-allow": source.replace("        status =", "        allowlisted.add(chain[-1])\n        status ="),
            "forbidden-import": "from scripts.evidence import EvidenceStore\n" + source,
            "unavailable-exit": source.replace("        sys.stdout.write", "        if output.status == \"unavailable\":\n            return 2\n        sys.stdout.write"),
            "raw-url-output": source.replace('"status": self.status,', '"raw_url": "https://x.example.com/?token=secret",\n            "status": self.status,'),
            "byte-cap": source.replace("MAX_RESPONSE_BYTES = 1_048_576", "MAX_RESPONSE_BYTES = 2_000_000"),
            "downloader-seam": source.replace("downloader(requested_url", "bypassed(requested_url"),
        }
        for expected, mutated in mutations.items():
            with self.subTest(expected=expected): self.assertIn(expected, _contract_findings(mutated))
        self.assertEqual([], _contract_findings(source + "\n# harmless prose control\n"))


if __name__ == "__main__":
    unittest.main()
