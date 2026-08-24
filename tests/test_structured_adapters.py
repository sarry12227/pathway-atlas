from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, replace
import http.client
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile

from scripts.adapters import (
    CellStatus,
    ColumnMapping,
    ExtractedCoverage,
    ExtractedRow,
    ExtractedTable,
    MappingError,
    StructuredFileError,
    StructuredValidationError,
)
from scripts.adapters.html_table import extract_html_table
from scripts.adapters.spreadsheet import (
    SpreadsheetDependencyError,
    extract_spreadsheet,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "replay" / "structured"
HTML_FIXTURE = (FIXTURES / "score-table.html").resolve()
XLSX_FIXTURE = (FIXTURES / "admission.xlsx").resolve()


class _RepeatedCanonicalMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        return "分数"

    def __iter__(self) -> Iterator[str]:
        return iter(("score",))

    def __len__(self) -> int:
        return 1

    def items(self):
        return (("score", "分数"), ("score", "总分"))


class ContractTest(unittest.TestCase):
    def test_coverage_normalizes_mathematical_integers_and_rejects_invalid_bounds(self):
        coverage = ExtractedCoverage(
            min_score=630.0,
            max_score=650,
            min_rank=100.0,
            max_rank=300,
        )
        self.assertEqual(
            coverage.to_dict(),
            {"min_score": 630, "max_score": 650, "min_rank": 100, "max_rank": 300},
        )
        for value in (True, 1.5, math.nan, math.inf):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    ExtractedCoverage(min_score=value)
        with self.assertRaises(ValueError):
            ExtractedCoverage(min_score=651, max_score=650)
        with self.assertRaises(ValueError):
            ExtractedCoverage(min_rank=301, max_rank=300)

    def test_rows_are_deep_snapshots_and_json_safe(self):
        raw_values = {"score": 650, "meta": {"tags": ["synthetic"]}}
        raw_status = {"score": "exact", "meta": CellStatus.EXACT}
        raw_warnings = ["kept-in-order"]
        row = ExtractedRow(
            values=raw_values,
            cell_status=raw_status,
            location="table[1]/tbody/tr[1]",
            confidence=1,
            warnings=raw_warnings,
        )
        raw_values["score"] = 1
        raw_values["meta"]["tags"].append("mutated")
        raw_status["score"] = "invalid"
        raw_warnings.append("mutated")
        self.assertEqual(row.values["score"], 650)
        self.assertEqual(row.values["meta"]["tags"], ("synthetic",))
        self.assertEqual(row.cell_status["score"], CellStatus.EXACT)
        self.assertEqual(row.warnings, ("kept-in-order",))
        with self.assertRaises(TypeError):
            row.values["score"] = 2
        with self.assertRaises(FrozenInstanceError):
            row.location = "changed"
        json.dumps(row.to_dict(), allow_nan=False, ensure_ascii=False)

    def test_direct_construction_and_replace_revalidate_every_invariant(self):
        row = ExtractedRow(
            values={"score": 650},
            cell_status={"score": "exact"},
            location="table[1]/tbody/tr[1]",
            confidence=1.0,
        )
        with self.assertRaises(ValueError):
            replace(row, cell_status={"score": "official"})
        with self.assertRaises(ValueError):
            replace(row, cell_status={"other": "exact"})
        with self.assertRaises(ValueError):
            replace(row, confidence=math.nan)
        with self.assertRaises(TypeError):
            replace(row, values={"score": object()})

    def test_table_snapshots_rows_and_never_serializes_a_local_path(self):
        rows = [
            ExtractedRow(
                values={"score": 650},
                cell_status={"score": "exact"},
                location="table[1]/tbody/tr[1]",
                confidence=1,
            )
        ]
        table = ExtractedTable(
            table_id="table[1]",
            caption="合成表",
            sheet=None,
            rows=rows,
            coverage=ExtractedCoverage(min_score=650, max_score=650),
            warnings=["synthetic"],
            extraction_method="html-table",
        )
        rows.clear()
        self.assertEqual(len(table.rows), 1)
        serialized = json.dumps(table.to_dict(), allow_nan=False, ensure_ascii=False)
        self.assertNotIn(str(ROOT), serialized)
        with self.assertRaises(ValueError):
            replace(table, extraction_method="")

    def test_mapping_is_an_immutable_deep_snapshot_with_explicit_roles_and_scale(self):
        aliases = ["最低分", "投档分"]
        columns = {"min_score": aliases, "min_rank": "最低位次"}
        roles = {"min_score": "score", "min_rank": "rank"}
        mapping = ColumnMapping(columns, roles=roles, score_scale=[0, 750])
        aliases.append("changed")
        columns["other"] = "其他"
        roles["min_score"] = "rank"
        self.assertEqual(mapping["min_score"], ("最低分", "投档分"))
        self.assertEqual(tuple(mapping), ("min_score", "min_rank"))
        self.assertEqual(mapping.roles["min_score"], "score")
        self.assertEqual(mapping.score_scale, (0, 750))
        with self.assertRaises(TypeError):
            mapping.columns["other"] = ("其他",)

    def test_mapping_rejects_empty_duplicate_whitespace_and_repeated_fields(self):
        invalid = (
            {"score": []},
            {" score": "分数"},
            {"score": " 分数"},
            {"score": ("分数", "分数")},
            {"score": "值", "rank": "值"},
            _RepeatedCanonicalMapping(),
        )
        for columns in invalid:
            with self.subTest(columns=columns):
                with self.assertRaises((TypeError, ValueError)):
                    ColumnMapping(columns)
        with self.assertRaises((TypeError, ValueError)):
            ColumnMapping({"score": "分数"}, score_scale={"lower": 0, "upper": 750})


class HtmlAdapterTest(unittest.TestCase):
    @staticmethod
    def mapping() -> ColumnMapping:
        return ColumnMapping(
            {"score": "分数", "rank": ("累计位次", "位次"), "note": "说明"},
            roles={"score": "score", "rank": "rank"},
            score_scale=(0, 750),
        )

    def _extract(self, **overrides) -> ExtractedTable:
        arguments = {
            "table_index": 1,
            "expected_caption": "合成分数位次表",
            "mapping": self.mapping(),
        }
        arguments.update(overrides)
        return extract_html_table(HTML_FIXTURE, **arguments)

    def _write_html(self, directory: Path, text: str, name: str = "input.html") -> Path:
        path = (directory / name).resolve()
        path.write_text(text, encoding="utf-8")
        return path

    def test_explicit_table_caption_mapping_location_values_and_coverage(self):
        table = self._extract()
        self.assertEqual(table.table_id, "table[1]")
        self.assertEqual(table.caption, "合成分数位次表")
        self.assertIsNone(table.sheet)
        self.assertEqual(table.extraction_method, "html-table")
        self.assertEqual(table.rows[0].location, "table[1]/tbody/tr[1]")
        self.assertEqual(table.rows[0].values, {"score": 650, "rank": 100, "note": "纯合成甲"})
        self.assertEqual(table.rows[0].cell_status["score"], CellStatus.EXACT)
        self.assertEqual(
            table.coverage,
            ExtractedCoverage(min_score=630, max_score=650, min_rank=100, max_rank=300),
        )
        self.assertEqual(len(table.rows), 3, "script payload must not become a table or row")

    def test_wrong_index_caption_and_unrelated_table_fail_without_auto_selection(self):
        with self.assertRaises(StructuredValidationError):
            self._extract(table_index=3)
        with self.assertRaises(StructuredValidationError):
            self._extract(expected_caption="别的标题")
        with self.assertRaises(MappingError):
            self._extract(table_index=2, expected_caption="无关表")

    def test_missing_ambiguous_duplicate_and_whitespace_only_headers_are_rejected(self):
        with self.assertRaises(MappingError):
            self._extract(mapping={"score": "不存在"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases = {
                "duplicate.html": "<table><caption>x</caption><tr><th>分数</th><th>分数</th></tr></table>",
                "ambiguous.html": "<table><caption>x</caption><tr><th>分数</th><th>总分</th></tr></table>",
                "whitespace.html": "<table><caption>x</caption><tr><th> 分数 </th></tr></table>",
            }
            mappings = {
                "duplicate.html": {"score": "分数"},
                "ambiguous.html": {"score": ("分数", "总分")},
                "whitespace.html": {"score": "分数"},
            }
            for name, source in cases.items():
                path = self._write_html(directory, source, name)
                with self.subTest(name=name):
                    with self.assertRaises(MappingError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping=mappings[name])

    def test_malformed_table_fails_and_truncated_or_empty_rows_warn(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            malformed = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>名称</th></tr><tr><td>未闭合",
                "malformed.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(malformed, table_index=1, expected_caption="x", mapping={"name": "名称"})

            truncated = self._write_html(
                directory,
                "<table><caption>x</caption><thead><tr><th>名称</th><th>备注</th></tr></thead>"
                "<tbody><tr><td>合成行</td></tr><tr><td></td><td></td></tr></tbody></table>",
                "truncated.html",
            )
            table = extract_html_table(
                truncated,
                table_index=1,
                expected_caption="x",
                mapping={"name": "名称", "note": "备注"},
            )
            self.assertEqual(table.rows[0].cell_status["note"], CellStatus.EMPTY)
            self.assertIn("truncated-row", table.rows[0].warnings)
            self.assertEqual(table.rows[1].cell_status["name"], CellStatus.EMPTY)
            self.assertIn("empty-required-cell:name", table.rows[1].warnings)

            empty = self._write_html(
                directory,
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead><tbody></tbody></table>",
                "empty.html",
            )
            empty_table = extract_html_table(empty, table_index=1, expected_caption="x", mapping={"name": "名称"})
            self.assertEqual(empty_table.rows, ())
            self.assertIn("empty-table", empty_table.warnings)

    def test_numeric_scale_and_monotonicity_fail_closed_without_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mapping = ColumnMapping(
                {"score": "分数", "rank": "位次"},
                roles={"score": "score", "rank": "rank"},
                score_scale=(0, 750),
            )
            nonmonotonic = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>100</td></tr><tr><td>640</td><td>200</td></tr>"
                "<tr><td>645</td><td>300</td></tr></table>",
                "nonmonotonic.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(nonmonotonic, table_index=1, expected_caption="x", mapping=mapping)
            outside = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>751</td><td>1</td></tr></table>",
                "outside.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(outside, table_index=1, expected_caption="x", mapping=mapping)
            invalid_rank = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>1.5</td></tr></table>",
                "rank.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(invalid_rank, table_index=1, expected_caption="x", mapping=mapping)
            for name, score in (("nan.html", "NaN"), ("inf.html", "Infinity")):
                invalid = self._write_html(
                    directory,
                    "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                    f"<tr><td>{score}</td><td>1</td></tr></table>",
                    name,
                )
                with self.subTest(name=name):
                    with self.assertRaises(StructuredValidationError):
                        extract_html_table(invalid, table_index=1, expected_caption="x", mapping=mapping)
            duplicate = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>1</td></tr><tr><td>650</td><td>1</td></tr></table>",
                "duplicate-row.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(duplicate, table_index=1, expected_caption="x", mapping=mapping)

    def test_masked_numeric_cells_remain_nonexact_and_do_not_fabricate_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_html(
                Path(temporary),
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650以上</td><td>前100</td></tr></table>",
            )
            table = extract_html_table(
                path,
                table_index=1,
                expected_caption="x",
                mapping=ColumnMapping(
                    {"score": "分数", "rank": "位次"},
                    roles={"score": "score", "rank": "rank"},
                    score_scale=(0, 750),
                ),
            )
            self.assertIsNone(table.rows[0].values["score"])
            self.assertEqual(table.rows[0].cell_status["score"], CellStatus.MASKED)
            self.assertEqual(table.coverage, ExtractedCoverage())


class FileBoundaryTest(unittest.TestCase):
    def test_html_requires_absolute_exact_suffix_regular_bounded_non_url_file(self):
        with self.assertRaises(StructuredFileError):
            extract_html_table("tests/fixture.html", table_index=1, expected_caption="x", mapping={"x": "x"})
        for value in ("https://example.test/table.html", "file:///tmp/table.html"):
            with self.subTest(value=value):
                with self.assertRaises(StructuredFileError):
                    extract_html_table(value, table_index=1, expected_caption="x", mapping={"x": "x"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            with self.assertRaises(StructuredFileError):
                extract_html_table(directory, table_index=1, expected_caption="x", mapping={"x": "x"})
            wrong = directory / "input.HTML"
            wrong.write_text("<table></table>", encoding="utf-8")
            with self.assertRaises(StructuredFileError):
                extract_html_table(wrong, table_index=1, expected_caption="x", mapping={"x": "x"})
            oversized = directory / "large.html"
            oversized.write_bytes(b"x" * 32)
            with mock.patch("scripts.adapters.MAX_FILE_BYTES", 16):
                with self.assertRaises(StructuredFileError):
                    extract_html_table(oversized, table_index=1, expected_caption="x", mapping={"x": "x"})

    def test_parent_traversal_and_same_name_replacement_fail_closed_without_path_leak(self):
        traversal = str(HTML_FIXTURE.parent / ".." / "structured" / HTML_FIXTURE.name)
        with self.assertRaises(StructuredFileError):
            extract_html_table(traversal, table_index=1, expected_caption="x", mapping={"x": "x"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            target = directory / "race.html"
            target.write_text("<table><caption>x</caption><tr><th>x</th></tr></table>", encoding="utf-8")
            replacement = directory / "replacement.html"
            replacement.write_text("<table><caption>y</caption><tr><th>y</th></tr></table>", encoding="utf-8")
            original_open = Path.open

            def replacing_open(candidate, *args, **kwargs):
                if candidate == target:
                    os.replace(replacement, target)
                return original_open(candidate, *args, **kwargs)

            with mock.patch.object(Path, "open", replacing_open):
                with self.assertRaises(StructuredFileError) as raised:
                    extract_html_table(target, table_index=1, expected_caption="x", mapping={"x": "x"})
            self.assertNotIn(str(directory), str(raised.exception))

    def test_symlink_or_reparse_input_is_rejected_when_platform_can_create_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            link = directory / "linked.html"
            try:
                os.symlink(HTML_FIXTURE, link)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {type(error).__name__}")
            with self.assertRaises(StructuredFileError):
                extract_html_table(link, table_index=1, expected_caption="x", mapping={"x": "x"})

    def test_resolved_path_drift_from_parent_reparse_fails_closed(self):
        redirected = HTML_FIXTURE.parent.parent / "redirected" / HTML_FIXTURE.name
        with mock.patch.object(Path, "resolve", return_value=redirected):
            with self.assertRaises(StructuredFileError):
                extract_html_table(
                    HTML_FIXTURE,
                    table_index=1,
                    expected_caption="合成分数位次表",
                    mapping=HtmlAdapterTest.mapping(),
                )


class SpreadsheetAdapterTest(unittest.TestCase):
    @staticmethod
    def mapping() -> ColumnMapping:
        return ColumnMapping(
            {
                "school": "院校",
                "group": "专业组",
                "min_score": "最低分",
                "min_rank": "最低位次",
                "plan_count": "计划数",
                "note": "备注",
            },
            roles={"min_score": "score", "min_rank": "rank"},
            score_scale=(0, 750),
        )

    def _mutated_fixture(self, directory: Path, name: str, mutate) -> Path:
        output = (directory / name).resolve()
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        ET.register_namespace("x", namespace)
        with zipfile.ZipFile(XLSX_FIXTURE) as source, zipfile.ZipFile(output, "w") as destination:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    root = ET.fromstring(payload)
                    mutate(root, namespace)
                    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                destination.writestr(item, payload)
        return output

    def test_exact_sheet_header_location_formula_merge_empty_and_coverage(self):
        table = extract_spreadsheet(XLSX_FIXTURE, sheet="物理类", mapping=self.mapping())
        self.assertEqual(table.table_id, "sheet:物理类")
        self.assertEqual(table.sheet, "物理类")
        self.assertIsNone(table.caption)
        self.assertEqual(table.extraction_method, "xlsx-worksheet")
        self.assertEqual(table.rows[0].location, "物理类!A2:F2")
        self.assertEqual(table.rows[0].values["school"], "虚构大学甲")
        self.assertEqual(table.rows[0].values["min_score"], 620)
        self.assertEqual(table.rows[0].cell_status["min_score"], CellStatus.EXACT)
        self.assertEqual(table.rows[1].cell_status["min_score"], CellStatus.FORMULA)
        self.assertIn("formula-cell:min_score", table.rows[1].warnings)
        self.assertEqual(table.rows[2].cell_status["group"], CellStatus.MERGED)
        self.assertEqual(table.rows[2].cell_status["plan_count"], CellStatus.EMPTY)
        self.assertIn("merged-cell:group", table.rows[2].warnings)
        self.assertIn("empty-required-cell:plan_count", table.rows[2].warnings)
        self.assertEqual(
            table.coverage,
            ExtractedCoverage(min_score=620, max_score=620, min_rank=1200, max_rank=1200),
        )
        self.assertIn("coverage-excludes-nonexact-rows", table.warnings)

    def test_exact_sheet_selection_never_falls_back_to_unrelated_sheet(self):
        with self.assertRaises(StructuredValidationError):
            extract_spreadsheet(XLSX_FIXTURE, sheet="不存在", mapping=self.mapping())
        with self.assertRaises(MappingError):
            extract_spreadsheet(XLSX_FIXTURE, sheet="说明", mapping=self.mapping())

    def test_duplicate_and_whitespace_only_xlsx_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def duplicate(root, namespace):
                cell = root.find(f".//{{{namespace}}}c[@r='B1']/{{{namespace}}}v")
                cell.text = "院校"

            def whitespace(root, namespace):
                cell = root.find(f".//{{{namespace}}}c[@r='C1']/{{{namespace}}}v")
                cell.text = " 最低分 "

            for name, mutation in (("duplicate.xlsx", duplicate), ("whitespace.xlsx", whitespace)):
                path = self._mutated_fixture(directory, name, mutation)
                with self.subTest(name=name):
                    with self.assertRaises(MappingError):
                        extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())

    def test_hidden_truncated_and_formula_only_structures_remain_nonexact_with_warnings(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def hide_header(root, namespace):
                root.find(f".//{{{namespace}}}row[@r='1']").set("hidden", "1")

            hidden_header = self._mutated_fixture(directory, "hidden-header.xlsx", hide_header)
            hidden_header_table = extract_spreadsheet(hidden_header, sheet="物理类", mapping=self.mapping())
            self.assertIn("hidden-header-row", hidden_header_table.warnings)

            def hide_data(root, namespace):
                root.find(f".//{{{namespace}}}row[@r='2']").set("hidden", "1")

            hidden_data = self._mutated_fixture(directory, "hidden-data.xlsx", hide_data)
            hidden_table = extract_spreadsheet(hidden_data, sheet="物理类", mapping=self.mapping())
            self.assertEqual(hidden_table.rows[0].cell_status["min_score"], CellStatus.UNCERTAIN)
            self.assertIn("hidden-row", hidden_table.rows[0].warnings)
            self.assertEqual(hidden_table.coverage, ExtractedCoverage())

            def truncate_and_add_formula(root, namespace):
                sheet_data = root.find(f"{{{namespace}}}sheetData")
                row4 = sheet_data.find(f"{{{namespace}}}row[@r='4']")
                row4.remove(row4.find(f"{{{namespace}}}c[@r='F4']"))
                row5 = ET.SubElement(sheet_data, f"{{{namespace}}}row", {"r": "5"})
                cell = ET.SubElement(row5, f"{{{namespace}}}c", {"r": "C5", "t": "n"})
                ET.SubElement(cell, f"{{{namespace}}}f").text = "C2-20"
                ET.SubElement(cell, f"{{{namespace}}}v").text = "600"

            altered = self._mutated_fixture(directory, "truncated-formula-only.xlsx", truncate_and_add_formula)
            altered_table = extract_spreadsheet(altered, sheet="物理类", mapping=self.mapping())
            self.assertIn("truncated-row", altered_table.rows[2].warnings)
            self.assertEqual(altered_table.rows[3].location, "物理类!A5:F5")
            self.assertEqual(altered_table.rows[3].cell_status["min_score"], CellStatus.FORMULA)
            self.assertIn("formula-cell:min_score", altered_table.rows[3].warnings)

    def test_xlsx_numeric_bool_nonintegral_rank_scale_and_monotonicity_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def bool_score(root, namespace):
                cell = root.find(f".//{{{namespace}}}c[@r='C2']")
                cell.set("t", "b")
                cell.find(f"{{{namespace}}}v").text = "1"

            def nonintegral_rank(root, namespace):
                root.find(f".//{{{namespace}}}c[@r='D2']/{{{namespace}}}v").text = "1.5"

            def outside_scale(root, namespace):
                root.find(f".//{{{namespace}}}c[@r='C2']/{{{namespace}}}v").text = "751"

            for name, mutation in (
                ("bool.xlsx", bool_score),
                ("rank.xlsx", nonintegral_rank),
                ("scale.xlsx", outside_scale),
            ):
                path = self._mutated_fixture(directory, name, mutation)
                with self.subTest(name=name):
                    with self.assertRaises(StructuredValidationError):
                        extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())

            def nonmonotonic(root, namespace):
                row3_score = root.find(f".//{{{namespace}}}c[@r='C3']")
                formula = row3_score.find(f"{{{namespace}}}f")
                row3_score.remove(formula)
                row3_score.find(f"{{{namespace}}}v").text = "621"

            path = self._mutated_fixture(directory, "nonmonotonic.xlsx", nonmonotonic)
            with self.assertRaises(StructuredValidationError):
                extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())

    def test_fixture_binary_independently_contains_expected_structure_formula_merge_and_style(self):
        with zipfile.ZipFile(XLSX_FIXTURE) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet_names = [item.attrib["name"] for item in workbook.findall("m:sheets/m:sheet", ns)]
            self.assertEqual(sheet_names, ["物理类", "说明"])
            worksheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            formulas = [item.text for item in worksheet.findall(".//m:f", ns)]
            merges = [item.attrib["ref"] for item in worksheet.findall("m:mergeCells/m:mergeCell", ns)]
            styled = [item for item in worksheet.findall(".//m:c", ns) if int(item.attrib.get("s", "0")) > 0]
            self.assertIn("C2-10", formulas)
            self.assertIn("A4:B4", merges)
            self.assertTrue(styled)
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            self.assertGreater(int(styles.find("m:fonts", ns).attrib["count"]), 1)
            self.assertGreater(int(styles.find("m:fills", ns).attrib["count"]), 1)

    def test_missing_dependency_is_a_controlled_runtime_error_not_import_failure(self):
        code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from scripts.adapters import ColumnMapping
from scripts.adapters.spreadsheet import SpreadsheetDependencyError, extract_spreadsheet
try:
    extract_spreadsheet({str(XLSX_FIXTURE)!r}, sheet='物理类', mapping=ColumnMapping({{'school': '院校'}}))
except SpreadsheetDependencyError as error:
    assert {str(ROOT)!r} not in str(error)
    print(type(error).__name__)
else:
    raise SystemExit('dependency failure did not fail closed')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.strip(), "SpreadsheetDependencyError")

    def test_package_and_flat_imports_are_lazy_and_perform_no_file_or_network_io(self):
        code = f"""
import pathlib, socket, sys
sys.path.insert(0, {str(ROOT)!r})
def blocked(*args, **kwargs):
    raise AssertionError('I/O during import')
pathlib.Path.open = blocked
pathlib.Path.read_bytes = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
import scripts.adapters.html_table
import scripts.adapters.spreadsheet
sys.modules.pop('adapters', None)
sys.path.insert(0, {str(ROOT / 'scripts')!r})
import adapters.html_table
import adapters.spreadsheet
print('ok')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")

    def test_xlsx_requires_exact_safe_file_and_rechecks_same_name_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            wrong = directory / "fixture.XLSX"
            wrong.write_bytes(XLSX_FIXTURE.read_bytes())
            with self.assertRaises(StructuredFileError):
                extract_spreadsheet(wrong, sheet="物理类", mapping=self.mapping())

            target = directory / "race.xlsx"
            target.write_bytes(XLSX_FIXTURE.read_bytes())
            replacement = directory / "replacement.xlsx"
            replacement.write_bytes(XLSX_FIXTURE.read_bytes() + b"replacement")
            original_open = Path.open

            def replacing_open(candidate, *args, **kwargs):
                if candidate == target:
                    os.replace(replacement, target)
                return original_open(candidate, *args, **kwargs)

            with mock.patch.object(Path, "open", replacing_open):
                with self.assertRaises(StructuredFileError) as raised:
                    extract_spreadsheet(target, sheet="物理类", mapping=self.mapping())
            self.assertNotIn(str(directory), str(raised.exception))

    def test_network_sentinels_are_never_touched(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), mock.patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("network")
        ), mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")), mock.patch.object(
            http.client.HTTPConnection, "connect", side_effect=AssertionError("network")
        ):
            self.assertEqual(len(extract_html_table(
                HTML_FIXTURE,
                table_index=1,
                expected_caption="合成分数位次表",
                mapping=HtmlAdapterTest.mapping(),
            ).rows), 3)
            self.assertEqual(len(extract_spreadsheet(XLSX_FIXTURE, sheet="物理类", mapping=self.mapping()).rows), 3)


if __name__ == "__main__":
    unittest.main()
