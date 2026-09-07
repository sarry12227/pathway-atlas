"""Synthetic program regressions; these are not real student/AI acceptance runs."""

from __future__ import annotations

import unittest

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.adapters.rank_bridge import bridge_rank_evidence
from scripts.contracts import EvidenceStatus, SourceTier
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.planning_profile import PlanningProfile
from scripts.rank_locator import locate_rank
from scripts.research_snapshot import build_research_snapshot
from scripts.school_recommend import SchoolRecommendError, personalize_school_recommendations
from tests.test_rank_evidence_bridge import candidate, plan, profile
from tests.test_research_snapshot import school_bridge


def school_student(*, score=600, rank=None, scope="school", source="school_report"):
    payload = profile().to_dict()
    payload.pop("mode")
    payload.pop("digest")
    payload.update(
        province="浙江",
        city="杭州",
        high_school="合成校内考试示例中学",
        grade="高一",
        exam_year=2029,
        subject_mode="3+3",
        subject_group="物理",
        secondary_subjects=["地理", "技术"],
        score_basis="赋分",
        best_rank=None,
        usual_rank=None,
        rank_observations=[
            {
                "exam_date": "2026-09-01",
                "scope": scope,
                "score": score,
                "max_score": 750,
                "rank": rank,
                "cohort_size": 1000 if rank is not None else None,
                "source": source,
            }
        ],
    )
    return PlanningProfile.create(payload)


def synthetic_score_bridge(student, query_plan, *, reference=False):
    """Authenticate exact invented test rows without fetching public data."""
    task = next(
        item
        for item in query_plan.tasks
        if item.kind == "score_table" and item.year == query_plan.research_year
    )
    rows = tuple(
        ExtractedRow(
            values={"score": score, "rank": rank, "cumulative_count": rank},
            cell_status={
                name: CellStatus.EXACT for name in ("score", "rank", "cumulative_count")
            },
            location=f"table[1]/tbody/tr[{index}]",
            confidence=1,
        )
        for index, (score, rank) in enumerate(((600, 50000), (100, 200000)), 1)
    )
    table = ExtractedTable(
        table_id="table[1]",
        caption="合成一分一段测试表",
        sheet=None,
        rows=rows,
        coverage=ExtractedCoverage(
            lower_score=100, upper_score=600, lower_rank=50000, upper_rank=200000
        ),
        warnings=(),
        extraction_method="html-table",
    )
    sources = (
        tuple(
            candidate(
                f"synthetic-reference-{index}",
                tier=SourceTier.C,
                publisher=f"合成独立测试发布方{index}",
                host=f"synthetic-reference-{index}.example.cn",
            )
            for index in range(1, 4)
        )
        if reference
        else (
            candidate(
                "synthetic-zhejiang-score",
                publisher="浙江省教育考试院",
                host="www.zjzs.net",
            ),
        )
    )
    return bridge_rank_evidence(
        profile=student,
        plan=query_plan,
        task=task,
        table=table,
        extracted_row=rows[0],
        candidates=sources,
        coverage_status=EvidenceStatus.REFERENCE if reference else EvidenceStatus.OFFICIAL,
    )


class SchoolScoreCalibrationTest(unittest.TestCase):
    def test_monthly_600_of_750_cannot_become_provincial_rank_or_school_strategy(self):
        for school_rank in (None, 120):
            for reference in (False, True):
                with self.subTest(school_rank=school_rank, reference=reference):
                    student = school_student(rank=school_rank)
                    query_plan = plan(student)
                    snapshot = build_research_snapshot(
                        student,
                        query_plan,
                        (synthetic_score_bridge(student, query_plan, reference=reference),),
                        DecisionPolicySnapshot.load_default(),
                    )
                    # The official/reference table is usable context, but its
                    # 600-point rank is not a calibration of a monthly exam.
                    self.assertEqual(snapshot.score_rows[0].rank, 50000)
                    scenario = locate_rank(student, research_snapshot=snapshot)
                    self.assertEqual(scenario.status, EvidenceStatus.MISSING)
                    self.assertIsNone(scenario.optimistic_rank)
                    self.assertIsNone(scenario.central_rank)
                    self.assertIsNone(scenario.conservative_rank)
                    self.assertEqual(scenario.confidence, "none")
                    with self.assertRaises(SchoolRecommendError) as rejected:
                        personalize_school_recommendations(
                            [
                                {
                                    "year": query_plan.research_year,
                                    "school_name": "合成大学甲",
                                    "min_rank": 50000,
                                    "min_score": 600,
                                }
                            ],
                            student,
                            rank_scenario=scenario,
                        )
                    self.assertEqual(rejected.exception.code, "REC_001")

    def test_source_label_does_not_turn_school_scope_into_an_official_exam(self):
        student = school_student(source="official_score")
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (synthetic_score_bridge(student, query_plan),),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.MISSING)
        self.assertIsNone(scenario.central_rank)

    def test_official_exam_score_can_still_use_authenticated_score_table(self):
        original = school_student(scope="province_official", source="official_score")
        payload = original.to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload.update(grade="高三", exam_year=2026)
        payload["rank_observations"][0]["exam_date"] = "2026-06-07"
        student = PlanningProfile.create(payload)
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (synthetic_score_bridge(student, query_plan),),
            DecisionPolicySnapshot.load_default(),
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.central_rank, 50000)
        self.assertIn("profile-reported-score", scenario.source_ids)

    def test_joint_exam_needs_rank_and_cohort_instead_of_its_raw_score(self):
        for scope in ("province_joint", "city_joint"):
            for rank, expected_rank in ((None, None), (120, 24000)):
                with self.subTest(scope=scope, rank=rank):
                    student = school_student(
                        scope=scope, source="joint_exam_report", rank=rank
                    )
                    query_plan = plan(student)
                    snapshot = build_research_snapshot(
                        student,
                        query_plan,
                        (synthetic_score_bridge(student, query_plan),),
                        DecisionPolicySnapshot.load_default(),
                    )
                    scenario = locate_rank(student, research_snapshot=snapshot)
                    self.assertEqual(scenario.central_rank, expected_rank)
                    self.assertEqual(
                        scenario.status,
                        EvidenceStatus.MISSING if rank is None else EvidenceStatus.INFERRED,
                    )

    def test_school_anchor_calibration_uses_school_rank_and_not_monthly_score(self):
        for score in (400, 600):
            with self.subTest(monthly_score=score):
                student = school_student(score=score, rank=120)
                query_plan = plan(student)
                snapshot = build_research_snapshot(
                    student,
                    query_plan,
                    (
                        school_bridge(student, query_plan, 2025, 46000),
                        school_bridge(student, query_plan, 2026, 50000),
                    ),
                    DecisionPolicySnapshot.load_default(),
                )
                scenario = locate_rank(student, research_snapshot=snapshot)
                self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
                self.assertEqual(scenario.central_rank, 48000)
                self.assertIn("student_score_not_used_no_versioned_model", scenario.reasons)


if __name__ == "__main__":
    unittest.main()
