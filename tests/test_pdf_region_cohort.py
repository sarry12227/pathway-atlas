"""Synthetic PDF regions must not become a fabricated provincial cohort."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.adapters.rank_bridge import bridge_rank_evidence
from scripts.adapters.pdf_table import extract_pdf_table
from scripts.contracts import EvidenceStatus
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.planning_profile import PlanningProfile
from scripts.rank_locator import locate_rank
from scripts.research_snapshot import build_research_snapshot
from tests.test_pdf_table_fallback import HEADERS, MAPPING, synthetic_pdf
from tests.test_rank_evidence_bridge import candidate, plan, score_table
from tests.test_research_snapshot import joint_bridge, school_bridge
from tests.test_school_score_calibration import school_student


def pdf_region_bridge(student, query_plan):
    task = next(
        item for item in query_plan.tasks
        if item.kind == "score_table" and item.year == query_plan.research_year
    )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "synthetic.pdf"
        synthetic_pdf(path, [[
            "Score Count Cumulative", "600 100 50000", "599 120 50120",
        ]])
        table = extract_pdf_table(
            path, mapping=MAPPING, headers=HEADERS, page_number=1,
            header_line=1, first_data_line=2, last_data_line=3,
        )
        source = replace(
            candidate("synthetic-regional-pdf"),
            content_hash="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return bridge_rank_evidence(
        profile=student, plan=query_plan, task=task, table=table,
        extracted_row=table.rows[0], candidates=(source,),
        coverage_status=EvidenceStatus.OFFICIAL,
    )


def prior_complete_table_bridge(student, query_plan):
    task = next(
        item for item in query_plan.tasks
        if item.kind == "score_table" and item.year == query_plan.research_year - 1
    )
    table = score_table()
    return bridge_rank_evidence(
        profile=student, plan=query_plan, task=task, table=table,
        extracted_row=table.rows[0], candidates=(candidate("synthetic-full-table"),),
        coverage_status=EvidenceStatus.OFFICIAL,
    )


class PdfRegionCohortTest(unittest.TestCase):
    def test_partial_pdf_cannot_project_profile_joint_rank_using_its_last_row(self):
        student = school_student(scope="city_joint", source="joint_exam_report", rank=120)
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student, query_plan, (pdf_region_bridge(student, query_plan),),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.MISSING)
        self.assertIsNone(scenario.central_rank)
        self.assertIn("official_cohort_size_missing", scenario.reasons)

    def test_partial_pdf_cannot_scale_authenticated_joint_channel_through_row_fallback(self):
        student = school_student(rank=120)
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student, query_plan,
            (
                pdf_region_bridge(student, query_plan),
                joint_bridge(student, query_plan, 2025, 80, 120, 160),
            ),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.MISSING)
        self.assertIsNone(scenario.central_rank)

    def test_partial_pdf_still_maps_matching_official_score_to_its_exact_table_row(self):
        payload = school_student(scope="province_official", source="official_score").to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload.update(grade="高三", exam_year=2026)
        payload["rank_observations"][0]["exam_date"] = "2026-06-07"
        student = PlanningProfile.create(payload)
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student, query_plan, (pdf_region_bridge(student, query_plan),),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.central_rank, 50000)
        self.assertIn("synthetic-regional-pdf", scenario.source_ids)

    def test_partial_pdf_does_not_override_a_separate_complete_cohort_table(self):
        student = school_student(scope="city_joint", source="joint_exam_report", rank=120)
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student, query_plan,
            (pdf_region_bridge(student, query_plan), prior_complete_table_bridge(student, query_plan)),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.central_rank, 24000)
        self.assertIn("synthetic-full-table", scenario.source_ids)
        self.assertNotIn("synthetic-regional-pdf", scenario.source_ids)

    def test_absolute_school_anchor_calibration_does_not_need_a_pdf_cohort(self):
        student = school_student(rank=120)
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student, query_plan,
            (
                pdf_region_bridge(student, query_plan),
                school_bridge(student, query_plan, 2025, 46000),
                school_bridge(student, query_plan, 2026, 50000),
            ),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.central_rank, 48000)


if __name__ == "__main__":
    unittest.main()
