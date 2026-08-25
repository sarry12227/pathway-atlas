import contextlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from scripts import release_check as release_gate
from scripts.release_check import (
    CheckResult,
    ReleaseContext,
    _check_clean_worktree,
    _check_deterministic_boundaries,
    _check_docx_tests,
    _check_future_paths,
    _check_tracked_modes,
    check_markdown_links,
    check_path_identities,
    check_project_version,
    check_tracked_paths,
    check_untracked_sensitive_paths,
    evaluate_release,
    main,
)
from scripts.compliance_scan import git_tracked_entries, load_policy, missing_required_release_paths


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

    def test_release_inventory_rejects_non_regular_index_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            object_id = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=root,
                input=b"target",
                capture_output=True,
                check=True,
            ).stdout.decode("ascii").strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"120000,{object_id},payload.txt"],
                cwd=root,
                check=True,
            )

            result = _check_tracked_modes(git_tracked_entries(root))

        self.assertFalse(result.ok)
        self.assertIn("rule=unsupported-tracked-mode", result.details[0])


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

    def test_untracked_safe_name_with_secret_content_is_rejected_and_redacted(self) -> None:
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notes.txt").write_text(f"token={secret}", encoding="utf-8")
            result = check_untracked_sensitive_paths(
                ["notes.txt"], root=root, policy=load_policy(ROOT / "release-policy.json")
            )

        serialized = json.dumps(result.to_dict())
        self.assertFalse(result.ok)
        self.assertNotIn(secret, serialized)
        self.assertIn("rule=github-token", serialized)

    def test_ignored_inventory_scans_sensitive_files_without_flagging_benign_cache(self) -> None:
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            (root / ".gitignore").write_text(".env\nignored-notes.txt\n.venv/\n", encoding="utf-8")
            subprocess.run(["git", "add", "--", ".gitignore"], cwd=root, check=True)
            (root / ".env").write_text(f"token={secret}", encoding="utf-8")
            (root / "ignored-notes.txt").write_text(f"token={secret}", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "cache.pyc").write_bytes(b"\x00benign-cache")

            ordinary, ignored = release_gate._git_untracked_inventory(root)
            result = check_untracked_sensitive_paths(
                ordinary,
                root=root,
                policy=load_policy(ROOT / "release-policy.json"),
                ignored_paths=ignored,
            )

        serialized = json.dumps(result.to_dict())
        self.assertFalse(result.ok)
        self.assertEqual(result.count, 5)
        self.assertIn("rule=sensitive-name", serialized)
        self.assertIn("rule=github-token", serialized)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("cache.pyc", serialized)

    def test_ignored_benign_roots_never_hide_sensitive_suffixes(self) -> None:
        sensitive_paths = (
            ".venv/private.pem",
            ".venv/client.key",
            "node_modules/client.p12",
            "dist/student.docx",
            "dist/release.pem",
        )
        safe_paths = (
            ".venv/cache.pyc",
            "node_modules/package/index.js",
            "dist/release.zip",
            "dist/release.zip.sha256",
        )
        result = check_untracked_sensitive_paths(
            (),
            policy=load_policy(ROOT / "release-policy.json"),
            ignored_paths=sensitive_paths + safe_paths,
        )

        serialized = json.dumps(result.to_dict())
        self.assertFalse(result.ok)
        self.assertEqual(result.count, len(sensitive_paths))
        self.assertTrue(
            all("kind=untracked_path;rule=sensitive-name;line=0;" in detail for detail in result.details)
        )
        for path in sensitive_paths:
            self.assertIn(path, serialized)
        for path in safe_paths:
            self.assertNotIn(path, serialized)

    def test_ci_cleanliness_exempts_only_declared_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            (root / "tracked.txt").write_text("base", encoding="utf-8")
            subprocess.run(["git", "add", "--", "tracked.txt"], cwd=root, check=True)
            (root / "tracked.txt").write_text("dirty", encoding="utf-8")
            (root / "generated").mkdir()
            (root / "generated" / "report.txt").write_text("generated", encoding="utf-8")

            result = _check_clean_worktree(root, True, ("generated",))

        self.assertFalse(result.ok)
        self.assertEqual(result.details, ("worktree-has-unexpected-changes",))

    def test_untracked_future_artifact_does_not_satisfy_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text("name: fake", encoding="utf-8")
            policy = load_policy(ROOT / "release-policy.json")

            result = _check_future_paths(root, policy, tracked=())

        self.assertFalse(result.ok)
        self.assertIn("missing-or-untracked:.github/workflows/ci.yml", result.details)

    def test_release_artifact_gate_uses_only_policy_exact_and_prefix_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = replace(
                load_policy(ROOT / "release-policy.json"),
                required_release_paths=("custom-required.txt",),
                required_release_prefixes=("custom-fixtures",),
            )

            result = _check_future_paths(root, policy, tracked=())

        self.assertEqual(
            result.details,
            (
                "missing-or-untracked:custom-required.txt",
                "missing-or-untracked:custom-fixtures/**",
            ),
        )

    def test_offline_gate_installs_real_sentinel_and_does_not_trust_noop_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tests" / "test_noop_network.py").write_text(
                "import unittest\n"
                "def network_sentinel(): pass\n"
                "class Safe(unittest.TestCase):\n"
                "    def test_safe(self): self.assertEqual(2 + 2, 4)\n",
                encoding="utf-8",
            )
            (root / "scripts").mkdir()
            (root / "scripts" / "live_smoke.py").write_text("# explicit live-only boundary\n", encoding="utf-8")
            policy = replace(
                load_policy(ROOT / "release-policy.json"),
                deterministic_test_modules=("tests.test_noop_network",),
            )
            context = ReleaseContext(root=root, expected_version="0.1.0")

            result = _check_deterministic_boundaries(context, policy)

        self.assertTrue(result.ok, result.details)
        sendmsg = "armed" if hasattr(socket.socket, "sendmsg") else "unavailable"
        armed = 14 if sendmsg == "armed" else 13
        self.assertEqual(
            result.details,
            (f"armed={armed};sendmsg={sendmsg};attempts=0;run=1;skipped=0",),
        )

    def test_offline_gate_rejects_any_network_attempt_without_leaking_endpoint(self) -> None:
        endpoint = "private-endpoint.invalid"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tests" / "test_network_attempt.py").write_text(
                "import socket, unittest\n"
                "class Unsafe(unittest.TestCase):\n"
                f"    def test_network(self): socket.getaddrinfo('{endpoint}', 443)\n",
                encoding="utf-8",
            )
            (root / "scripts").mkdir()
            (root / "scripts" / "live_smoke.py").write_text("# explicit live-only boundary\n", encoding="utf-8")
            policy = replace(
                load_policy(ROOT / "release-policy.json"),
                deterministic_test_modules=("tests.test_network_attempt",),
            )
            context = ReleaseContext(root=root, expected_version="0.1.0")

            result = _check_deterministic_boundaries(context, policy)

        self.assertFalse(result.ok)
        self.assertNotIn(endpoint, json.dumps(result.to_dict()))

    def test_docx_gate_uses_explicit_suite_loaded_run_and_skip_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
            suite_path = root / "tests" / "test_docx_contract.py"
            suite_path.write_text(
                "import unittest\n"
                "class Docx(unittest.TestCase):\n"
                "    def test_export(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            policy = replace(
                load_policy(ROOT / "release-policy.json"),
                docx_test_modules=("tests.test_docx_contract",),
            )
            context = ReleaseContext(root=root, expected_version="0.1.0")
            passed = _check_docx_tests(context, policy)
            suite_path.write_text(
                "import unittest\n"
                "class Docx(unittest.TestCase):\n"
                "    @unittest.skip('dependency absent')\n"
                "    def test_export(self): pass\n",
                encoding="utf-8",
            )
            skipped = _check_docx_tests(context, policy)

        self.assertTrue(passed.ok)
        self.assertEqual(passed.details, ("loaded=1;run=1;skipped=0",))
        self.assertFalse(skipped.ok)

    def test_result_json_has_stable_bounded_shape(self) -> None:
        result = CheckResult("privacy", False, ("secret:README.md:4",), count=1)
        self.assertEqual(
            result.to_dict(),
            {"name": "privacy", "ok": False, "count": 1, "details": ["secret:README.md:4"]},
        )


class ReleaseEvaluationTest(unittest.TestCase):
    def test_all_release_git_queries_ignore_ambient_git_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            poison_index = Path(temporary) / "empty-index"
            poison_index.write_bytes(b"")
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_INDEX_FILE": str(poison_index),
                    "GIT_DIR": str(Path(temporary) / "fake-git"),
                    "GIT_WORK_TREE": str(Path(temporary) / "fake-worktree"),
                },
            ):
                report = evaluate_release(
                    ReleaseContext(
                        root=ROOT,
                        expected_version="0.1.0",
                        ci=True,
                        run_tests=False,
                    )
                )

        by_name = {result.name: result for result in report.results}
        self.assertTrue(by_name["repository_scope"].ok)
        self.assertTrue(by_name["tracked_inventory"].ok)
        self.assertGreater(by_name["tracked_inventory"].count, 100)

    def test_future_artifact_gate_allows_only_the_task8_transition_without_nested_suite(self) -> None:
        context = ReleaseContext(
            root=ROOT,
            expected_version="0.1.0",
            tag=None,
            ci=True,
            run_tests=False,
        )

        report = evaluate_release(context)

        by_name = {result.name: result for result in report.results}
        self.assertNotIn("required_artifacts", by_name)
        future_result = by_name["future_release_artifacts"]
        tracked = {entry.path for entry in git_tracked_entries(ROOT)}
        policy = load_policy(ROOT / "release-policy.json")
        expected = tuple(
            f"missing-or-untracked:{path}"
            for path in missing_required_release_paths(policy, tracked)
        )
        self.assertIn(expected, ((), ("missing-or-untracked:docs/release-process.md",)))
        self.assertEqual(future_result.details, expected)
        self.assertEqual(future_result.ok, not expected)
        serialized = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertNotIn(str(ROOT), serialized)
        self.assertNotIn("ghp_", serialized)

    def test_cli_always_emits_json_across_the_known_task8_transition(self) -> None:
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
        self.assertEqual(exit_code == 0, payload["ok"])
        future = next(result for result in payload["results"] if result["name"] == "future_release_artifacts")
        self.assertIn(
            tuple(future["details"]),
            ((), ("missing-or-untracked:docs/release-process.md",)),
        )
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
