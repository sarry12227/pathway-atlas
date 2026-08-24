import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.compliance_scan import (
    PolicyError,
    contains_price_text,
    find_price_text,
    load_policy,
    main,
    scan_text,
    scan_tracked,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_policy(
    path: Path,
    *,
    allowlist: list[dict[str, str]] | None = None,
    file_allowlist: list[dict[str, object]] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "binary_extensions": [".png", ".xlsx"],
                "max_text_bytes": 1048576,
                "forbidden_tracked_directories": ["data", "output", "private"],
                "allowlist": allowlist or [],
                "file_allowlist": file_allowlist or [],
                "future_release_paths": [],
                "deterministic_test_modules": ["tests.test_replay_scenarios"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class ComplianceScanTest(unittest.TestCase):
    def test_detects_every_required_category_without_retaining_values(self) -> None:
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        phone = "13800138000"
        identity = "11010519491231002X"
        absolute = "C:\\Users\\someone\\Downloads\\student-report.docx"
        text = "\n".join(
            (
                f"token={secret}",
                "学生姓名：张三",
                f"手机号：{phone}",
                f"身份证号：{identity}",
                "runtime imports shengxue-system private export",
                "限时优惠，原价 30600元，立即咨询",
                f"附件：{absolute}",
            )
        )

        findings = scan_text(text)

        self.assertEqual(
            {finding.kind for finding in findings},
            {
                "secret",
                "student_pii",
                "phone",
                "identity_number",
                "private_system_reference",
                "pricing_or_sales",
                "absolute_local_path",
            },
        )
        serialized = json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False)
        for sensitive in (secret, phone, identity, absolute, "张三", "30600元"):
            self.assertNotIn(sensitive, serialized)

    def test_disclaimer_and_non_price_numbers_are_not_hidden_false_positives(self) -> None:
        self.assertEqual(scan_text("AI 生成仅供参考；武汉大学 985；位次 ±2000；学费 3万港币"), [])

    def test_legacy_price_helpers_keep_the_public_report_contract(self) -> None:
        self.assertEqual(find_price_text("方案原价 30600元"), "原价 30600")
        self.assertTrue(contains_price_text("现在仅需六万元"))
        self.assertFalse(contains_price_text("港校学费 3万港币"))

    def test_exact_allowlist_requires_path_kind_line_hash_and_reason(self) -> None:
        line = "手机号：13800138000"
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            _write_policy(
                policy_path,
                allowlist=[
                    {
                        "path": "tests/fixture.txt",
                        "kind": "phone",
                        "line_sha256": digest,
                        "reason": "synthetic negative fixture",
                    }
                ],
            )
            policy = load_policy(policy_path)

        self.assertEqual(scan_text(line, path="tests/fixture.txt", policy=policy), [])
        self.assertEqual(
            {item.kind for item in scan_text(line + " synthetic", path="tests/fixture.txt", policy=policy)},
            {"phone"},
        )
        self.assertEqual(
            {item.kind for item in scan_text(line, path="docs/example.txt", policy=policy)},
            {"phone"},
        )

    def test_policy_rejects_duplicate_nonfinite_and_unknown_keys(self) -> None:
        malformed = (
            '{"schema_version":"1.0","schema_version":"1.0",'
            '"binary_extensions":[],"max_text_bytes":NaN,'
            '"forbidden_tracked_directories":[],"allowlist":[],'
            '"file_allowlist":[],"future_release_paths":[],'
            '"deterministic_test_modules":[],"mystery":true}'
        )
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            policy_path.write_text(malformed, encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(policy_path)

    def test_exact_file_allowlist_invalidates_on_any_text_change(self) -> None:
        content = "手机号：13800138000\r\n"
        digest = hashlib.sha256(content.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            fixture = repo / "fixture.txt"
            fixture.write_bytes(content.encode("utf-8"))
            policy_path = repo / "policy.json"
            _write_policy(
                policy_path,
                file_allowlist=[
                    {
                        "path": "fixture.txt",
                        "kinds": ["phone"],
                        "file_sha256": digest,
                        "reason": "synthetic negative fixture",
                    }
                ],
            )
            subprocess.run(["git", "add", "--", "fixture.txt", "policy.json"], cwd=repo, check=True)
            policy = load_policy(policy_path)
            self.assertEqual(scan_tracked(repo, policy).findings, ())

            fixture.write_text(content + "changed", encoding="utf-8")
            findings = scan_tracked(repo, policy).findings

        self.assertEqual({finding.kind for finding in findings}, {"phone"})

    def test_tracked_scan_uses_git_inventory_and_skips_binary_by_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "clean.txt").write_text("AI 生成仅供参考", encoding="utf-8")
            (repo / "fixture.png").write_bytes(b"token=ghp_abcdefghijklmnopqrstuvwxyz123456")
            (repo / "untracked.txt").write_text("手机号：13800138000", encoding="utf-8")
            policy_path = repo / "policy.json"
            _write_policy(policy_path)
            subprocess.run(
                ["git", "add", "--", "clean.txt", "fixture.png", "policy.json"],
                cwd=repo,
                check=True,
            )

            summary = scan_tracked(repo, load_policy(policy_path))

        self.assertEqual(summary.findings, ())
        self.assertEqual(summary.scanned_files, 2)
        self.assertEqual(summary.skipped_binary_files, 1)

    def test_tracked_finding_redacts_a_secret_embedded_in_the_filename(self) -> None:
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            sensitive_name = f"fixture-{secret}.txt"
            (repo / sensitive_name).write_text("手机号：13800138000", encoding="utf-8")
            policy_path = repo / "policy.json"
            _write_policy(policy_path)
            subprocess.run(["git", "add", "--", sensitive_name, "policy.json"], cwd=repo, check=True)

            summary = scan_tracked(repo, load_policy(policy_path))

        self.assertNotIn(secret, json.dumps(summary.to_dict(), ensure_ascii=False))
        self.assertEqual(summary.findings[0].path, "redacted-sensitive-path")

    def test_legacy_file_cli_reports_location_not_sensitive_match_or_absolute_path(self) -> None:
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private-report.txt"
            path.write_text(f"token={secret}", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main([str(path)])

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertNotIn(secret, output)
        self.assertNotIn(str(path), output)
        self.assertIn("secret", output)

    def test_repository_policy_is_strict_and_loadable(self) -> None:
        policy = load_policy(ROOT / "release-policy.json")
        self.assertEqual(policy.schema_version, "1.0")
        self.assertGreater(len(policy.binary_extensions), 3)


if __name__ == "__main__":
    unittest.main()
