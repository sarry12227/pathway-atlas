"""Synthetic PDF policy excerpts retain real fields without inventing omissions."""
from dataclasses import replace
import unittest

from scripts.adapters.pdf_text import PdfTextDocument, PdfTextPage
from scripts.adapters.pathway_extraction import PathwayExtractionError
from scripts.contracts import EvidenceStatus
from tests.test_pathway_evidence_bridge import project, candidate, profile, plan, task_for


class PartialPdfPolicyTest(unittest.TestCase):
    def test_partial_pdf_keeps_read_fields_and_missing_conditions(self):
        student = profile()
        query_plan = plan(student)
        task = task_for(query_plan)
        for method in ("pdfplumber-text", "pypdf-text"):
            with self.subTest(method=method):
                document = PdfTextDocument("sha256:" + "a" * 64, 1,
                    (PdfTextPage(1, f"合成示例高校\n{task.year}年招生简章", method),))
                projection = project(student=student, query_plan=query_plan, task=task,
                    extraction=document, field_map={"institution": [1, "合成示例高校"],
                                                    "year": [1, str(task.year)]},
                    candidates=(replace(candidate(), content_hash=document.document_id),))
                self.assertEqual(projection.institution, "合成示例高校")
                self.assertEqual(projection.data_year, task.year)
                self.assertIsNone(projection.eligibility_requirements)
                self.assertIsNone(projection.fees_and_subsidies)
                missing = next(p for p in projection.field_provenance if p.field == "eligibility_requirements")
                self.assertEqual(missing.status, EvidenceStatus.MISSING)
                present = next(p for p in projection.field_provenance if p.field == "institution")
                self.assertEqual(present.extraction_methods, (method,))
                self.assertEqual(projection.input_projection["documents"][0]["extraction_method"], method)

    def test_unseen_or_ambiguous_pdf_quote_cannot_become_an_institution_fact(self):
        document = PdfTextDocument("sha256:" + "b" * 64, 1,
            (PdfTextPage(1, "合成高校 合成高校", "pdfplumber-text"),))
        for quote in ("未出现高校", "合成高校"):
            with self.subTest(quote=quote):
                result = project(extraction=document, field_map={"institution": [1, quote]},
                    candidates=(replace(candidate(), content_hash=document.document_id),))
                self.assertIsNone(result.institution)
        with self.assertRaises(PathwayExtractionError):
            project(extraction=document, field_map={"invented_field": [1, "合成高校"]})


if __name__ == "__main__":
    unittest.main()
