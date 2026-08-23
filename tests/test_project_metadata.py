import pathlib
import subprocess
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProjectMetadataTest(unittest.TestCase):
    def test_python_floor_and_optional_dependencies(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

        self.assertEqual(data["project"]["name"], "shengxue-skill")
        self.assertEqual(data["project"]["version"], "0.1.0")
        self.assertEqual(data["project"]["requires-python"], ">=3.10")
        self.assertEqual(data["project"].get("dependencies", []), [])

        extras = data["project"]["optional-dependencies"]
        self.assertIn("python-docx", " ".join(extras["documents"]))
        self.assertIn("openpyxl", " ".join(extras["spreadsheets"]))
        self.assertIn("pdfplumber", " ".join(extras["pdf"]))
        self.assertIn("coverage", " ".join(extras["test"]))
        self.assertTrue(
            any("tomli" in requirement for requirement in extras["test"]),
            "Python 3.10 must have a tomli fallback in the test extra",
        )

    def test_setuptools_build_and_scripts_package_discovery_are_declared(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

        self.assertEqual(
            data["build-system"]["build-backend"], "setuptools.build_meta"
        )
        self.assertIn("setuptools", data["build-system"]["requires"][0])
        self.assertEqual(
            data["tool"]["setuptools"]["packages"]["find"]["include"],
            ["scripts*"],
        )

    def test_sensitive_runtime_paths_are_ignored_by_git(self):
        candidates = (
            "output/report.md",
            "work/session.json",
            ".cache/response.json",
            "data/hubei/official.csv",
            ".worktrees/feature/README.md",
            "__pycache__/module.pyc",
            ".venv/pyvenv.cfg",
            ".env.local",
            "credentials.env",
            "private/key.pem",
            "private/certificate.crt",
            "local.sqlite3",
            "reports/generated.docx",
            "reports/generated.md",
            "evidence/raw-downloads/page.html",
        )

        for candidate in candidates:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", "--", candidate],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"git does not ignore {candidate!r}: {result.stderr.strip()}",
            )


if __name__ == "__main__":
    unittest.main()
