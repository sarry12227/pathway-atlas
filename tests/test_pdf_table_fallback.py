"""Synthetic text PDFs exercise exact regional extraction, without live data."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.adapters import CellStatus, ColumnMapping, StructuredValidationError
from scripts.adapters.pdf_table import extract_pdf_table
from scripts.adapters.pdf_text import PdfDependencyError


HEADERS = ("Score", "Count", "Cumulative")
MAPPING = ColumnMapping(
    {"score": "Score", "cumulative_count": "Cumulative"},
    roles={"score": "score", "cumulative_count": "rank"},
    score_scale=(0, 750),
)


def synthetic_pdf(path: Path, pages: list[list[str]]) -> None:
    """Write a minimal actual text PDF for the installed PDF parser to read."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids = []
    for lines in pages:
        page_id = len(objects) + 1
        stream_id = page_id + 1
        page_ids.append(page_id)
        commands = ["BT /F1 12 Tf 16 TL 50 760 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii")
        objects.extend(
            [
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>".encode("ascii"),
                f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
            ]
        )
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode("ascii") + value + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(data)


class PdfTableFallbackTest(unittest.TestCase):
    def test_horizontal_groups_preserve_exact_selected_columns_and_pdf_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.pdf"
            synthetic_pdf(path, [[
                "Synthetic score table",
                "Score Count Cumulative Score Count Cumulative",
                "650 50 3000 600 100 50000",
                "649 60 3060 599 120 50120",
                "Page 1 of 1",
            ]])
            before = path.read_bytes()
            table = extract_pdf_table(
                path, mapping=MAPPING, headers=HEADERS, page_number=1,
                header_line=2, first_data_line=3, last_data_line=4,
                column_group=2, expected_caption="Synthetic score table",
            )
            self.assertEqual([row.to_dict()["values"] for row in table.rows], [
                {"score": 600, "cumulative_count": 50000},
                {"score": 599, "cumulative_count": 50120},
            ])
            self.assertEqual(table.extraction_method, "pdf-text-table")
            self.assertEqual(table.rows[0].location, "page[1]/line[3]/group[2]")
            self.assertEqual(table.rows[1].location, "page[1]/line[4]/group[2]")
            self.assertTrue(all(status is CellStatus.EXACT for row in table.rows for status in row.cell_status.values()))
            self.assertEqual(table.coverage.to_dict(), {
                "lower_score": 599, "upper_score": 600,
                "lower_rank": 50000, "upper_rank": 50120,
            })
            self.assertIn(hashlib.sha256(before).hexdigest(), table.table_id)
            self.assertRegex(table.table_id, r"parser\[(?:pdfplumber|pypdf)-text\]")
            self.assertEqual(table.warnings, ())
            self.assertEqual(path.read_bytes(), before)

    def test_vertical_blocks_need_explicit_header_and_never_mix_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.pdf"
            synthetic_pdf(path, [[
                "Synthetic score table", "Score Count Cumulative", "650 50 3000",
                "Score Count Cumulative", "600 100 50000", "599 120 50120",
                "Page 1 of 1",
            ]])
            table = extract_pdf_table(
                path, mapping=MAPPING, headers=HEADERS, page_number=1,
                header_line=4, first_data_line=5, last_data_line=6,
            )
            self.assertEqual([row.values["score"] for row in table.rows], [600, 599])
            self.assertEqual(table.rows[0].location, "page[1]/line[5]/group[1]")

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "optional pypdf unavailable")
    def test_missing_pdfplumber_still_extracts_numeric_rows_with_real_pypdf(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.pdf"
            synthetic_pdf(path, [["Score Count Cumulative", "600 100 50000"]])
            with mock.patch(
                "scripts.adapters.pdf_text._load_pdfplumber",
                side_effect=PdfDependencyError("dependency unavailable"),
            ):
                table = extract_pdf_table(
                    path, mapping=MAPPING, headers=HEADERS, page_number=1,
                    header_line=1, first_data_line=2, last_data_line=2,
                )
            self.assertEqual(table.rows[0].to_dict()["values"], {
                "score": 600, "cumulative_count": 50000,
            })
            self.assertIn("parser[pypdf-text]", table.table_id)
            self.assertEqual(table.rows[0].location, "page[1]/line[2]/group[1]")

    def test_wrong_headers_missing_cells_masked_values_and_bad_order_fail_closed(self):
        cases = (
            ["Score Count Cumulative", "600 50000"],
            ["Score Count Cumulative", "600+ 100 50000"],
            ["Score Count Cumulative", "600 100 50000 extra"],
            ["Score Count Cumulative", "600 O 50000"],
            ["Cumulative Count Score", "600 100 50000"],
            ["Score Count Cumulative Score Count Cumulative", "650 3000 600 100 50000"],
            ["Score Count Cumulative", "600 100 50000", "601 120 50120"],
            ["Score Count Cumulative", "600 100 50000", "599 120 49000"],
            ["Score Count Cumulative", "600 100 50000", "Page 1 of 1"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.pdf"
            for lines in cases:
                with self.subTest(lines=lines):
                    synthetic_pdf(path, [lines])
                    with self.assertRaises(StructuredValidationError):
                        extract_pdf_table(
                            path, mapping=MAPPING, headers=HEADERS, page_number=1,
                            header_line=1, first_data_line=2, last_data_line=len(lines),
                        )

    def test_wrong_page_region_caption_and_column_group_fail_without_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "synthetic.pdf"
            synthetic_pdf(path, [["Synthetic score table", "Score Count Cumulative", "600 100 50000"]])
            for override in (
                {"page_number": 2}, {"page_number": True}, {"header_line": 3},
                {"first_data_line": 2}, {"last_data_line": 4},
                {"column_group": 2}, {"expected_caption": "Another score table"},
            ):
                arguments = dict(
                    mapping=MAPPING, headers=HEADERS, page_number=1, header_line=2,
                    first_data_line=3, last_data_line=3,
                )
                arguments.update(override)
                with self.subTest(override=override), self.assertRaises((StructuredValidationError, ValueError, TypeError)):
                    extract_pdf_table(path, **arguments)

    def test_image_only_page_is_never_treated_as_a_text_table(self):
        path = Path(__file__).resolve().parent / "fixtures/replay/pdf/text-and-image.pdf"
        with self.assertRaises(StructuredValidationError):
            extract_pdf_table(
                path, mapping=MAPPING, headers=HEADERS, page_number=2,
                header_line=1, first_data_line=2, last_data_line=2,
            )


if __name__ == "__main__":
    unittest.main()
