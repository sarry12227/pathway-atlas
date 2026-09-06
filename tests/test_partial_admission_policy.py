"""Keep partial admission rows nonnumeric at the public report boundary."""

from copy import copy
from dataclasses import fields, replace
import unittest

from scripts.contracts import EvidenceStatus
from scripts.report_model import ReportModel, build_report_model, render_markdown
from tests.test_generate_report_evidence import (
    evidence_snapshot,
    recommendations,
    student,
)


class PartialAdmissionReportContractTest(unittest.TestCase):
    def build(self):
        return build_report_model(
            profile=student(),
            recommendations=recommendations(),
            rank=None,
            pathways=None,
            evidence=evidence_snapshot(),
        )

    def test_report_recommendation_rejects_partial_numeric_status(self):
        report = self.build()
        with self.assertRaisesRegex(ValueError, "numeric recommendations"):
            replace(report.recommendations[0], evidence_status=EvidenceStatus.PARTIAL)

    def test_report_model_revalidates_partial_numeric_nested_record(self):
        report = self.build()
        partial = copy(report.recommendations[0])
        object.__setattr__(partial, "evidence_status", EvidenceStatus.PARTIAL)
        values = {field.name: getattr(report, field.name) for field in fields(report)}
        values["recommendations"] = (partial,)
        with self.assertRaisesRegex(ValueError, "numeric recommendations"):
            ReportModel._create(**values)

    def test_exact_row_remains_numeric_in_partial_aggregate_report(self):
        report = self.build()
        self.assertEqual(report.recommendation_coverage_status, EvidenceStatus.PARTIAL)
        self.assertEqual(report.recommendations[0].evidence_status, EvidenceStatus.REFERENCE)
        self.assertIn("| 645 | 4300 |", render_markdown(report))


if __name__ == "__main__":
    unittest.main()
