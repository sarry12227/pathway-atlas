import pathlib
import os
import json
import subprocess
import tempfile
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProjectMetadataTest(unittest.TestCase):
    def test_git_attributes_force_lf_text_and_explicit_binary_modes(self):
        text = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", "README.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        self.assertIn("README.md: text: auto", text)
        self.assertIn("README.md: eol: lf", text)

        for path in (
            "fixture.xlsx",
            "fixture.pdf",
            "fixture.docx",
            "fixture.png",
            "fixture.jpg",
            "fixture.jpeg",
            "fixture.zip",
        ):
            with self.subTest(path=path):
                attributes = subprocess.run(
                    ["git", "check-attr", "text", "--", path],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                ).stdout.strip()
                self.assertEqual(attributes, f"{path}: text: unset")

    def test_autocrlf_windows_style_checkout_keeps_text_lf_and_binary_bytes_exact(self):
        text_bytes = b"first line\nsecond line\n"
        binary_bytes = b"%PDF-1.7\r\n\x00synthetic\r\n"
        with tempfile.TemporaryDirectory() as temporary:
            repo = pathlib.Path(temporary)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
            (repo / "sample.txt").write_bytes(text_bytes)
            (repo / "sample.pdf").write_bytes(binary_bytes)
            subprocess.run(
                ["git", "-c", "core.autocrlf=false", "add", "--", ".gitattributes", "sample.txt", "sample.pdf"],
                cwd=repo,
                check=True,
            )
            checkout = repo / "checkout"
            checkout.mkdir()
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.autocrlf=true",
                    "-c",
                    "core.eol=crlf",
                    "checkout-index",
                    "--all",
                    "--force",
                    f"--prefix=checkout{os.sep}",
                ],
                cwd=repo,
                check=True,
            )

            checked_text = (checkout / "sample.txt").read_bytes()
            checked_binary = (checkout / "sample.pdf").read_bytes()

        self.assertEqual(checked_text, text_bytes)
        self.assertNotIn(b"\r\n", checked_text)
        self.assertEqual(checked_binary, binary_bytes)

    def test_python_floor_and_optional_dependencies(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))

        self.assertEqual(data["project"]["name"], "pathway-atlas")
        self.assertEqual(data["project"]["version"], "0.1.0")
        self.assertEqual(data["project"]["requires-python"], ">=3.10")
        self.assertEqual(data["project"].get("dependencies", []), [])

        extras = data["project"]["optional-dependencies"]
        self.assertIn("python-docx", " ".join(extras["documents"]))
        self.assertIn("openpyxl", " ".join(extras["spreadsheets"]))
        self.assertIn("pdfplumber", " ".join(extras["pdf"]))
        self.assertIn("coverage", " ".join(extras["test"]))
        self.assertIn("PyYAML>=6,<7", extras["test"])
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

    def test_public_runtime_brand_identifiers_are_current(self):
        downloader = (ROOT / "scripts" / "downloader.py").read_text("utf-8")
        docx_export = (ROOT / "scripts" / "docx_export.py").read_text("utf-8")
        self.assertIn('"pathway-atlas-downloader/0.1"', downloader)
        self.assertIn("pip install 'pathway-atlas[documents]'", docx_export)

        schema_paths = sorted((ROOT / "schemas").glob("*.schema.json"))
        self.assertGreaterEqual(len(schema_paths), 6)
        identifiers = []
        for path in schema_paths:
            schema = json.loads(path.read_text(encoding="utf-8"))
            identifier = schema.get("$id")
            if identifier is not None:
                identifiers.append((path.name, identifier))
        self.assertGreaterEqual(len(identifiers), 6)
        old_slug = "shengxue" + "-skill"
        for name, identifier in identifiers:
            with self.subTest(schema=name):
                self.assertIn("pathway-atlas", identifier)
                self.assertNotIn(old_slug, identifier)

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
