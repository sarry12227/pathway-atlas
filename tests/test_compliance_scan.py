import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
                "ci_generated_paths": ["build", "dist"],
                "deterministic_test_modules": ["tests.test_replay_scenarios"],
                "docx_test_modules": ["tests.test_docx_semantic_parity"],
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

    def test_detects_student_address_and_posix_or_unc_attachment_paths(self) -> None:
        posix_path = "/tmp/private/student.docx"
        unc_path = r"\\server\share\student.docx"
        findings = scan_text(
            f"学生住址：湖北省武汉市某路 1 号\n附件：{posix_path}\n本地文件：{unc_path}"
        )

        self.assertEqual(
            {finding.rule_id for finding in findings},
            {"student-address-label", "posix-absolute-attachment", "unc-absolute-path"},
        )
        serialized = json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False)
        self.assertNotIn(posix_path, serialized)
        self.assertNotIn(unc_path, serialized)

    def test_school_and_report_addresses_are_safe_but_bare_residential_address_is_pii(self) -> None:
        self.assertEqual(scan_text("学校地址：武汉市大学路；报告地址：https://example.test/report"), [])
        findings = scan_text("住址：湖北省武汉市某路 1 号")
        self.assertEqual({finding.rule_id for finding in findings}, {"student-address-label"})

    def test_legacy_price_helpers_keep_the_public_report_contract(self) -> None:
        self.assertEqual(find_price_text("方案原价 30600元"), "原价 30600")
        self.assertTrue(contains_price_text("现在仅需六万元"))
        self.assertFalse(contains_price_text("港校学费 3万港币"))

    def test_school_tuition_and_student_aid_are_safe_but_product_quotes_are_not(self) -> None:
        self.assertEqual(scan_text("武汉大学学费 30000元；国家助学金 6000元"), [])
        findings = scan_text("咨询服务报价 30000元，立即购买")
        self.assertEqual({finding.kind for finding in findings}, {"pricing_or_sales"})

    def test_distant_course_or_registration_words_do_not_poison_educational_amounts(self) -> None:
        safe_controls = (
            "课程报名请参考招生章程；武汉大学学费 30000元，国家助学金 6000元",
            "产品售价示例仅作说明，武汉大学学费 30000元",
        )
        for safe in safe_controls:
            self.assertEqual(scan_text(safe), [])
        findings = scan_text("升学规划课程收费 30000元")
        self.assertEqual({finding.kind for finding in findings}, {"pricing_or_sales"})

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

    def test_policy_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            _write_policy(policy_path)
            valid = policy_path.read_text("utf-8")
            policy_path.write_text(valid.replace('{"schema_version":', '{"schema_version":"1.0","schema_version":', 1), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(policy_path)

    def test_policy_rejects_nonfinite_json_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            _write_policy(policy_path)
            valid = policy_path.read_text("utf-8")
            policy_path.write_text(valid.replace("1048576", "NaN", 1), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(policy_path)

    def test_policy_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path = Path(temporary) / "policy.json"
            _write_policy(policy_path)
            payload = json.loads(policy_path.read_text("utf-8"))
            payload["mystery"] = True
            policy_path.write_text(json.dumps(payload), encoding="utf-8")
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

    def test_nul_in_undeclared_extension_fails_closed_instead_of_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "payload.txt").write_bytes(b"token=ghp_abcdefghijklmnopqrstuvwxyz123456\x00tail")
            policy_path = repo / "policy.json"
            _write_policy(policy_path)
            subprocess.run(["git", "add", "--", "payload.txt", "policy.json"], cwd=repo, check=True)

            summary = scan_tracked(repo, load_policy(policy_path))

        self.assertEqual(summary.skipped_binary_files, 0)
        self.assertIn("undeclared-binary-content", {finding.rule_id for finding in summary.findings})

    def test_index_symlink_mode_is_rejected_even_if_worktree_path_is_regular(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            payload = repo / "payload.txt"
            payload.write_text("ordinary worktree file", encoding="utf-8")
            policy_path = repo / "policy.json"
            _write_policy(policy_path)
            subprocess.run(["git", "add", "--", "policy.json"], cwd=repo, check=True)
            object_id = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo,
                input=b"outside-target",
                check=True,
                capture_output=True,
            ).stdout.decode("ascii").strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"120000,{object_id},payload.txt"],
                cwd=repo,
                check=True,
            )

            summary = scan_tracked(repo, load_policy(policy_path))

        self.assertIn("unsupported-tracked-mode", {finding.rule_id for finding in summary.findings})

    def test_intermediate_link_or_reparse_is_rejected_even_when_it_stays_in_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "real").mkdir()
            (repo / "real" / "payload.txt").write_text("safe", encoding="utf-8")
            try:
                (repo / "alias").symlink_to(repo / "real", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks unavailable: {type(error).__name__}")
            policy_path = repo / "policy.json"
            _write_policy(policy_path)
            subprocess.run(["git", "add", "--", "policy.json"], cwd=repo, check=True)
            object_id = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo,
                input=b"safe",
                check=True,
                capture_output=True,
            ).stdout.decode("ascii").strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"100644,{object_id},alias/payload.txt"],
                cwd=repo,
                check=True,
            )

            summary = scan_tracked(repo, load_policy(policy_path))

        self.assertIn("tracked-link-or-reparse", {finding.rule_id for finding in summary.findings})

    def test_tracked_inventory_ignores_all_ambient_git_control_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "tracked.txt").write_text("safe", encoding="utf-8")
            policy_path = repo / "policy.json"
            _write_policy(policy_path)
            subprocess.run(["git", "add", "--", "tracked.txt", "policy.json"], cwd=repo, check=True)
            poison_index = repo / "empty-index"
            poison_index.write_bytes(b"")

            with mock.patch.dict(
                os.environ,
                {
                    "GIT_INDEX_FILE": str(poison_index),
                    "GIT_DIR": str(repo / "missing-git-dir"),
                    "GIT_WORK_TREE": str(repo / "missing-work-tree"),
                },
            ):
                summary = scan_tracked(repo, load_policy(policy_path))

        self.assertEqual(summary.scanned_files, 2)

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

    def test_private_system_and_pricing_filename_fragments_are_also_redacted(self) -> None:
        for sensitive_name in ("shengxue-system.txt", "咨询服务报价30600元.txt"):
            finding = scan_text("手机号：13800138000", path=sensitive_name)[0]
            self.assertEqual(finding.path, "redacted-sensitive-path")

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

    def test_every_finding_output_uses_only_rule_location_metadata(self) -> None:
        finding = scan_text("runtime imports shengxue-system private export")[0]
        self.assertEqual(set(finding.to_dict()), {"kind", "rule", "line"})

        price = "30600元"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.txt"
            path.write_text(f"咨询服务报价 {price}", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main([str(path)])

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertNotIn(price, output)
        self.assertIn("rule=price-expression", output)

    def test_repository_policy_is_strict_and_loadable(self) -> None:
        policy = load_policy(ROOT / "release-policy.json")
        self.assertEqual(policy.schema_version, "1.0")
        self.assertGreater(len(policy.binary_extensions), 3)


if __name__ == "__main__":
    unittest.main()
