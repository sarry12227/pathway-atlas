from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import build_release


class BuildReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        (self.root / "scripts").mkdir()
        (self.root / "tests" / "fixtures" / "synthetic").mkdir(parents=True)
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "pathway-atlas"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        for document in (
            "CHANGELOG.md",
            "CODE_OF_CONDUCT.md",
            "CONTRIBUTING.md",
            "DATA_SOURCES.md",
            "LICENSE",
            "README.md",
            "ROADMAP.md",
            "SECURITY.md",
            "SKILL.md",
        ):
            (self.root / document).write_text(f"# Synthetic {document}\n", encoding="utf-8")
        (self.root / "release-policy.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.2",
                    "binary_extensions": [".png"],
                    "binary_release_manifest": [],
                    "max_text_bytes": 1048576,
                    "forbidden_tracked_directories": [
                        ".cache",
                        "data",
                        "evidence/raw-downloads",
                        "output",
                        "private",
                        "reports",
                        "work",
                    ],
                    "allowlist": [],
                    "file_allowlist": [],
                    "required_release_paths": [
                        "CHANGELOG.md",
                        "CODE_OF_CONDUCT.md",
                        "CONTRIBUTING.md",
                        "DATA_SOURCES.md",
                        "LICENSE",
                        "README.md",
                        "ROADMAP.md",
                        "SECURITY.md",
                        "SKILL.md",
                        "pyproject.toml",
                        "release-policy.json",
                        "scripts/release_check.py",
                    ],
                    "required_release_prefixes": ["tests/fixtures"],
                    "ci_generated_paths": ["build", "dist"],
                    "deterministic_test_modules": ["tests.test_replay_scenarios"],
                    "docx_test_modules": ["tests.test_docx_semantic_parity"],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "tests" / "fixtures" / "synthetic" / "rows.csv").write_text(
            "score,rank\n700,1\n", encoding="utf-8"
        )
        (self.root / "scripts" / "release_check.py").write_text(
            "import sys\nraise SystemExit(0)\n", encoding="utf-8"
        )
        self._git_add_all()

    def _git_add_all(self) -> None:
        subprocess.run(["git", "add", "--all"], cwd=self.root, check=True)

    def _build(self, output: str) -> build_release.ReleaseArtifacts:
        return build_release.build_release(self.root, "0.1.0", output)

    def _zip_names(self, artifact: Path) -> list[str]:
        with zipfile.ZipFile(artifact) as archive:
            return archive.namelist()

    def test_two_real_git_snapshot_builds_are_byte_identical_and_well_formed(self) -> None:
        first = self._build("dist-one")
        second = self._build("dist-two")

        self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
        digest = hashlib.sha256(first.archive.read_bytes()).hexdigest()
        self.assertEqual(
            first.checksums.read_text("utf-8"),
            f"{digest}  pathway-atlas-0.1.0.zip\n",
        )
        with zipfile.ZipFile(first.archive) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            required = {
                "pathway-atlas/CHANGELOG.md",
                "pathway-atlas/CODE_OF_CONDUCT.md",
                "pathway-atlas/CONTRIBUTING.md",
                "pathway-atlas/DATA_SOURCES.md",
                "pathway-atlas/LICENSE",
                "pathway-atlas/README.md",
                "pathway-atlas/ROADMAP.md",
                "pathway-atlas/SECURITY.md",
                "pathway-atlas/SKILL.md",
                "pathway-atlas/pyproject.toml",
                "pathway-atlas/release-policy.json",
                "pathway-atlas/tests/fixtures/synthetic/rows.csv",
            }
            self.assertTrue(required <= set(names))
            self.assertIn(
                "pathway-atlas/tests/fixtures/synthetic/rows.csv",
                names,
            )
            legacy_root = "shengxue" + "-skill/"
            self.assertFalse(any(name.startswith(legacy_root) for name in names))
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(info.create_system, 3)
                self.assertIn((info.external_attr >> 16) & 0o777, (0o644, 0o755))

    def test_archive_uses_only_tracked_tree_and_ignores_untracked_content(self) -> None:
        (self.root / "untracked-secret.txt").write_text("not packaged\n", encoding="utf-8")

        artifact = self._build("dist")

        with zipfile.ZipFile(artifact.archive) as archive:
            self.assertEqual(
                archive.read("pathway-atlas/README.md"),
                b"# Synthetic README.md\n",
            )
            self.assertNotIn("pathway-atlas/untracked-secret.txt", archive.namelist())

    def test_worktree_and_index_mismatch_fails_before_publication(self) -> None:
        (self.root / "README.md").write_text("uncommitted replacement\n", encoding="utf-8")

        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist")

        self.assertFalse((self.root / "dist").exists())

    def test_gate_staging_a_private_file_changes_tree_and_aborts_without_packaging(self) -> None:
        def mutate_index(_root: Path, _version: str) -> None:
            private = self.root / "private"
            private.mkdir()
            (private / "student.txt").write_text("private\n", encoding="utf-8")
            subprocess.run(["git", "add", "--", "private/student.txt"], cwd=self.root, check=True)

        with mock.patch.object(build_release, "_run_release_check", side_effect=mutate_index):
            with self.assertRaises(build_release.BuildReleaseError):
                self._build("dist")

        self.assertFalse((self.root / "dist").exists())

    def test_gate_runs_first_and_failure_is_fixed_path_neutral_with_no_partial_output(self) -> None:
        sensitive = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        (self.root / "scripts" / "release_check.py").write_text(
            f'import sys\nprint("{sensitive}", file=sys.stderr)\nraise SystemExit(2)\n',
            encoding="utf-8",
        )
        self._git_add_all()
        stderr = io.StringIO()

        with mock.patch("sys.stderr", stderr):
            exit_code = build_release.main(
                ["--root", str(self.root), "--version", "0.1.0", "--output", "dist"]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue(), "release check failed\n")
        self.assertNotIn(sensitive, stderr.getvalue())
        self.assertFalse((self.root / "dist").exists())

    def test_failed_gate_reports_safe_check_and_test_identifiers(self) -> None:
        payload = {"results": [
            {"name": "docx_tests", "ok": False, "details": ["unsafe/detail"]},
            {"name": "full_tests", "ok": False, "details": ["failed-test:test_report.ReportTest.test_partial"]},
            {"name": "unsafe/detail", "ok": False, "details": []},
        ]}
        completed = subprocess.CompletedProcess([], 2, json.dumps(payload).encode(), b"untrusted stderr")
        with mock.patch.object(build_release.subprocess, "run", return_value=completed):
            with self.assertRaises(build_release.BuildReleaseError) as error:
                build_release._run_release_check(self.root, "0.1.0")
        self.assertIn("docx_tests", str(error.exception))
        self.assertIn("test_report.ReportTest.test_partial", str(error.exception))
        self.assertNotIn("unsafe/detail", str(error.exception))
        self.assertNotIn("untrusted stderr", str(error.exception))

    def test_release_checker_can_import_a_sibling_module_as_the_real_gate_does(self) -> None:
        (self.root / "scripts" / "gate_helper.py").write_text(
            "ALLOW_RELEASE = True\n", encoding="utf-8"
        )
        (self.root / "scripts" / "release_check.py").write_text(
            "from gate_helper import ALLOW_RELEASE\n"
            "raise SystemExit(0 if ALLOW_RELEASE else 2)\n",
            encoding="utf-8",
        )
        self._git_add_all()

        artifacts = self._build("dist")

        self.assertTrue(artifacts.archive.is_file())

    def test_rejects_version_mismatch_unsafe_output_sensitive_paths_and_overwrite(self) -> None:
        with self.assertRaises(build_release.BuildReleaseError):
            build_release.build_release(self.root, "0.1.1", "dist")
        with self.assertRaises(build_release.BuildReleaseError):
            build_release.build_release(self.root, "0.1.0", "../escape")

        (self.root / ".env.local").write_text("placeholder\n", encoding="utf-8")
        self._git_add_all()
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-sensitive")

        subprocess.run(["git", "rm", "--cached", "--", ".env.local"], cwd=self.root, check=True)
        first = self._build("dist-existing")
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-existing")
        self.assertTrue(first.archive.is_file())

    def test_rejects_case_collisions_and_nonordinary_git_index_modes(self) -> None:
        object_id = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=self.root,
            input=b"payload",
            capture_output=True,
            check=True,
        ).stdout.decode("ascii").strip()
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{object_id},Guide.md"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{object_id},guide.md"],
            cwd=self.root,
            check=True,
        )
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-collision")

        subprocess.run(["git", "rm", "--cached", "--", "Guide.md", "guide.md"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"120000,{object_id},linked.txt"],
            cwd=self.root,
            check=True,
        )
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-link")

    def test_policy_forbidden_directories_required_documents_and_fixture_are_enforced(self) -> None:
        for forbidden in ("data", "evidence/raw-downloads", "private", "reports"):
            with self.subTest(forbidden=forbidden):
                path = self.root.joinpath(*forbidden.split("/"), "payload.txt")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("must not ship\n", encoding="utf-8")
                subprocess.run(["git", "add", "--", forbidden], cwd=self.root, check=True)
                with self.assertRaises(build_release.BuildReleaseError):
                    self._build(f"dist-{forbidden.replace('/', '-')}")
                subprocess.run(["git", "rm", "-r", "--cached", "--", forbidden], cwd=self.root, check=True)

        for required in ("SECURITY.md", "CODE_OF_CONDUCT.md", "ROADMAP.md"):
            with self.subTest(required=required):
                subprocess.run(["git", "rm", "--cached", "--", required], cwd=self.root, check=True)
                with self.assertRaises(build_release.BuildReleaseError):
                    self._build(f"dist-missing-{required.casefold().replace('.', '-')}")
                subprocess.run(["git", "add", "--", required], cwd=self.root, check=True)
        subprocess.run(["git", "rm", "-r", "--cached", "--", "tests/fixtures"], cwd=self.root, check=True)
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-missing-fixture")

    def test_unmanifested_docx_pdf_and_xlsx_never_reach_an_archive(self) -> None:
        policy_path = self.root / "release-policy.json"
        payload = json.loads(policy_path.read_text("utf-8"))
        payload["binary_extensions"] = [".docx", ".pdf", ".png", ".xlsx"]
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        for name in ("student.docx", "student.pdf", "student.xlsx"):
            (self.root / name).write_bytes(b"unreviewed-binary\x00payload")
        self._git_add_all()

        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-unreviewed")

        self.assertFalse((self.root / "dist-unreviewed").exists())

    def test_exact_synthetic_binary_manifest_is_packaged_and_hash_drift_is_rejected(self) -> None:
        binary = b"synthetic-binary\x00original"
        fixture = self.root / "tests" / "fixtures" / "synthetic" / "sample.png"
        fixture.write_bytes(binary)
        policy_path = self.root / "release-policy.json"
        payload = json.loads(policy_path.read_text("utf-8"))
        payload["binary_release_manifest"] = [
            {
                "path": "tests/fixtures/synthetic/sample.png",
                "sha256": hashlib.sha256(binary).hexdigest(),
                "classification": "synthetic",
            }
        ]
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
        self._git_add_all()

        artifacts = self._build("dist-binary")
        with zipfile.ZipFile(artifacts.archive) as archive:
            self.assertEqual(
                archive.read("pathway-atlas/tests/fixtures/synthetic/sample.png"),
                binary,
            )

        fixture.write_bytes(b"synthetic-binary\x00drift")
        subprocess.run(["git", "add", "--", "tests/fixtures/synthetic/sample.png"], cwd=self.root, check=True)
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-binary-drift")
        self.assertFalse((self.root / "dist-binary-drift").exists())

    def test_git_environment_poison_is_ignored(self) -> None:
        poison = self.root / "poison-index"
        poison.write_bytes(b"")
        with mock.patch.dict(
            os.environ,
            {
                "GIT_INDEX_FILE": str(poison),
                "GIT_DIR": str(self.root / "fake-git"),
                "GIT_WORK_TREE": str(self.root / "fake-worktree"),
            },
        ):
            artifact = self._build("dist")

        self.assertIn("pathway-atlas/SKILL.md", self._zip_names(artifact.archive))

    def test_replacement_ref_is_rejected_and_private_blob_is_never_packaged(self) -> None:
        private = b"token=ghp_builder_private_payload_1234567890\x00tail"
        original = subprocess.run(
            ["git", "rev-parse", ":README.md"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        replacement = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=self.root, check=True,
            input=private, capture_output=True,
        ).stdout.decode("ascii").strip()
        subprocess.run(["git", "replace", original, replacement], cwd=self.root, check=True)

        with mock.patch.dict(os.environ, {"GIT_NO_REPLACE_OBJECTS": "0"}):
            with self.assertRaises(build_release.BuildReleaseError):
                self._build("dist-replacement")

        self.assertFalse((self.root / "dist-replacement").exists())

    def test_ref_verifier_accepts_only_exact_annotated_ascii_semver_tag(self) -> None:
        subprocess.run(["git", "config", "user.name", "Synthetic"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "synthetic@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "snapshot"], cwd=self.root, check=True)
        subprocess.run(["git", "tag", "-a", "v0.1.0", "-m", "release"], cwd=self.root, check=True)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()
        output = self.root / "version-output"

        self.assertEqual(
            build_release.verify_release_ref(self.root, "v0.1.0", commit, output),
            "0.1.0",
        )
        self.assertEqual(output.read_text("utf-8"), "version=0.1.0\n")
        for hostile in ("v0.1.0$(touch owned)", 'v0.1.0" --draft', "v０.１.０"):
            with self.subTest(hostile=hostile):
                with self.assertRaises(build_release.BuildReleaseError):
                    build_release.verify_release_ref(self.root, hostile, commit, output)

    def test_release_notes_api_extracts_one_exact_nonempty_section(self) -> None:
        changelog = (
            "# Changes\n\n"
            "## v0.2.0 — Later\n\nLater notes.\n\n"
            "## v0.1.0 — Preview\n\nFirst line.\n\n- Detail\n\n"
            "## v0.0.1 — Earlier\n\nEarlier notes.\n"
        )

        self.assertEqual(
            build_release.extract_release_notes(changelog, "0.1.0"),
            "First line.\n\n- Detail\n",
        )
        for invalid in (
            changelog + "\n## v0.1.0 — Duplicate\n\nDuplicate.\n",
            "# Changes\n\n## v0.1.0 — Empty\n\n## v0.0.1 — Earlier\n",
        ):
            with self.subTest(invalid=invalid[-30:]):
                with self.assertRaises(build_release.BuildReleaseError):
                    build_release.extract_release_notes(invalid, "0.1.0")

    def test_release_notes_cli_reads_only_declared_environment_and_writes_exact_content(self) -> None:
        changelog = self.root / "notes-source.md"
        notes = self.root / "notes-output.md"
        changelog.write_text(
            "# Changes\n\n## v0.1.0 — Preview\n\nPublished body.\n",
            encoding="utf-8",
        )
        environment = {
            "RELEASE_VERSION": "0.1.0",
            "CHANGELOG_PATH": str(changelog),
            "NOTES_PATH": str(notes),
        }

        with mock.patch.dict(os.environ, environment, clear=True):
            exit_code = build_release.main(["--extract-notes"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(notes.read_text("utf-8"), "Published body.\n")

    def test_archive_failure_cleans_staging_and_never_publishes_a_partial_pair(self) -> None:
        with mock.patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("private raw path")):
            with self.assertRaises(build_release.BuildReleaseError):
                self._build("dist")

        self.assertFalse((self.root / "dist").exists())
        self.assertEqual(list(self.root.glob(".dist.release-tmp-*")), [])

    def test_competing_output_is_preserved_and_never_replaced(self) -> None:
        competitor = self.root / "dist"
        competitor.mkdir()
        marker = competitor / "owner.txt"
        marker.write_text("competitor\n", encoding="utf-8")

        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist")

        self.assertEqual(marker.read_text("utf-8"), "competitor\n")
        self.assertEqual(sorted(path.name for path in competitor.iterdir()), ["owner.txt"])

    def test_directory_race_preserves_competitor_and_cleans_owned_ready_directory(self) -> None:
        real_publish = build_release._atomic_publish_directory

        def race(ready: Path, output: Path) -> None:
            output.mkdir()
            (output / "owner.txt").write_text("competitor\n", encoding="utf-8")
            real_publish(ready, output)

        with mock.patch.object(build_release, "_atomic_publish_directory", side_effect=race):
            with self.assertRaisesRegex(build_release.BuildReleaseError, "release publication failed"):
                self._build("dist")

        competitor = self.root / "dist" / "owner.txt"
        self.assertEqual(competitor.read_text("utf-8"), "competitor\n")
        self.assertEqual({path.name for path in (self.root / "dist").iterdir()}, {"owner.txt"})
        self.assertEqual(list(self.root.glob(".dist.release-ready-*")), [])

    def test_owned_ready_cleanup_failure_never_masks_the_primary_publish_error(self) -> None:
        with mock.patch.object(
            build_release,
            "_atomic_publish_directory",
            side_effect=build_release.BuildReleaseError("release publication failed"),
        ):
            with mock.patch.object(build_release.shutil, "rmtree", side_effect=OSError("cleanup failure")):
                with self.assertRaisesRegex(build_release.BuildReleaseError, "release publication failed"):
                    self._build("dist")

    def test_final_output_never_exposes_only_half_of_the_artifact_pair(self) -> None:
        real_link = os.link
        calls = 0
        failures: list[BaseException] = []
        finished = threading.Event()

        def widen_old_half_pair(source: str | bytes, destination: str | bytes, *args: object, **kwargs: object) -> None:
            nonlocal calls
            real_link(source, destination, *args, **kwargs)
            calls += 1
            if calls == 1:
                threading.Event().wait(0.25)

        def build() -> None:
            try:
                self._build("dist")
            except BaseException as error:
                failures.append(error)
            finally:
                finished.set()

        half_visible = False
        with mock.patch.object(build_release.os, "link", side_effect=widen_old_half_pair):
            worker = threading.Thread(target=build)
            worker.start()
            deadline = time.monotonic() + 5
            while not finished.is_set() and time.monotonic() < deadline:
                output = self.root / "dist"
                if output.exists():
                    names = {path.name for path in output.iterdir()}
                    if names != {"pathway-atlas-0.1.0.zip", "SHA256SUMS"}:
                        half_visible = True
                        break
                threading.Event().wait(0.005)
            worker.join(timeout=5)

        self.assertEqual(failures, [])
        self.assertFalse(half_visible)
        self.assertEqual(
            {path.name for path in (self.root / "dist").iterdir()},
            {"pathway-atlas-0.1.0.zip", "SHA256SUMS"},
        )

    def test_atomic_directory_dispatch_uses_each_supported_platform_primitive(self) -> None:
        ready = self.root / "ready"
        output = self.root / "output"
        for platform, helper_name in (
            ("linux", "_linux_rename_noreplace"),
            ("darwin", "_macos_rename_exclusive"),
            ("win32", "_windows_rename_noreplace"),
        ):
            with self.subTest(platform=platform):
                helper = mock.Mock()
                with mock.patch.object(build_release.sys, "platform", platform):
                    with mock.patch.object(build_release, helper_name, helper):
                        build_release._atomic_publish_directory(ready, output)
                helper.assert_called_once_with(ready, output)

    def test_platform_rename_bindings_use_exclusive_atomic_flags(self) -> None:
        class ForeignCall:
            def __init__(self, result: int) -> None:
                self.result = result
                self.calls: list[tuple[object, ...]] = []
                self.argtypes: object = None
                self.restype: object = None

            def __call__(self, *arguments: object) -> int:
                self.calls.append(arguments)
                return self.result

        ready = self.root / "ready"
        output = self.root / "output"

        linux_call = ForeignCall(0)
        linux_library = type("LinuxLibrary", (), {"renameat2": linux_call})()
        with mock.patch.object(build_release, "_load_posix_library", return_value=linux_library):
            build_release._linux_rename_noreplace(ready, output)
        self.assertEqual(
            linux_call.calls,
            [(-100, os.fsencode(ready), -100, os.fsencode(output), 1)],
        )

        macos_call = ForeignCall(0)
        macos_library = type("MacOSLibrary", (), {"renamex_np": macos_call})()
        with mock.patch.object(build_release, "_load_posix_library", return_value=macos_library):
            build_release._macos_rename_exclusive(ready, output)
        self.assertEqual(macos_call.calls, [(os.fsencode(ready), os.fsencode(output), 4)])

        windows_call = ForeignCall(1)
        windows_library = type("WindowsLibrary", (), {"MoveFileExW": windows_call})()
        with mock.patch.object(build_release, "_load_windows_library", return_value=windows_library):
            build_release._windows_rename_noreplace(ready, output)
        self.assertEqual(windows_call.calls, [(str(ready), str(output), 8)])

    def test_unsupported_atomic_directory_platform_fails_closed_and_cleans_ready_directory(self) -> None:
        with mock.patch.object(build_release.sys, "platform", "freebsd14"):
            with self.assertRaisesRegex(build_release.BuildReleaseError, "atomic publication unsupported"):
                self._build("dist")

        self.assertFalse((self.root / "dist").exists())
        self.assertEqual(list(self.root.glob(".dist.release-ready-*")), [])

    def test_missing_atomic_platform_api_fails_closed(self) -> None:
        empty_library = object()
        with mock.patch.object(build_release, "_load_posix_library", return_value=empty_library):
            for helper in (
                build_release._linux_rename_noreplace,
                build_release._macos_rename_exclusive,
            ):
                with self.subTest(helper=helper.__name__):
                    with self.assertRaisesRegex(
                        build_release.BuildReleaseError,
                        "atomic publication unsupported",
                    ):
                        helper(self.root / "ready", self.root / "output")
        with mock.patch.object(build_release, "_load_windows_library", return_value=empty_library):
            with self.assertRaisesRegex(
                build_release.BuildReleaseError,
                "atomic publication unsupported",
            ):
                build_release._windows_rename_noreplace(
                    self.root / "ready",
                    self.root / "output",
                )

    def test_output_rejects_an_intermediate_link_or_reparse_component(self) -> None:
        real = self.root / "real-output"
        real.mkdir()
        alias = self.root / "alias-output"
        try:
            alias.symlink_to(real, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {type(error).__name__}")

        with self.assertRaises(build_release.BuildReleaseError):
            self._build("alias-output/dist")

        self.assertFalse((real / "dist").exists())


if __name__ == "__main__":
    unittest.main()
