from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
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
            '[project]\nname = "shengxue-skill"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        for document in (
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "DATA_SOURCES.md",
            "LICENSE",
            "README.md",
            "SECURITY.md",
            "SKILL.md",
        ):
            (self.root / document).write_text(f"# Synthetic {document}\n", encoding="utf-8")
        (self.root / "release-policy.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "binary_extensions": [".png"],
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
                    "future_release_paths": [],
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
            f"{digest}  shengxue-skill-0.1.0.zip\n",
        )
        with zipfile.ZipFile(first.archive) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            required = {
                "shengxue-skill/CHANGELOG.md",
                "shengxue-skill/CONTRIBUTING.md",
                "shengxue-skill/DATA_SOURCES.md",
                "shengxue-skill/LICENSE",
                "shengxue-skill/README.md",
                "shengxue-skill/SECURITY.md",
                "shengxue-skill/SKILL.md",
                "shengxue-skill/pyproject.toml",
                "shengxue-skill/release-policy.json",
                "shengxue-skill/tests/fixtures/synthetic/rows.csv",
            }
            self.assertTrue(required <= set(names))
            self.assertIn(
                "shengxue-skill/tests/fixtures/synthetic/rows.csv",
                names,
            )
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
                archive.read("shengxue-skill/README.md"),
                b"# Synthetic README.md\n",
            )
            self.assertNotIn("shengxue-skill/untracked-secret.txt", archive.namelist())

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

        subprocess.run(["git", "rm", "--cached", "--", "SECURITY.md"], cwd=self.root, check=True)
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-missing-doc")
        subprocess.run(["git", "add", "--", "SECURITY.md"], cwd=self.root, check=True)
        subprocess.run(["git", "rm", "-r", "--cached", "--", "tests/fixtures"], cwd=self.root, check=True)
        with self.assertRaises(build_release.BuildReleaseError):
            self._build("dist-missing-fixture")

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

        self.assertIn("shengxue-skill/SKILL.md", self._zip_names(artifact.archive))

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

    def test_link_race_preserves_competitor_and_reports_primary_publication_error(self) -> None:
        real_link = os.link

        def race(source: str | bytes, destination: str | bytes, *args: object, **kwargs: object) -> None:
            target = Path(destination)
            target.write_text("competitor\n", encoding="utf-8")
            real_link(source, destination, *args, **kwargs)

        with mock.patch.object(build_release.os, "link", side_effect=race):
            with self.assertRaisesRegex(build_release.BuildReleaseError, "release publication failed"):
                self._build("dist")

        competitor = self.root / "dist" / "shengxue-skill-0.1.0.zip"
        self.assertEqual(competitor.read_text("utf-8"), "competitor\n")
        self.assertFalse((self.root / "dist" / "SHA256SUMS").exists())

    def test_owned_cleanup_failure_never_masks_the_primary_link_error(self) -> None:
        real_link = os.link
        calls = 0

        def fail_second_link(source: str | bytes, destination: str | bytes, *args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("primary link failure")
            real_link(source, destination, *args, **kwargs)

        with mock.patch.object(build_release.os, "link", side_effect=fail_second_link):
            with mock.patch.object(Path, "unlink", side_effect=OSError("cleanup failure")):
                with self.assertRaisesRegex(build_release.BuildReleaseError, "release publication failed"):
                    self._build("dist")

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
