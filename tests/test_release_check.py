import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.release_check import (
    CheckResult,
    ReleaseContext,
    check_markdown_links,
    check_path_identities,
    check_project_version,
    check_tracked_paths,
    check_untracked_sensitive_paths,
    evaluate_release,
    main,
)


ROOT = Path(__file__).resolve().parents[1]


class TrackedBoundaryTest(unittest.TestCase):
    def test_rejects_real_data_generated_private_and_noncanonical_paths(self) -> None:
        result = check_tracked_paths(
            [
                "README.md",
                "data/hubei/xibao.csv",
                "reports/final.docx",
                "private/key.pem",
                "tests/../outside.txt",
                "C:/outside.txt",
            ],
            forbidden_directories=("data", "reports", "private"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.count, 5)
        self.assertTrue(all("C:/" not in detail for detail in result.details))

    def test_rejects_casefold_duplicate_path_identity(self) -> None:
        result = check_tracked_paths(
            ["docs/Guide.md", "DOCS/guide.md"],
            forbidden_directories=("data",),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.details, ("duplicate-path-identity",))

    def test_rejects_tracked_symlink_or_reparse_without_leaking_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "outside-secret.txt"
            target.write_text("secret", encoding="utf-8")
            link = root / "linked.txt"
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {type(error).__name__}")

            result = check_path_identities(root, ["linked.txt"])

        self.assertFalse(result.ok)
        self.assertIn("tracked-link-or-reparse:linked.txt", result.details)
        self.assertNotIn(str(target), json.dumps(result.to_dict()))


class ReleaseComponentTest(unittest.TestCase):
    def test_version_must_match_expected_and_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nname="shengxue-skill"\nversion="0.1.0"\nrequires-python=">=3.10"\n',
                encoding="utf-8",
            )
            self.assertTrue(check_project_version(root, "0.1.0", "v0.1.0").ok)
            mismatch = check_project_version(root, "0.1.1", "v0.1.0")

        self.assertFalse(mismatch.ok)
        self.assertEqual(mismatch.details, ("expected-version-mismatch", "tag-version-mismatch"))

    def test_markdown_links_resolve_relative_to_each_document_and_stay_in_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "README.md").write_text(
                "[ok](docs/guide.md) [web](https://example.test) [anchor](#x)", encoding="utf-8"
            )
            (root / "docs" / "guide.md").write_text("[bad](../missing.md) [escape](../../secret.md)", encoding="utf-8")

            result = check_markdown_links(root, ["README.md", "docs/guide.md"])

        self.assertFalse(result.ok)
        self.assertEqual(
            result.details,
            ("missing-relative-link:docs/guide.md:1", "outside-relative-link:docs/guide.md:1"),
        )

    def test_untracked_sensitive_names_are_detected_without_absolute_paths(self) -> None:
        result = check_untracked_sensitive_paths(
            ["notes.txt", ".env.local", "private/client.key", "reports/student.docx"]
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.count, 3)
        self.assertTrue(all(not os.path.isabs(detail.split(":", 1)[-1]) for detail in result.details))

    def test_untracked_sensitive_filename_is_redacted_from_json(self) -> None:
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        result = check_untracked_sensitive_paths([f"private/{secret}.key"])
        serialized = json.dumps(result.to_dict())
        self.assertFalse(result.ok)
        self.assertNotIn(secret, serialized)
        self.assertIn("redacted-sensitive-path", serialized)

    def test_result_json_has_stable_bounded_shape(self) -> None:
        result = CheckResult("privacy", False, ("secret:README.md:4",), count=1)
        self.assertEqual(
            result.to_dict(),
            {"name": "privacy", "ok": False, "count": 1, "details": ["secret:README.md:4"]},
        )


class ReleaseEvaluationTest(unittest.TestCase):
    def test_current_tree_reports_later_task_gaps_precisely_without_running_nested_suite(self) -> None:
        context = ReleaseContext(
            root=ROOT,
            expected_version="0.1.0",
            tag=None,
            ci=True,
            run_tests=False,
        )

        report = evaluate_release(context)

        by_name = {result.name: result for result in report.results}
        self.assertFalse(report.ok)
        self.assertFalse(by_name["future_release_artifacts"].ok)
        self.assertIn("missing:.github/workflows/ci.yml", by_name["future_release_artifacts"].details)
        serialized = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertNotIn(str(ROOT), serialized)
        self.assertNotIn("ghp_", serialized)

    def test_cli_always_emits_json_and_nonzero_for_known_pending_gates(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), mock.patch.dict(
            os.environ, {"SHENGXUE_RELEASE_CHECK_TESTING": "1"}
        ):
            exit_code = main(
                [
                    "--root",
                    str(ROOT),
                    "--expected-version",
                    "0.1.0",
                    "--ci",
                    "--internal-skip-tests",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertNotEqual(exit_code, 0)
        self.assertFalse(payload["ok"])
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(str(ROOT), stdout.getvalue())

    def test_git_inventory_is_nul_delimited_for_unusual_tracked_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            unusual = root / "space name.txt"
            unusual.write_text("safe", encoding="utf-8")
            subprocess.run(["git", "add", "--", unusual.name], cwd=root, check=True)
            output = subprocess.run(
                ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
            ).stdout

        self.assertIn(b"space name.txt\x00", output)


if __name__ == "__main__":
    unittest.main()
