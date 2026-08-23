import ast
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_OR_UNREVIEWED_PATHS = (
    "scripts/export_from_system.py",
    "scripts/parity_check.py",
    "scripts/fetch_via_qr.py",
    "data/hubei",
    "output",
    "scripts/build_template.py",
    "templates/方案模板.docx",
    "tests/test_docx_export.py",
    "scripts/verify_province.py",
    "tests/test_province_onboarding.py",
    "data/demo-xx",
    "scripts/estimate_rank.py",
    "tests/test_rank_estimation.py",
)

FORBIDDEN_MODULE_BASENAMES = frozenset(
    Path(relative).stem.casefold()
    for relative in PRIVATE_OR_UNREVIEWED_PATHS
    if relative.startswith("scripts/") and relative.endswith(".py")
)
FORBIDDEN_PATH_FRAGMENTS = tuple(
    relative.replace("\\", "/").casefold().strip("/")
    for relative in PRIVATE_OR_UNREVIEWED_PATHS
)


def _is_forbidden_path_reference(value: str) -> bool:
    normalized = value.replace("\\", "/").casefold().strip()
    haystack = "/" + normalized.strip("/")
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        needle = "/" + fragment
        # The renderer may create a fresh ignored output directory.  What the
        # public boundary forbids is a bundled/read-back artifact beneath it.
        if fragment == "output" and haystack == needle:
            continue
        if haystack == needle or haystack.endswith(needle):
            return True
        if needle + "/" in haystack:
            return True
    return False


def _runtime_references(path: Path) -> tuple[str, ...]:
    """Return executable references to private siblings or the old template.

    Comments and statement docstrings are deliberately outside this check: they
    may preserve attribution without creating a runtime dependency.
    """

    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    references: list[str] = []

    class ReferenceVisitor(ast.NodeVisitor):
        def visit_Expr(self, node: ast.Expr) -> None:
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._record_import(alias.name)
                self._record(alias.name)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            self._record_import(module)
            self._record(module)
            if module.casefold() in {"", "scripts"}:
                for alias in node.names:
                    self._record_import(alias.name)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str):
                self._record(node.value)

        def _record(self, value: str) -> None:
            normalized = value.replace("\\", "/").casefold()
            if "shengxue-system" in normalized:
                references.append(value)
            if _is_forbidden_path_reference(value):
                references.append(value)
            if normalized in FORBIDDEN_MODULE_BASENAMES:
                references.append(value)
            if normalized.startswith("scripts.") and (
                normalized.rsplit(".", 1)[-1] in FORBIDDEN_MODULE_BASENAMES
            ):
                references.append(value)

        def _record_import(self, module: str) -> None:
            basename = module.casefold().rsplit(".", 1)[-1]
            if basename in FORBIDDEN_MODULE_BASENAMES:
                references.append(module)

    ReferenceVisitor().visit(tree)
    return tuple(dict.fromkeys(references))


def _public_python_files():
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if path == Path(__file__).resolve():
            continue
        if relative.parts[0] in {".git", ".scratch", ".superpowers", ".worktrees"}:
            continue
        if relative.parts[0] == "tests":
            continue
        yield path


class PublicPackageBoundaryTest(unittest.TestCase):
    def test_ast_boundary_detects_package_imports_and_dynamic_legacy_paths(self):
        source = '''
import scripts.verify_province
from scripts import fetch_via_qr

rank_cli = "scripts/estimate_rank.py"
private_export = "scripts/export_from_system.py"
parity = "scripts/parity_check.py"
real_data = "data/hubei/province.json"
old_demo = "data/demo-xx/province.json"
generated = "output/report.md"
'''
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.py"
            path.write_text(source, encoding="utf-8")
            findings = _runtime_references(path)

        for expected in (
            "scripts.verify_province",
            "fetch_via_qr",
            "scripts/estimate_rank.py",
            "scripts/export_from_system.py",
            "scripts/parity_check.py",
            "data/hubei/province.json",
            "data/demo-xx/province.json",
            "output/report.md",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, findings)

    def test_ast_boundary_allows_historical_attribution_docstrings(self):
        source = '''"""Migrated from shengxue-system for attribution only."""\n'''
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.py"
            path.write_text(source, encoding="utf-8")
            findings = _runtime_references(path)

        self.assertEqual(findings, ())

    def test_private_and_unreviewed_runtime_artifacts_are_absent(self):
        present = [
            relative
            for relative in PRIVATE_OR_UNREVIEWED_PATHS
            if os.path.lexists(ROOT / relative)
        ]
        self.assertEqual(present, [], f"public tree still contains: {present}")

    def test_public_python_has_no_executable_private_or_template_lookup(self):
        findings = {
            path.relative_to(ROOT).as_posix(): references
            for path in _public_python_files()
            if (references := _runtime_references(path))
        }
        self.assertEqual(findings, {})

    def test_both_province_modes_remain_available_as_synthetic_fixtures(self):
        fixture_roots = (
            ROOT / "tests/fixtures/provinces/demo-312",
            ROOT / "tests/fixtures/provinces/demo-33",
        )
        modes = []
        for fixture_root in fixture_roots:
            metadata_path = fixture_root / "province.json"
            self.assertTrue(metadata_path.is_file(), str(metadata_path))
            metadata = json.loads(metadata_path.read_text("utf-8"))
            self.assertTrue(metadata["province"].startswith("演示"), metadata)
            self.assertTrue((fixture_root / "yifenyiduan.csv").is_file())
            self.assertTrue((fixture_root / "tou_dang.csv").is_file())
            with (fixture_root / "tou_dang.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertTrue(
                all(row["school_code"].startswith("SYN") for row in rows), rows
            )
            self.assertTrue(
                all(row["school_name"].startswith("虚构") for row in rows), rows
            )
            modes.append(metadata["mode"])

        self.assertEqual(modes, ["3+1+2", "3+3"])


if __name__ == "__main__":
    unittest.main()
