"""Synthetic documents exercise optional parsers without network or admission data."""

from __future__ import annotations

import hashlib
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.adapters import CellStatus, StructuredValidationError
from scripts.adapters import pdf_text


ROOT = Path(__file__).resolve().parents[1]
PDF_FIXTURE = ROOT / "tests/fixtures/replay/pdf/text-and-image.pdf"


def _record(code: int, data: bytes = b"") -> bytes:
    return struct.pack("<HH", code, len(data)) + data


def _synthetic_xls(*, hidden: bool = False, merged: bool = False, formula: bool = False,
                   error: bool = False, hidden_header: bool = False,
                   hidden_sheet: bool = False, hidden_column: bool = False,
                   unordered: bool = False, admission: bool = False) -> bytes:
    """Small BIFF8 workbook written directly; every value is invented for this test."""
    def bof(kind: int) -> bytes:
        return _record(0x0809, struct.pack("<HHHHII", 0x0600, kind, 0x0DBB, 1997, 0, 6))

    def text(row: int, column: int, value: str) -> bytes:
        return _record(0x0204, struct.pack("<HHHHB", row, column, 0, len(value), 0) + value.encode("ascii"))

    def number(row: int, column: int, value: float) -> bytes:
        return _record(0x0203, struct.pack("<HHHd", row, column, 0, value))

    headers = ("Name", "Score", "Rank", "SchoolCode", "ProgramGroup") if admission else ("Name", "Score", "Rank")
    rows = b"".join(text(0, col, label) for col, label in enumerate(headers))
    rows += text(1, 0, "Synthetic A")
    if formula:
        rows += _record(0x0006, struct.pack("<HHHdHIH", 1, 1, 0, 630, 0, 0, 3) + b"\x1e\x76\x02")
    elif error:
        rows += _record(0x0205, struct.pack("<HHHBB", 1, 1, 0, 0x07, 1))
    else:
        rows += number(1, 1, 630)
    rows += number(1, 2, 101)
    rows += text(2, 0, "Synthetic B") + number(2, 1, 650 if unordered else 620) + number(2, 2, 51 if unordered else 201)
    if admission:
        rows += text(1, 3, "0001") + text(1, 4, "001")
        rows += text(2, 3, "0002") + text(2, 4, "001")
        if unordered:
            rows += text(3, 0, "Synthetic C") + number(3, 1, 615) + number(3, 2, 202)
            rows += text(3, 3, "0003") + text(3, 4, "001")
    if hidden:
        rows += _record(0x0208, struct.pack("<HHHHHHI", 1, 0, 3, 255, 0, 0, 0x20))
    if hidden_header:
        rows += _record(0x0208, struct.pack("<HHHHHHI", 0, 0, 3, 255, 0, 0, 0x20))
    if hidden_column:
        rows += _record(0x007D, struct.pack("<HHHHHH", 1, 1, 2048, 0, 1, 0))
    if merged:
        rows += _record(0x00E5, struct.pack("<HHHHH", 1, 1, 1, 0, 1))
    row_count = 4 if unordered and admission else 3
    worksheet = bof(0x0010) + _record(0x0200, struct.pack("<IIHHH", 0, row_count, 0, len(headers), 0)) + rows + _record(0x000A)
    name = b"Synthetic"
    prefix = bof(0x0005) + _record(0x0042, struct.pack("<H", 1200))
    bounds = lambda offset: _record(0x0085, struct.pack("<IBBB", offset, int(hidden_sheet), 0, len(name)) + b"\x00" + name)
    offset = len(prefix) + len(bounds(0)) + len(_record(0x000A))
    return prefix + bounds(offset) + _record(0x000A) + worksheet


class PdfFallbackAdapterTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "optional pypdf unavailable")
    def test_missing_pdfplumber_uses_real_pypdf_and_preserves_hash_pages_and_ocr_warning(self):
        with mock.patch.object(pdf_text, "_load_pdfplumber", side_effect=pdf_text.PdfDependencyError("unavailable")):
            document = pdf_text.extract_pdf_text(PDF_FIXTURE)
        self.assertEqual(document.document_id, "sha256:" + hashlib.sha256(PDF_FIXTURE.read_bytes()).hexdigest())
        self.assertEqual(document.page_count, 2)
        self.assertIn("Synthetic Admission Snapshot", document.pages[0].text)
        self.assertEqual(document.pages[0].extraction_method, "pypdf-text")
        self.assertEqual(document.pages[1].page_number, 2)
        self.assertEqual(document.pages[1].text, "")
        self.assertTrue(document.pages[1].image_only)
        self.assertEqual(document.warnings, ("image-only-pages-present",))

    def test_both_pdf_libraries_missing_is_a_controlled_dependency_failure(self):
        with mock.patch.object(pdf_text, "_load_pdfplumber", side_effect=pdf_text.PdfDependencyError("unavailable")):
            with mock.patch.dict("sys.modules", {"pypdf": None}):
                with self.assertRaises(pdf_text.PdfDependencyError) as raised:
                    pdf_text.extract_pdf_text(PDF_FIXTURE)
        self.assertIsNone(raised.exception.__cause__)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "optional pypdf unavailable")
    def test_malformed_pdf_remains_parse_failure_with_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "private-malformed.pdf"
            path.write_bytes(b"not a PDF")
            with mock.patch.object(pdf_text, "_load_pdfplumber", side_effect=pdf_text.PdfDependencyError("unavailable")):
                with self.assertRaises(pdf_text.PdfParseError) as raised:
                    pdf_text.extract_pdf_text(path)
            self.assertNotIn(str(path), str(raised.exception))

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "optional pypdf unavailable")
    def test_blank_page_is_not_misreported_as_an_unreadable_image(self):
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic-blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(path)
            with mock.patch.object(pdf_text, "_load_pdfplumber", side_effect=pdf_text.PdfDependencyError("unavailable")):
                document = pdf_text.extract_pdf_text(path)
        self.assertFalse(document.pages[0].image_only)
        self.assertEqual(document.warnings, ("empty-pages-present",))


class XlsDependencyBoundaryTest(unittest.TestCase):
    def test_missing_reader_leaves_the_original_source_untouched(self):
        from scripts.adapters.xls import XlsDependencyError, extract_xls

        with tempfile.TemporaryDirectory() as temporary:
            source = _synthetic_xls()
            path = Path(temporary).resolve() / "synthetic.xls"
            path.write_bytes(source)
            with mock.patch.dict("sys.modules", {"xlrd": None}):
                with self.assertRaises(XlsDependencyError) as raised:
                    extract_xls(path, sheet="Synthetic", mapping={"score": "Score"})
            self.assertEqual(path.read_bytes(), source)
            self.assertIsNone(raised.exception.__cause__)


@unittest.skipUnless(importlib.util.find_spec("xlrd"), "optional xlrd unavailable")
class XlsFallbackAdapterTest(unittest.TestCase):
    def _extract(self, source: bytes, **kwargs):
        from scripts.adapters.xls import extract_xls

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.xls"
            path.write_bytes(source)
            result = extract_xls(path, sheet="Synthetic", mapping={"name": "Name", "score": "Score", "rank": "Rank"}, **kwargs)
            self.assertEqual(path.read_bytes(), source)
            return result

    def test_real_biff_read_preserves_cells_source_row_coordinates_and_method(self):
        result = self._extract(_synthetic_xls())
        self.assertEqual(result.extraction_method, "xls-worksheet")
        self.assertEqual(result.sheet, "Synthetic")
        self.assertEqual([dict(row.values) for row in result.rows], [
            {"name": "Synthetic A", "score": 630, "rank": 101},
            {"name": "Synthetic B", "score": 620, "rank": 201},
        ])
        self.assertEqual([row.location for row in result.rows], ["Synthetic!A2:C2", "Synthetic!A3:C3"])
        self.assertEqual(result.coverage.lower_score, 620)
        self.assertEqual(result.coverage.upper_rank, 201)
        self.assertTrue(all(status is CellStatus.EXACT for row in result.rows for status in row.cell_status.values()))

    def test_hidden_merged_formula_and_error_cells_never_become_exact_evidence(self):
        for options, field, status in (
            ({"hidden": True}, "score", CellStatus.UNCERTAIN),
            ({"merged": True}, "score", CellStatus.MERGED),
            ({"formula": True}, "score", CellStatus.FORMULA),
            ({"error": True}, "score", CellStatus.INVALID),
        ):
            with self.subTest(options=options):
                result = self._extract(_synthetic_xls(**options))
                self.assertEqual(result.rows[0].cell_status[field], status)
                self.assertEqual(result.coverage.upper_score, 620)

    def test_renamed_html_is_not_accepted_as_a_workbook(self):
        with self.assertRaises(StructuredValidationError):
            self._extract(b"<html><table><tr><td>630</td></tr></table></html>")

    def test_hidden_header_sheet_or_selected_column_excludes_coverage(self):
        for options in ({"hidden_header": True}, {"hidden_sheet": True}, {"hidden_column": True}):
            with self.subTest(options=options):
                result = self._extract(_synthetic_xls(**options))
                self.assertEqual(result.rows[0].cell_status["score"], CellStatus.UNCERTAIN)
                self.assertIsNone(result.coverage.upper_score)

    def test_explicit_missing_sheet_is_not_replaced_by_first_sheet(self):
        from scripts.adapters.xls import extract_xls

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.xls"
            path.write_bytes(_synthetic_xls())
            with self.assertRaises(StructuredValidationError):
                extract_xls(path, sheet="Missing", mapping={"score": "Score"})

    def test_admission_rows_keep_school_order_without_imposing_province_score_order(self):
        from scripts.adapters.xls import extract_xls

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic-admissions.xls"
            original = _synthetic_xls(unordered=True, admission=True)
            path.write_bytes(original)
            table = extract_xls(path, sheet="Synthetic", mapping={
                "school_code": "SchoolCode", "school_name": "Name", "program_group": "ProgramGroup",
                "min_score": "Score", "min_rank": "Rank",
            })
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual([dict(row.values) for row in table.rows], [
            {"school_code": "0001", "school_name": "Synthetic A", "program_group": "001", "min_score": 630, "min_rank": 101},
            {"school_code": "0002", "school_name": "Synthetic B", "program_group": "001", "min_score": 650, "min_rank": 51},
            {"school_code": "0003", "school_name": "Synthetic C", "program_group": "001", "min_score": 615, "min_rank": 202},
        ])
        self.assertEqual([row.location for row in table.rows], ["Synthetic!A2:E2", "Synthetic!A3:E3", "Synthetic!A4:E4"])
        self.assertEqual(table.coverage.lower_score, 615)
        self.assertEqual(table.coverage.upper_score, 650)
        self.assertEqual(table.coverage.lower_rank, 51)

    def test_score_distribution_still_rejects_nonmonotonic_rows(self):
        with self.assertRaises(StructuredValidationError):
            self._extract(_synthetic_xls(unordered=True))


if __name__ == "__main__":
    unittest.main()
