import json
import math
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PDF_FIXTURE = ROOT / "tests" / "fixtures" / "replay" / "pdf" / "text-and-image.pdf"
OCR_FIXTURE = ROOT / "tests" / "fixtures" / "replay" / "ocr" / "rows.json"
QR_FIXTURE = ROOT / "tests" / "fixtures" / "replay" / "qr" / "decoded-url.txt"


class UnstructuredAdapterImportTest(unittest.TestCase):
    def test_task4_public_modules_are_importable(self):
        from scripts.adapters import ocr_rows, pdf_text, qr

        self.assertTrue(callable(pdf_text.extract_pdf_text))
        self.assertTrue(callable(ocr_rows.normalize_ocr_rows))
        self.assertTrue(callable(qr.resolve_qr_payload))


class PdfTextAdapterTest(unittest.TestCase):
    def test_real_pdf_preserves_text_image_only_page_numbers_and_safe_serialization(self):
        from scripts.adapters.pdf_text import extract_pdf_text

        document = extract_pdf_text(PDF_FIXTURE)

        self.assertEqual(document.page_count, 2)
        self.assertEqual([page.page_number for page in document.pages], [1, 2])
        self.assertIn("Synthetic Admission Snapshot", document.pages[0].text)
        self.assertEqual(document.pages[0].extraction_method, "pdfplumber-text")
        self.assertFalse(document.pages[0].image_only)
        self.assertEqual(document.pages[1].text, "")
        self.assertEqual(document.pages[1].extraction_method, "none")
        self.assertTrue(document.pages[1].image_only)
        self.assertEqual(document.pages[1].warnings, ("image-only",))
        self.assertRegex(document.document_id, r"^sha256:[0-9a-f]{64}$")
        serialized = json.dumps(document.to_dict(), ensure_ascii=False, allow_nan=False)
        self.assertNotIn(str(ROOT), serialized)
        self.assertEqual(document, extract_pdf_text(PDF_FIXTURE))

    def test_page_and_document_contracts_reject_forged_or_mutable_state(self):
        from scripts.adapters.pdf_text import PdfTextDocument, PdfTextPage

        warnings = ["image-only"]
        page = PdfTextPage(1, "", "none", warnings, True)
        warnings.append("forged")
        self.assertEqual(page.warnings, ("image-only",))
        with self.assertRaises(Exception):
            page.warnings += ("forged",)

        invalid_pages = (
            lambda: PdfTextPage(True, "x", "pdfplumber-text"),
            lambda: PdfTextPage(1, "x", "unknown"),
            lambda: PdfTextPage(1, "x", "none"),
            lambda: PdfTextPage(1, "", "pdfplumber-text"),
            lambda: PdfTextPage(1, "", "none", ("unknown",), False),
            lambda: PdfTextPage(1, "", "none", (), math.nan),
        )
        for constructor in invalid_pages:
            with self.subTest(constructor=constructor):
                with self.assertRaises((TypeError, ValueError)):
                    constructor()

        text_page = PdfTextPage(1, "text", "pdfplumber-text")
        second = PdfTextPage(2, "", "none", ("empty-page",), False)
        document = PdfTextDocument(
            "sha256:" + "a" * 64,
            2,
            [text_page, second],
            ("empty-pages-present",),
        )
        with self.assertRaises(Exception):
            document.pages += (text_page,)
        for changed in (
            {"document_id": "C:/secret/source.pdf"},
            {"page_count": True},
            {"page_count": 3},
            {"pages": (text_page, text_page)},
            {"pages": (second,)},
            {"warnings": ("unknown",)},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises((TypeError, ValueError)):
                    replace(document, **changed)

    def test_page_and_document_warnings_exactly_match_derived_state(self):
        from scripts.adapters.pdf_text import PdfTextDocument, PdfTextPage

        document_id = "sha256:" + "b" * 64
        for constructor in (
            lambda: PdfTextPage(1, "", "none", ("image-only", "empty-page"), True),
            lambda: PdfTextPage(1, "", "none", ("empty-page", "image-only"), False),
        ):
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValueError):
                    constructor()

        text = PdfTextPage(1, "text", "pdfplumber-text")
        image = PdfTextPage(1, "", "none", ("image-only",), True)
        empty = PdfTextPage(2, "", "none", ("empty-page",), False)
        invalid_documents = (
            lambda: PdfTextDocument(document_id, 1, (image,), ()),
            lambda: PdfTextDocument(document_id, 1, (text,), ("image-only-pages-present",)),
            lambda: PdfTextDocument(
                document_id,
                2,
                (image, empty),
                ("empty-pages-present", "image-only-pages-present"),
            ),
        )
        for constructor in invalid_documents:
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValueError):
                    constructor()

        document = PdfTextDocument(
            document_id,
            2,
            (image, empty),
            ("image-only-pages-present", "empty-pages-present"),
        )
        with self.assertRaises(ValueError):
            replace(document, warnings=("image-only-pages-present",))

    def test_malformed_truncated_and_encrypted_fail_without_path_leak(self):
        from scripts.adapters.pdf_text import PdfParseError, extract_pdf_text

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            cases = {
                "malformed.pdf": b"not a pdf",
                "truncated.pdf": PDF_FIXTURE.read_bytes()[:100],
            }
            for name, content in cases.items():
                path = directory / name
                path.write_bytes(content)
                with self.subTest(name=name):
                    with self.assertRaises(PdfParseError) as raised:
                        extract_pdf_text(path)
                    error = raised.exception
                    rendered = "".join(traceback.format_exception(error))
                    self.assertIsNone(error.__cause__)
                    self.assertNotIn(str(path), str(error))
                    self.assertNotIn(str(path), rendered)

            with mock.patch("pdfplumber.open", side_effect=RuntimeError(str(directory / "encrypted.pdf"))):
                with self.assertRaises(PdfParseError) as raised:
                    extract_pdf_text(PDF_FIXTURE)
                self.assertIsNone(raised.exception.__cause__)
                self.assertNotIn(str(directory), str(raised.exception))

    def test_pdf_requires_the_shared_absolute_exact_suffix_regular_file_boundary(self):
        from scripts.adapters import StructuredFileError
        from scripts.adapters.pdf_text import extract_pdf_text

        with self.assertRaises(StructuredFileError):
            extract_pdf_text("tests/fixtures/replay/pdf/text-and-image.pdf")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            wrong = directory / "fixture.PDF"
            wrong.write_bytes(PDF_FIXTURE.read_bytes())
            with self.assertRaises(StructuredFileError):
                extract_pdf_text(wrong)
            named_directory = directory / "directory.pdf"
            named_directory.mkdir()
            with self.assertRaises(StructuredFileError):
                extract_pdf_text(named_directory)

            oversized = directory / "oversized.pdf"
            oversized.write_bytes(b"x" * 32)
            with mock.patch("scripts.adapters.MAX_FILE_BYTES", 16):
                with self.assertRaises(StructuredFileError):
                    extract_pdf_text(oversized)

    def test_pdf_symlink_and_same_name_replacement_fail_closed(self):
        from scripts.adapters import StructuredFileError
        from scripts.adapters.pdf_text import extract_pdf_text

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            target = directory / "race.pdf"
            target.write_bytes(PDF_FIXTURE.read_bytes())
            replacement = directory / "replacement.pdf"
            replacement.write_bytes(PDF_FIXTURE.read_bytes() + b"changed")
            original_open = Path.open

            def replacing_open(candidate, *args, **kwargs):
                if candidate == target:
                    os.replace(replacement, target)
                return original_open(candidate, *args, **kwargs)

            with mock.patch.object(Path, "open", replacing_open):
                with self.assertRaises(StructuredFileError):
                    extract_pdf_text(target)

            link = directory / "linked.pdf"
            try:
                os.symlink(PDF_FIXTURE, link)
            except OSError:
                return
            with self.assertRaises(StructuredFileError):
                extract_pdf_text(link)

    def test_pdf_dependency_is_lazy_and_controlled(self):
        code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from scripts.adapters.pdf_text import PdfDependencyError, extract_pdf_text
try:
    extract_pdf_text({str(PDF_FIXTURE)!r})
except PdfDependencyError as error:
    assert error.__cause__ is None
    assert {str(ROOT)!r} not in str(error)
    print(type(error).__name__)
else:
    raise SystemExit('missing dependency did not fail closed')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.strip(), "PdfDependencyError")


class OcrRowsAdapterTest(unittest.TestCase):
    @staticmethod
    def mapping():
        from scripts.adapters import ColumnMapping

        return ColumnMapping(
            {
                "group": ["Fictional group"],
                "score": ["Score"],
                "rank": ["Rank"],
            },
            roles={"score": "score", "rank": "rank"},
        )

    @staticmethod
    def payload():
        return json.loads(OCR_FIXTURE.read_text("utf-8"))

    @staticmethod
    def write_payload(directory, payload, name="rows.json"):
        path = directory / name
        path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        return path

    def extract(self, path=OCR_FIXTURE, mapping=None, **changes):
        from scripts.adapters.ocr_rows import normalize_ocr_rows

        arguments = {
            "score_scale": (0, 750),
            "min_exact_confidence": 0.95,
        }
        arguments.update(changes)
        return normalize_ocr_rows(path, mapping or self.mapping(), **arguments)

    def test_exact_rows_preserve_row_and_cell_provenance_mapping_and_coverage(self):
        from scripts.adapters import CellStatus, ExtractedTable

        columns = {
            "group": ["Fictional group"],
            "score": ["Score"],
            "rank": ["Rank"],
        }
        from scripts.adapters import ColumnMapping

        mapping = ColumnMapping(columns, roles={"score": "score", "rank": "rank"})
        table = self.extract(mapping=mapping)
        columns["score"].append("forged")

        self.assertIsInstance(table, ExtractedTable)
        self.assertEqual(table.table_id, "ocr-document-001")
        self.assertEqual(table.extraction_method, "host-ocr-rows")
        self.assertEqual(table.rows[0].location, "page[1]/image[page-1]/bbox[10,20,500,60]")
        self.assertEqual(
            table.rows[0].cell_locations["score"],
            "page[1]/image[page-1]/bbox[210,20,330,60]",
        )
        self.assertEqual(table.rows[0].values, {"group": "Sample Alpha", "score": 650, "rank": 100})
        self.assertTrue(all(status is CellStatus.EXACT for status in table.rows[0].cell_status.values()))
        self.assertIsNone(table.rows[1].values["score"])
        self.assertIsNone(table.rows[1].values["rank"])
        self.assertIs(table.rows[1].cell_status["score"], CellStatus.MASKED)
        self.assertIs(table.rows[1].cell_status["rank"], CellStatus.MASKED)
        self.assertEqual(
            table.coverage.to_dict(),
            {"lower_score": 620, "upper_score": 650, "lower_rank": 100, "upper_rank": 420},
        )
        self.assertEqual(
            table.to_dict()["mapping_snapshot"],
            {
                "columns": {
                    "group": ["Fictional group"],
                    "score": ["Score"],
                    "rank": ["Rank"],
                },
                "roles": {"score": "score", "rank": "rank"},
                "score_scale": [0, 750],
            },
        )
        serialized = json.dumps(table.to_dict(), ensure_ascii=False, allow_nan=False)
        self.assertNotIn(str(ROOT), serialized)
        self.assertEqual(table.to_dict(), self.extract().to_dict())

    def test_masked_boundary_forms_never_retain_apparent_numbers(self):
        from scripts.adapters import CellStatus

        forms = ("*", "—", "580分以上", "前100名", "由学校逐一告知")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            for index, raw_text in enumerate(forms):
                payload = self.payload()
                payload["rows"][1]["cells"][1]["raw_text"] = raw_text
                payload["rows"][1]["cells"][1]["normalized_value"] = 580
                path = self.write_payload(directory, payload, f"masked-{index}.json")
                with self.subTest(raw_text=raw_text):
                    row = self.extract(path).rows[1]
                    self.assertIsNone(row.values["score"])
                    self.assertIs(row.cell_status["score"], CellStatus.MASKED)

    def test_low_confidence_cropped_and_partial_coverage_remain_nonexact(self):
        from scripts.adapters import CellStatus

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()

            low = self.payload()
            low["rows"][0]["cells"][1]["confidence"] = 0.5
            low_table = self.extract(self.write_payload(directory, low, "low.json"))
            self.assertIs(low_table.rows[0].cell_status["score"], CellStatus.UNCERTAIN)
            self.assertIn("low-confidence-cell:score", low_table.rows[0].warnings)

            cropped = self.payload()
            cropped["rows"][0]["cropped"] = True
            cropped_table = self.extract(self.write_payload(directory, cropped, "cropped.json"))
            self.assertTrue(
                all(status is not CellStatus.EXACT for status in cropped_table.rows[0].cell_status.values())
            )
            self.assertIn("cropped-row", cropped_table.rows[0].warnings)

            partial = self.payload()
            partial["total_pages"] = 2
            partial_table = self.extract(self.write_payload(directory, partial, "partial.json"))
            self.assertTrue(
                all(
                    status is not CellStatus.EXACT
                    for row in partial_table.rows
                    for status in row.cell_status.values()
                )
            )
            self.assertEqual(
                partial_table.coverage.to_dict(),
                {"lower_score": None, "upper_score": None, "lower_rank": None, "upper_rank": None},
            )
            self.assertIn("partial-page-coverage", partial_table.warnings)

    def test_role_coverage_requires_exact_first_and_last_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()

            masked_score = self.payload()
            masked_score["rows"][0]["cells"][1].update(
                raw_text="580分以上",
                normalized_value=580,
            )
            masked_table = self.extract(
                self.write_payload(directory, masked_score, "masked-score-boundary.json")
            )
            self.assertEqual(
                masked_table.coverage.to_dict(),
                {"lower_score": None, "upper_score": None, "lower_rank": 100, "upper_rank": 420},
            )
            self.assertIn("coverage-score-unavailable", masked_table.warnings)

            cropped_last = self.payload()
            cropped_last["rows"][-1]["cropped"] = True
            cropped_table = self.extract(
                self.write_payload(directory, cropped_last, "cropped-last-boundary.json")
            )
            self.assertEqual(
                cropped_table.coverage.to_dict(),
                {"lower_score": None, "upper_score": None, "lower_rank": None, "upper_rank": None},
            )
            self.assertEqual(
                cropped_table.warnings,
                (
                    "masked-cells-present",
                    "uncertain-cells-present",
                    "coverage-score-unavailable",
                    "coverage-rank-unavailable",
                ),
            )

            boundary_states = {
                "low-confidence-rank": lambda item: item["rows"][-1]["cells"][2].update(confidence=0.5),
                "unverified-score": lambda item: item["rows"][0]["cells"][1].update(verified=False),
                "missing-rank": lambda item: item["rows"][0]["cells"][2].update(
                    raw_text="", normalized_value=None
                ),
            }
            for name, mutate in boundary_states.items():
                payload = self.payload()
                mutate(payload)
                table = self.extract(self.write_payload(directory, payload, f"{name}.json"))
                affected_role = "score" if "score" in name else "rank"
                with self.subTest(name=name):
                    self.assertIsNone(getattr(table.coverage, f"lower_{affected_role}"))
                    self.assertIsNone(getattr(table.coverage, f"upper_{affected_role}"))
                    self.assertIn(f"coverage-{affected_role}-unavailable", table.warnings)

    def test_strict_json_schema_ids_geometry_counts_and_anchors_fail_closed(self):
        from scripts.adapters.ocr_rows import OcrValidationError

        mutations = {
            "future": lambda item: item.update(schema_version=2),
            "unknown": lambda item: item.update(extra="x"),
            "unsafe-id": lambda item: item.update(document_id="C:/private/rows.json"),
            "bad-bbox": lambda item: item["rows"][0].update(bbox=[10, 20, 5, 60]),
            "bad-confidence": lambda item: item["rows"][0]["cells"][0].update(confidence=1.1),
            "bool-count": lambda item: item.update(total_pages=True),
            "bad-covered": lambda item: item.update(covered_pages=[2]),
            "one-anchor": lambda item: item.update(anchors=item["anchors"][:1]),
            "duplicate-anchor": lambda item: item.update(anchors=[item["anchors"][0], item["anchors"][0]]),
            "anchor-mismatch": lambda item: item["anchors"][0].update(raw_text="forged"),
            "duplicate-label": lambda item: item["rows"][0]["cells"].append(dict(item["rows"][0]["cells"][0])),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            for name, mutate in mutations.items():
                payload = self.payload()
                mutate(payload)
                path = self.write_payload(directory, payload, f"{name}.json")
                with self.subTest(name=name):
                    with self.assertRaises(OcrValidationError):
                        self.extract(path)

            coincident = self.payload()
            coincident["rows"][2]["bbox"] = [10, 20, 500, 60]
            coincident["rows"][2]["cells"][0]["bbox"] = [10, 20, 200, 60]
            coincident["rows"][2]["cells"][1]["bbox"] = [210, 20, 330, 60]
            coincident["rows"][2]["cells"][2]["bbox"] = [10, 20, 200, 60]
            coincident["anchors"][1]["bbox"] = [10, 20, 200, 60]
            with self.assertRaises(OcrValidationError):
                self.extract(self.write_payload(directory, coincident, "coincident-anchors.json"))

            for name, source in {
                "duplicate-key.json": '{"schema_version":1,"schema_version":1}',
                "nan.json": '{"schema_version":NaN}',
                "infinity.json": '{"schema_version":Infinity}',
                "malformed.json": '{"schema_version":',
            }.items():
                path = directory / name
                path.write_text(source, "utf-8")
                with self.subTest(name=name):
                    with self.assertRaises(OcrValidationError):
                        self.extract(path)

    def test_anchors_reject_near_overlap_on_one_image_but_allow_same_bbox_across_images(self):
        from scripts.adapters.ocr_rows import OcrValidationError

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            overlapping = self.payload()
            overlapping_last = overlapping["rows"][-1]
            overlapping_last["bbox"] = [10, 20, 500, 60]
            overlapping_last["cells"][0]["bbox"] = [210, 20, 330, 60]
            overlapping_last["cells"][1]["bbox"] = [340, 20, 500, 60]
            overlapping_last["cells"][2]["bbox"] = [11, 20, 201, 60]
            overlapping["anchors"][1] = {
                "row_index": 3,
                "label": "Rank",
                "bbox": [11, 20, 201, 60],
                "raw_text": "420",
                "normalized_value": 420,
            }
            with self.assertRaises(OcrValidationError):
                self.extract(self.write_payload(directory, overlapping, "near-overlap.json"))

            separate_images = self.payload()
            separate_images["total_pages"] = 2
            separate_images["covered_pages"] = [1, 2]
            separate_images["images"].append({"page_number": 2, "image_id": "page-2"})
            last_row = separate_images["rows"][-1]
            last_row.update(page_number=2, image_id="page-2", bbox=[10, 20, 500, 60])
            last_row["cells"][0]["bbox"] = [10, 20, 200, 60]
            last_row["cells"][1]["bbox"] = [210, 20, 330, 60]
            last_row["cells"][2]["bbox"] = [10, 20, 200, 60]
            separate_images["anchors"][1]["bbox"] = [10, 20, 200, 60]
            table = self.extract(
                self.write_payload(directory, separate_images, "same-bbox-separate-images.json")
            )
            self.assertEqual(len(table.rows), 3)

    def test_numeric_scale_integer_duplicate_and_monotonic_rules_fail_closed(self):
        from scripts.adapters.ocr_rows import OcrValidationError

        mutations = {
            "bool-score": lambda item: item["rows"][0]["cells"][1].update(normalized_value=True),
            "fraction-rank": lambda item: item["rows"][0]["cells"][2].update(normalized_value=100.5),
            "outside-scale": lambda item: item["rows"][0]["cells"][1].update(normalized_value=751),
            "non-monotonic": lambda item: item["rows"][2]["cells"][1].update(normalized_value=700),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            for name, mutate in mutations.items():
                payload = self.payload()
                mutate(payload)
                path = self.write_payload(directory, payload, f"{name}.json")
                with self.subTest(name=name):
                    with self.assertRaises(OcrValidationError):
                        self.extract(path)

            duplicate = self.payload()
            duplicate["rows"][2] = json.loads(json.dumps(duplicate["rows"][0]))
            duplicate["anchors"][1] = {
                "row_index": 3,
                "label": "Rank",
                "bbox": [340, 20, 500, 60],
                "raw_text": "100",
                "normalized_value": 100,
            }
            with self.assertRaises(OcrValidationError):
                self.extract(self.write_payload(directory, duplicate, "duplicate.json"))

    def test_exact_score_monotonicity_is_independent_of_uncertain_sibling_rank(self):
        from scripts.adapters.ocr_rows import OcrValidationError

        payload = self.payload()
        payload["rows"][1]["cells"][1].update(
            raw_text="660",
            normalized_value=660,
            confidence=0.99,
            verified=True,
        )
        payload["rows"][1]["cells"][2].update(
            raw_text="150",
            normalized_value=150,
            confidence=0.50,
            verified=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_payload(
                Path(temporary).resolve(), payload, "field-specific-monotonicity.json"
            )
            with self.assertRaises(OcrValidationError):
                self.extract(path)

    def test_fractional_score_matches_task3_number_parity_but_not_integral_coverage(self):
        from scripts.adapters import CellStatus

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            payload = self.payload()
            payload["rows"][0]["cells"][1].update(
                raw_text="650.5",
                normalized_value=650.5,
            )
            table = self.extract(self.write_payload(directory, payload, "fractional-score.json"))

        self.assertEqual(table.rows[0].values["score"], 650.5)
        self.assertIs(table.rows[0].cell_status["score"], CellStatus.EXACT)
        self.assertEqual(table.coverage.lower_score, 620)
        self.assertEqual(table.coverage.upper_score, 620)
        self.assertIn("coverage-nonintegral-score-excluded", table.warnings)

    def test_threshold_mapping_and_file_inputs_reject_ambiguous_values(self):
        from scripts.adapters import ColumnMapping, StructuredFileError
        from scripts.adapters.ocr_rows import OcrValidationError

        for value in (True, -0.1, 1.1, math.nan, "0.95"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.extract(min_exact_confidence=value)
        with self.assertRaises((TypeError, ValueError)):
            self.extract(score_scale=(0, True))
        with self.assertRaises(OcrValidationError):
            self.extract(mapping=ColumnMapping({"missing": "Missing"}))
        with self.assertRaises(StructuredFileError):
            self.extract(path="tests/fixtures/replay/ocr/rows.json")

    def test_ocr_contract_extensions_are_frozen_and_replace_validated(self):
        from scripts.adapters import CellStatus, PublicLocatorError
        from scripts.adapters.ocr_rows import OcrExtractedRow

        locations = {"score": "page[1]/image[page-1]/bbox[1,2,3,4]"}
        row = OcrExtractedRow(
            {"score": 650},
            {"score": CellStatus.EXACT},
            "page[1]/image[page-1]/bbox[1,2,5,6]",
            1,
            (),
            locations,
        )
        locations["score"] = "forged"
        self.assertEqual(row.cell_locations["score"], "page[1]/image[page-1]/bbox[1,2,3,4]")
        with self.assertRaises(TypeError):
            row.cell_locations["score"] = "forged"
        with self.assertRaises(ValueError):
            replace(row, cell_locations={"other": "page[1]/image[x]/bbox[1,2,3,4]"})
        unsafe = "page[1]/image[C:\\private\\page.png]/bbox[1,2,3,4]"
        with self.assertRaises(PublicLocatorError) as raised:
            replace(row, cell_locations={"score": unsafe})
        self.assertNotIn(unsafe, str(raised.exception))


class QrPayloadAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name).resolve()
        self.original_url = QR_FIXTURE.read_text("utf-8").strip()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_resolution(self, *, final_url="https://final.example.test/table.pdf", media_type="application/pdf"):
        from scripts.adapters.qr import resolve_qr_payload
        from scripts.downloader import DownloadResult

        destination = self.workspace / "downloaded.pdf"
        destination.write_bytes(b"PDF!")
        result = DownloadResult(
            destination,
            final_url,
            media_type,
            4,
            (self.original_url, final_url),
        )
        with mock.patch("scripts.adapters.qr.validate_public_url"), mock.patch(
            "scripts.adapters.qr.download_public_file", return_value=result
        ):
            return resolve_qr_payload(
                self.original_url,
                self.workspace,
                qr_image_source_id="qr-page-1",
                max_bytes=4096,
                timeout=7,
            )

    @mock.patch("scripts.adapters.qr.download_public_file")
    @mock.patch("scripts.adapters.qr.validate_public_url")
    def test_decoded_public_url_delegates_only_to_downloader_and_records_safe_provenance(
        self, validate_url, download
    ):
        from scripts.downloader import DownloadResult
        from scripts.adapters.qr import resolve_qr_payload

        destination = self.workspace / "downloaded.pdf"
        destination.write_bytes(b"PDF!")
        download.return_value = DownloadResult(
            destination,
            "https://final.example.test/table.pdf",
            "application/pdf",
            4,
            [
                self.original_url,
                "https://middle.example.test/file",
                "https://final.example.test/table.pdf",
            ],
        )

        resolution = resolve_qr_payload(
            f"  {self.original_url}\n",
            self.workspace,
            qr_image_source_id="qr-page-1",
            max_bytes=4096,
            timeout=7,
        )

        validate_url.assert_called_once_with(self.original_url)
        download.assert_called_once_with(
            self.original_url,
            self.workspace,
            max_bytes=4096,
            timeout=7,
        )
        self.assertEqual(resolution.qr_image_source_id, "qr-page-1")
        self.assertEqual(resolution.original_url, self.original_url)
        self.assertEqual(
            resolution.redirect_chain,
            (
                self.original_url,
                "https://middle.example.test/file",
                "https://final.example.test/table.pdf",
            ),
        )
        self.assertEqual(resolution.final_url, "https://final.example.test/table.pdf")
        self.assertEqual(resolution.media_type, "application/pdf")
        self.assertEqual(resolution.size_bytes, 4)
        self.assertEqual(resolution.downloaded_file_id, "downloaded.pdf")
        serialized = json.dumps(resolution.to_dict(), allow_nan=False)
        self.assertNotIn(str(self.workspace), serialized)

    def test_resolution_contract_rejects_forged_endpoints_ids_urls_and_metadata(self):
        from scripts.adapters.qr import QrResolution

        resolution = self.make_resolution()
        for changed in (
            {"qr_image_source_id": "C:/private/qr.png"},
            {"qr_image_source_id": "person@example.test"},
            {"original_url": "http://127.0.0.1/private"},
            {"redirect_chain": ("https://other.example.test/start", resolution.final_url)},
            {"redirect_chain": (resolution.original_url, "http://[::1]/private")},
            {"final_url": "https://other.example.test/final"},
            {"media_type": "application/pdf\r\nsecret"},
            {"size_bytes": True},
            {"size_bytes": -1},
            {"downloaded_file_id": "../private.pdf"},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises((TypeError, ValueError)):
                    replace(resolution, **changed)

    def test_resolution_is_factory_only_and_rejects_local_names_or_extension_mismatch_without_dns(self):
        from scripts.adapters.qr import QrResolution, QrResolutionError
        from scripts.downloader import DownloadResult

        direct_arguments = (
            "qr-page-1",
            "https://public.example.test/start",
            ("https://public.example.test/start", "https://public.example.test/final.pdf"),
            "https://public.example.test/final.pdf",
            "application/pdf",
            4,
            "downloaded.pdf",
        )
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS is forbidden")) as dns:
            with self.assertRaises(QrResolutionError):
                QrResolution(*direct_arguments)
            dns.assert_not_called()

        destination = self.workspace / "downloaded.pdf"
        destination.write_bytes(b"PDF!")
        forged_results = (
            DownloadResult(
                destination,
                "https://localhost/final.pdf",
                "application/pdf",
                4,
                (self.original_url, "https://localhost/final.pdf"),
            ),
            DownloadResult(
                destination,
                "https://service.localhost/final.pdf",
                "application/pdf",
                4,
                (self.original_url, "https://service.localhost/final.pdf"),
            ),
            DownloadResult(destination, self.original_url, "image/png", 4),
        )
        from scripts.adapters.qr import resolve_qr_payload

        for result in forged_results:
            with self.subTest(result=result), mock.patch(
                "scripts.adapters.qr.validate_public_url"
            ), mock.patch("scripts.adapters.qr.download_public_file", return_value=result), mock.patch(
                "socket.getaddrinfo", side_effect=AssertionError("DNS is forbidden")
            ) as dns:
                with self.assertRaises(QrResolutionError):
                    resolve_qr_payload(
                        self.original_url,
                        self.workspace,
                        qr_image_source_id="qr-page-1",
                        max_bytes=100,
                        timeout=1,
                    )
                dns.assert_not_called()

    @mock.patch("scripts.adapters.qr.download_public_file")
    def test_private_initial_and_downloader_redirect_failures_never_produce_resolution(self, download):
        from scripts.downloader import DownloadRedirectError, DownloadSecurityError
        from scripts.adapters.qr import resolve_qr_payload

        with self.assertRaises(DownloadSecurityError):
            resolve_qr_payload(
                "http://127.0.0.1/private.pdf",
                self.workspace,
                qr_image_source_id="qr-page-1",
                max_bytes=100,
                timeout=1,
            )
        download.assert_not_called()

        with mock.patch("scripts.adapters.qr.validate_public_url"):
            download.side_effect = DownloadRedirectError("synthetic malformed redirect")
            with self.assertRaises(DownloadRedirectError):
                resolve_qr_payload(
                    self.original_url,
                    self.workspace,
                    qr_image_source_id="qr-page-1",
                    max_bytes=100,
                    timeout=1,
                )

    @mock.patch("scripts.adapters.qr.download_public_file")
    @mock.patch("scripts.adapters.qr.validate_public_url")
    def test_payload_and_source_validation_rejects_extra_text_bytes_and_unsafe_forms_before_fetch(
        self, validate_url, download
    ):
        from scripts.adapters.qr import QrPayloadError, resolve_qr_payload

        cases = (
            ("", "qr-page-1"),
            (self.original_url + " https://second.example.test/file", "qr-page-1"),
            ("ftp://public.example.test/file", "qr-page-1"),
            ("https://user:pass@public.example.test/file", "qr-page-1"),
            ("https://public.example.test/file\x00extra", "qr-page-1"),
            (b"image bytes", "qr-page-1"),
            (self.original_url, "../qr.png"),
            (self.original_url, "person@example.test"),
            (self.original_url, "token=synthetic-secret"),
            (self.original_url, "qr-0000000"),
            (self.original_url, "api-key-secret"),
        )
        for payload, source_id in cases:
            with self.subTest(payload=payload, source_id=source_id):
                with self.assertRaises(QrPayloadError):
                    resolve_qr_payload(
                        payload,
                        self.workspace,
                        qr_image_source_id=source_id,
                        max_bytes=100,
                        timeout=1,
                    )
        validate_url.assert_not_called()
        download.assert_not_called()

    @mock.patch("scripts.adapters.qr.download_public_file")
    @mock.patch("scripts.adapters.qr.validate_public_url")
    def test_structured_phone_and_secret_source_ids_fail_before_network_seams(
        self, validate_url, download
    ):
        from scripts.adapters.qr import QrPayloadError, resolve_qr_payload

        for source_id in ("phone-138-0013-8000", "sk-live"):
            with self.subTest(source_id=source_id):
                with self.assertRaises(QrPayloadError):
                    resolve_qr_payload(
                        self.original_url,
                        self.workspace,
                        qr_image_source_id=source_id,
                        max_bytes=100,
                        timeout=1,
                    )
        validate_url.assert_not_called()
        download.assert_not_called()

    @mock.patch("scripts.adapters.qr.download_public_file")
    @mock.patch("scripts.adapters.qr.validate_public_url")
    def test_download_result_must_match_workspace_file_chain_and_size(self, validate_url, download):
        from scripts.downloader import DownloadResult
        from scripts.adapters.qr import QrResolutionError, resolve_qr_payload

        outside = ROOT / "outside.pdf"
        results = (
            DownloadResult(outside, self.original_url, "application/pdf", 0),
            DownloadResult(self.workspace / "missing.pdf", self.original_url, "application/pdf", 0),
        )
        for result in results:
            download.return_value = result
            with self.subTest(path=result.path):
                with self.assertRaises(QrResolutionError):
                    resolve_qr_payload(
                        self.original_url,
                        self.workspace,
                        qr_image_source_id="qr-page-1",
                        max_bytes=100,
                        timeout=1,
                    )

        wrong_size = self.workspace / "wrong-size.pdf"
        wrong_size.write_bytes(b"data")
        download.return_value = DownloadResult(
            wrong_size, self.original_url, "application/pdf", 3
        )
        with self.assertRaises(QrResolutionError):
            resolve_qr_payload(
                self.original_url,
                self.workspace,
                qr_image_source_id="qr-page-1",
                max_bytes=100,
                timeout=1,
            )

    @mock.patch("scripts.adapters.qr.download_public_file")
    @mock.patch("scripts.adapters.qr.validate_public_url")
    def test_non_path_download_result_fails_before_file_or_resolution_construction(
        self, validate_url, download
    ):
        from scripts.adapters.qr import QrResolution, QrResolutionError, resolve_qr_payload
        from scripts.downloader import DownloadResult

        download.return_value = DownloadResult(
            "not-a-path",
            self.original_url,
            "application/pdf",
            4,
        )
        path_calls = []
        original_resolve = Path.resolve

        def tracking_resolve(path, *args, **kwargs):
            path_calls.append(path)
            return original_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", tracking_resolve), mock.patch.object(
            QrResolution,
            "_from_download",
            side_effect=AssertionError("resolution construction is forbidden"),
        ) as construct:
            with self.assertRaises(QrResolutionError) as raised:
                resolve_qr_payload(
                    self.original_url,
                    self.workspace,
                    qr_image_source_id="qr-page-1",
                    max_bytes=100,
                    timeout=1,
                )
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(path_calls, [])
        construct.assert_not_called()


class UnstructuredImportBoundaryTest(unittest.TestCase):
    def test_package_and_flat_imports_perform_no_file_or_network_io(self):
        code = f"""
import pathlib, socket, sys
sys.path.insert(0, {str(ROOT)!r})
def blocked(*args, **kwargs):
    raise AssertionError('I/O during import')
pathlib.Path.open = blocked
pathlib.Path.read_bytes = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
import scripts.adapters.pdf_text
import scripts.adapters.ocr_rows
import scripts.adapters.qr
import scripts.adapters.pathway_extraction
import scripts.adapters.pathway_bridge
for name in tuple(sys.modules):
    if name == 'adapters' or name.startswith('adapters.'):
        sys.modules.pop(name)
sys.path.insert(0, {str(ROOT / 'scripts')!r})
import adapters.pdf_text
import adapters.ocr_rows
import adapters.qr
import adapters.pathway_extraction
import adapters.pathway_bridge
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


class PathwayUnstructuredProjectionTest(unittest.TestCase):
    @staticmethod
    def _pdf_projection(*, trailing_page):
        from scripts.adapters.pathway_extraction import extract_pathway_policy
        from scripts.adapters.pdf_text import PdfTextDocument, PdfTextPage
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            plan,
            policy_values,
            profile,
            task_for,
        )

        values = policy_values()
        pages = (
            PdfTextPage(
                1,
                "\n".join(str(values[field]) for field in POLICY_FIELDS),
                "pdfplumber-text",
            ),
            trailing_page,
        )
        warnings = (
            ("image-only-pages-present",)
            if trailing_page.image_only
            else ()
        )
        document = PdfTextDocument(
            "sha256:" + "e" * 64,
            len(pages),
            pages,
            warnings,
        )
        field_map = {
            field: (1, str(values[field])) for field in POLICY_FIELDS
        }
        student = profile()
        query_plan = plan(student)
        return extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task_for(query_plan),
            extraction=document,
            field_map=field_map,
            candidates=(candidate(),),
        )

    def test_unselected_image_only_pdf_page_forces_global_partial_coverage(self):
        from scripts.adapters.pdf_text import PdfTextPage
        from scripts.contracts import EvidenceStatus

        projection = self._pdf_projection(
            trailing_page=PdfTextPage(
                2,
                "",
                "none",
                ("image-only",),
                True,
            )
        )

        self.assertIs(projection.evidence_status, EvidenceStatus.PARTIAL)
        self.assertEqual(projection.coverage_status, "partial")
        self.assertIn("image-only-pages-present", projection.warnings)

    def test_unselected_clean_text_pdf_page_keeps_complete_coverage(self):
        from scripts.adapters.pdf_text import PdfTextPage
        from scripts.contracts import EvidenceStatus

        projection = self._pdf_projection(
            trailing_page=PdfTextPage(2, "公开附录", "pdfplumber-text")
        )

        self.assertIs(projection.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(projection.coverage_status, "complete")

    def test_pdf_pages_supply_exact_field_provenance_without_local_paths(self):
        from scripts.adapters.pathway_extraction import extract_pathway_policy
        from scripts.adapters.pdf_text import PdfTextDocument, PdfTextPage
        from scripts.contracts import EvidenceStatus
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            plan,
            policy_values,
            profile,
            task_for,
        )

        values = policy_values()
        pages = tuple(
            PdfTextPage(index, str(values[field]), "pdfplumber-text")
            for index, field in enumerate(POLICY_FIELDS, start=1)
        )
        document = PdfTextDocument(
            "sha256:" + "d" * 64,
            len(pages),
            pages,
        )
        field_map = {
            field: (index, str(values[field]))
            for index, field in enumerate(POLICY_FIELDS, start=1)
        }
        student = profile()
        query_plan = plan(student)
        projection = extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task_for(query_plan),
            extraction=document,
            field_map=field_map,
            candidates=(candidate(),),
        )
        self.assertIs(projection.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertTrue(
            all(
                locator.startswith("page[")
                for item in projection.field_provenance
                for locator in item.locators
            )
        )
        self.assertNotIn(str(ROOT), json.dumps(projection.to_dict(), ensure_ascii=False))

    def test_ocr_output_preserves_each_verified_cell_locator(self):
        from scripts.adapters import CellStatus, ExtractedCoverage
        from scripts.adapters.ocr_rows import OcrExtractedRow, OcrExtractedTable
        from scripts.adapters.pathway_extraction import extract_pathway_policy
        from scripts.contracts import EvidenceStatus
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            plan,
            policy_values,
            profile,
            task_for,
        )

        values = policy_values()
        locations = {
            field: f"page[1]/image[pathway-page]/bbox[{index},1,{index + 1},2]"
            for index, field in enumerate(POLICY_FIELDS, start=1)
        }
        row = OcrExtractedRow(
            values,
            {field: CellStatus.EXACT for field in POLICY_FIELDS},
            "page[1]/image[pathway-page]/bbox[1,1,30,3]",
            1,
            (),
            locations,
        )
        table = OcrExtractedTable(
            "pathway-ocr",
            None,
            None,
            (row,),
            ExtractedCoverage(),
            (),
            "host-ocr-rows",
            {"columns": {field: [field] for field in POLICY_FIELDS}},
        )
        student = profile()
        query_plan = plan(student)
        projection = extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task_for(query_plan),
            extraction=table,
            field_map={field: field for field in POLICY_FIELDS},
            candidates=(candidate(),),
        )
        self.assertIs(projection.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(
            {item.field: item.locators[0] for item in projection.field_provenance},
            locations,
        )


if __name__ == "__main__":
    unittest.main()
