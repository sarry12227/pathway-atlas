from __future__ import annotations

import hashlib
import io
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
        (self.root / "SKILL.md").write_text("# Synthetic skill\n", encoding="utf-8")
        (self.root / "README.md").write_text("# Synthetic repository\n", encoding="utf-8")
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
            self.assertIn("shengxue-skill/SKILL.md", names)
            self.assertIn("shengxue-skill/README.md", names)
            self.assertIn(
                "shengxue-skill/tests/fixtures/synthetic/rows.csv",
                names,
            )
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(info.create_system, 3)
                self.assertIn((info.external_attr >> 16) & 0o777, (0o644, 0o755))

    def test_archive_uses_index_blobs_and_ignores_untracked_or_modified_worktree_content(self) -> None:
        (self.root / "README.md").write_text("uncommitted replacement\n", encoding="utf-8")
        (self.root / "untracked-secret.txt").write_text("not packaged\n", encoding="utf-8")

        artifact = self._build("dist")

        with zipfile.ZipFile(artifact.archive) as archive:
            self.assertEqual(
                archive.read("shengxue-skill/README.md"),
                b"# Synthetic repository\n",
            )
            self.assertNotIn("shengxue-skill/untracked-secret.txt", archive.namelist())

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

    def test_archive_failure_cleans_staging_and_never_publishes_a_partial_pair(self) -> None:
        with mock.patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("private raw path")):
            with self.assertRaises(build_release.BuildReleaseError):
                self._build("dist")

        self.assertFalse((self.root / "dist").exists())
        self.assertEqual(list(self.root.glob(".dist.release-tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
