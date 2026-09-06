from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import importlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.adapters.admission_bridge import bridge_admission_evidence
from scripts.adapters.rank_bridge import bridge_rank_evidence
from scripts.contracts import EvidenceStatus, SourceTier
from scripts.contracts import CapabilityReport, CapabilityTier
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.evidence import EvidenceStore
from scripts.planning_profile import PlanningProfile
from scripts.validate_data import ValidatedAdmissionRow
from scripts.validate_evidence import validate_bundle_snapshot
from tests.test_rank_evidence_bridge import (
    candidate,
    plan,
    profile,
    score_table,
    unresolved_official_score_profile,
)


def bridges():
    student = profile()
    query_plan = plan(student)
    score_task = next(
        item
        for item in query_plan.tasks
        if item.kind == "score_table" and item.year == 2025
    )
    score_source = score_table()
    rank_bridge = bridge_rank_evidence(
        profile=student,
        plan=query_plan,
        task=score_task,
        table=score_source,
        extracted_row=score_source.rows[0],
        candidates=(candidate(),),
        coverage_status=EvidenceStatus.OFFICIAL,
    )

    admission_task = next(
        item
        for item in query_plan.tasks
        if item.kind == "batch_admission"
        and item.target_name == "普通批"
        and item.year == 2025
    )
    dataset_row = ValidatedAdmissionRow.from_mapping(
        {
            "year": 2025,
            "province": student.province,
            "subject_group": query_plan.subject_group,
            "school_code": "SYN-A01",
            "school_name": "合成示例大学",
            "program_group": "第01组",
            "min_score": 605,
            "min_rank": 20000,
            "remarks": "",
        }
    )
    adapter_values = {
        "school_code": "SYN-A01",
        "school_name": "合成示例大学",
        "program_group": "第01组",
        "min_score": 605,
        "min_rank": 20000,
    }
    adapter_row = ExtractedRow(
        values=adapter_values,
        cell_status={name: CellStatus.EXACT for name in adapter_values},
        location="table[1]/tbody/tr[2]",
        confidence=1,
    )
    lower_rank_boundary_values = {
        "school_code": "SYN-A00",
        "school_name": "合成高分边界大学",
        "program_group": "第01组",
        "min_score": 615,
        "min_rank": 17000,
    }
    lower_rank_boundary = ExtractedRow(
        values=lower_rank_boundary_values,
        cell_status={
            name: CellStatus.EXACT for name in lower_rank_boundary_values
        },
        location="table[1]/tbody/tr[1]",
        confidence=1,
    )
    upper_rank_boundary_values = {
        "school_code": "SYN-A02",
        "school_name": "合成低分边界大学",
        "program_group": "第01组",
        "min_score": 595,
        "min_rank": 23000,
    }
    upper_rank_boundary = ExtractedRow(
        values=upper_rank_boundary_values,
        cell_status={
            name: CellStatus.EXACT for name in upper_rank_boundary_values
        },
        location="table[1]/tbody/tr[3]",
        confidence=1,
    )
    table = ExtractedTable(
        table_id="table[1]",
        caption="普通批投档",
        sheet=None,
        rows=(lower_rank_boundary, adapter_row, upper_rank_boundary),
        coverage=ExtractedCoverage(
            lower_score=595,
            upper_score=615,
            lower_rank=17000,
            upper_rank=23000,
        ),
        warnings=(),
        extraction_method="html-table",
    )
    admission_bridge = bridge_admission_evidence(
        table=table,
        adapter_row=adapter_row,
        task=admission_task,
        dataset_row=dataset_row,
        fact_id="admission-2025-syn-a01",
        candidates=(
            candidate(
                "official-admission",
                publisher="湖北省普通批发布机关",
                host="admission.hubei.gov.cn",
            ),
        ),
        coverage_status=EvidenceStatus.OFFICIAL,
    )
    return student, query_plan, rank_bridge, admission_bridge


def joint_bridge(
    student,
    query_plan,
    year: int,
    lower: int,
    central: int,
    upper: int,
    *,
    cohort_size: int = 1000,
    rank_scope: str = "city_joint",
    exam_date: str | None = None,
):
    task = next(
        item
        for item in query_plan.tasks
        if item.kind == "joy_report" and item.year == year
    )
    values = {
        "scope": "joint_exam",
        "rank_scope": rank_scope,
        "exam_date": exam_date or f"{year}-06-01",
        "lower_rank": lower,
        "central_rank": central,
        "upper_rank": upper,
        "cohort_size": cohort_size,
    }
    row = ExtractedRow(
        values=values,
        cell_status={name: CellStatus.EXACT for name in values},
        location="table[1]/tbody/tr[1]",
        confidence=1,
    )
    table = ExtractedTable(
        table_id="table[1]",
        caption="联考位次区间",
        sheet=None,
        rows=(row,),
        coverage=ExtractedCoverage(lower_rank=1, upper_rank=cohort_size),
        warnings=(),
        extraction_method="html-table",
    )
    return bridge_rank_evidence(
        profile=student,
        plan=query_plan,
        task=task,
        table=table,
        extracted_row=row,
        candidates=(
            candidate(
                f"joint-{year}",
                publisher=f"联考发布方{year}",
                host=f"joint-{year}.example.cn",
            ),
        ),
        coverage_status=EvidenceStatus.OFFICIAL,
    )


def school_bridge(
    student,
    query_plan,
    year: int,
    province_rank: int,
    *,
    school_name: str | None = None,
    class_level: str | None = None,
    comparability_basis: str | None = None,
    explicit_comparability: bool = True,
):
    task = next(
        item
        for item in query_plan.tasks
        if item.kind == "joy_report" and item.year == year
    )
    values = {
        "scope": "school_anchor",
        "school_name": student.high_school if school_name is None else school_name,
        "class_level": student.class_level if class_level is None else class_level,
        "school_rank": 120,
        "province_rank": province_rank,
        "school_score": 610,
        "max_score": 750,
        "cohort_size": 1000,
    }
    actual_school = values["school_name"]
    actual_class = values["class_level"]
    if (
        explicit_comparability
        and actual_school == student.high_school
        and actual_class != student.class_level
    ):
        whole_school = actual_class in {"全校", "校级", "全年级"}
        values.update(
            {
                "comparability_tier": "same_school",
                "comparability_basis": (
                    "authenticated_same_school_whole_school_cohort"
                    if whole_school
                    else "authenticated_same_school_other_class_cohort"
                ),
            }
        )
    elif comparability_basis is not None:
        values.update(
            {
                "comparability_tier": "regional_similar",
                "comparability_basis": comparability_basis,
            }
        )
    row = ExtractedRow(
        values=values,
        cell_status={name: CellStatus.EXACT for name in values},
        location="table[1]/tbody/tr[1]",
        confidence=1,
    )
    table = ExtractedTable(
        table_id="table[1]",
        caption="学校成绩锚点",
        sheet=None,
        rows=(row,),
        coverage=ExtractedCoverage(lower_rank=1, upper_rank=1000),
        warnings=(),
        extraction_method="html-table",
    )
    return bridge_rank_evidence(
        profile=student,
        plan=query_plan,
        task=task,
        table=table,
        extracted_row=row,
        candidates=(candidate(f"school-anchor-{year}"),),
        coverage_status=EvidenceStatus.OFFICIAL,
    )


def provincial_cohort_bridge(student, query_plan, source_id: str = "provincial-cohort"):
    task = next(
        item
        for item in query_plan.tasks
        if item.kind == "score_table" and item.year == query_plan.research_year
    )
    table = score_table()
    return bridge_rank_evidence(
        profile=student,
        plan=query_plan,
        task=task,
        table=table,
        extracted_row=table.rows[0],
        candidates=(candidate(source_id),),
        coverage_status=EvidenceStatus.OFFICIAL,
    )


class ProvinceResearchSnapshotTest(unittest.TestCase):
    def test_shared_admission_fixture_coverage_matches_exact_boundary_rows(self):
        _student, _query_plan, _rank_bridge, admission_bridge = bridges()
        table = admission_bridge.table
        ranks = tuple(int(row.values["min_rank"]) for row in table.rows)
        scores = tuple(int(row.values["min_score"]) for row in table.rows)

        self.assertEqual(
            (table.coverage.lower_rank, table.coverage.upper_rank),
            (min(ranks), max(ranks)),
        )
        self.assertEqual(
            (table.coverage.lower_score, table.coverage.upper_score),
            (min(scores), max(scores)),
        )
        self.assertTrue(
            all(
                all(status is CellStatus.EXACT for status in row.cell_status.values())
                for row in table.rows
            )
        )
        self.assertEqual(
            admission_bridge.adapter_row.values["school_name"],
            "合成示例大学",
        )
        self.assertEqual(admission_bridge.adapter_row.values["min_rank"], 20000)

    def test_public_rank_locator_is_snapshot_only(self):
        from scripts.rank_locator import locate_rank

        parameters = inspect.signature(locate_rank).parameters
        self.assertEqual(tuple(parameters), ("profile", "research_snapshot"))
        self.assertEqual(
            parameters["research_snapshot"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIs(
            parameters["research_snapshot"].default,
            inspect.Parameter.empty,
        )
        with self.assertRaises(TypeError):
            locate_rank(profile(), evidence_facts=(), score_rows=())

    def test_typed_school_bridges_build_an_authenticated_anchor_snapshot(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        student = profile()
        query_plan = plan(student)
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (
                school_bridge(student, query_plan, 2025, 18000),
                school_bridge(student, query_plan, 2026, 19000),
            ),
            DecisionPolicySnapshot.load_default(),
        )

        self.assertEqual(len(snapshot.rank_facts), 2)
        self.assertTrue(
            all(fact["field"].startswith("rank_anchor:") for fact in snapshot.rank_facts)
        )
        scenario = locate_rank(student, research_snapshot=snapshot)
        self.assertEqual(
            (
                scenario.optimistic_rank,
                scenario.central_rank,
                scenario.conservative_rank,
            ),
            (16108, 18520, 20872),
        )
        self.assertIn("profile_best_rank_bound", scenario.reasons)
        self.assertIn("profile_usual_rank_anchor", scenario.reasons)
        self.assertIn("profile_source_user_reported", scenario.reasons)

    def test_school_anchor_fallback_tiers_lower_confidence_and_widen_intervals(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        payload = profile().to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload["rank_observations"][0]["source"] = "official_score"
        student = PlanningProfile.create(payload)
        query_plan = plan(student)
        years = tuple(query_plan.research_year - offset for offset in range(3))
        cases = (
            (
                "exact",
                student.high_school,
                student.class_level,
                "school_anchor_fallback_exact_class",
                "high",
                None,
            ),
            (
                "same_school",
                student.high_school,
                "普通班",
                "school_anchor_fallback_same_school",
                "medium",
                None,
            ),
            (
                "regional",
                "武汉市相似中学",
                "重点班",
                "school_anchor_fallback_regional_similar",
                "low",
                "authenticated_similar_school_cohort",
            ),
        )
        widths = {}
        for (
            label,
            school_name,
            class_level,
            reason,
            confidence,
            comparability_basis,
        ) in cases:
            with self.subTest(tier=label):
                snapshot = build_research_snapshot(
                    student,
                    query_plan,
                    tuple(
                        school_bridge(
                            student,
                            query_plan,
                            year,
                            18000,
                            school_name=school_name,
                            class_level=class_level,
                            comparability_basis=comparability_basis,
                        )
                        for year in years
                    ),
                    DecisionPolicySnapshot.load_default(),
                )
                scenario = locate_rank(student, research_snapshot=snapshot)
                self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
                self.assertIsNotNone(scenario.central_rank)
                self.assertEqual(scenario.confidence, confidence)
                self.assertIn(reason, scenario.reasons)
                self.assertEqual(len(scenario.source_ids), 3)
                widths[label] = (
                    scenario.conservative_rank - scenario.optimistic_rank
                )

        self.assertLess(widths["exact"], widths["same_school"])
        self.assertLess(widths["same_school"], widths["regional"])

    def test_school_anchor_fallback_exhausts_stronger_tier_before_next_tier(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        student = profile()
        query_plan = plan(student)
        years = tuple(query_plan.research_year - offset for offset in range(3))
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (
                school_bridge(
                    student,
                    query_plan,
                    years[0],
                    18000,
                ),
                *tuple(
                    school_bridge(
                        student,
                        query_plan,
                        year,
                        19000,
                        class_level="普通班",
                    )
                    for year in years
                ),
            ),
            DecisionPolicySnapshot.load_default(),
        )

        scenario = locate_rank(student, research_snapshot=snapshot)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertIn("school_anchor_fallback_same_school", scenario.reasons)
        self.assertNotIn("school_anchor_fallback_exact_class", scenario.reasons)

    def test_regional_school_anchor_requires_explicit_comparability_evidence(self):
        from scripts.adapters.rank_bridge import RankBridgeError

        student = profile()
        query_plan = plan(student)
        with self.assertRaisesRegex(RankBridgeError, "explicit comparability"):
            school_bridge(
                student,
                query_plan,
                query_plan.research_year,
                18000,
                school_name="武汉市未认证相似中学",
                class_level="重点班",
            )

    def test_broadened_school_anchors_require_explicit_comparability_evidence(self):
        from scripts.adapters.rank_bridge import RankBridgeError

        student = profile()
        query_plan = plan(student)
        for class_level in ("普通班", "全年级"):
            with self.subTest(class_level=class_level):
                with self.assertRaisesRegex(RankBridgeError, "explicit comparability"):
                    school_bridge(
                        student,
                        query_plan,
                        query_plan.research_year,
                        18000,
                        class_level=class_level,
                        explicit_comparability=False,
                    )

    def test_all_authenticated_rank_intervals_are_intersected_before_return(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        student = profile()
        query_plan = plan(student)
        policy = DecisionPolicySnapshot.load_default()
        conflict_snapshot = build_research_snapshot(
            student,
            query_plan,
            (
                provincial_cohort_bridge(student, query_plan),
                school_bridge(student, query_plan, 2025, 18000),
                school_bridge(student, query_plan, 2026, 19000),
                joint_bridge(student, query_plan, 2025, 700, 750, 800),
            ),
            policy,
        )

        conflict = locate_rank(student, research_snapshot=conflict_snapshot)

        self.assertEqual(conflict.status, EvidenceStatus.CONFLICT)
        self.assertIsNone(conflict.optimistic_rank)
        self.assertIsNone(conflict.central_rank)
        self.assertIsNone(conflict.conservative_rank)

        overlapping_snapshot = build_research_snapshot(
            student,
            query_plan,
            (
                provincial_cohort_bridge(student, query_plan),
                school_bridge(student, query_plan, 2025, 18000),
                school_bridge(student, query_plan, 2026, 19000),
                joint_bridge(student, query_plan, 2025, 80, 90, 100),
            ),
            policy,
        )

        bounded = locate_rank(student, research_snapshot=overlapping_snapshot)

        self.assertEqual(bounded.status, EvidenceStatus.INFERRED)
        self.assertEqual(
            (
                bounded.optimistic_rank,
                bounded.central_rank,
                bounded.conservative_rank,
            ),
            (16108, 18260, 20000),
        )
        self.assertIn("profile_usual_rank_anchor", bounded.reasons)

    def test_all_matching_score_years_narrow_the_profile_reported_interval(self):
        from scripts.planning_profile import load_planning_profile
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        payload = profile().to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload["rank_observations"] = [
            {
                **payload["rank_observations"][0],
                "scope": "province_official",
                "rank": None,
                "cohort_size": None,
                "source": "official_score",
            }
        ]
        student = load_planning_profile(payload)
        query_plan = plan(student)
        bridges = []
        for year, rank in ((2025, 18000), (2024, 19000)):
            task = next(
                item
                for item in query_plan.tasks
                if item.kind == "score_table" and item.year == year
            )
            base = score_table()
            row = replace(
                base.rows[0],
                values={"score": 610, "rank": rank, "cumulative_count": rank},
            )
            table = replace(
                base,
                rows=(row,),
                coverage=ExtractedCoverage(
                    lower_score=610,
                    upper_score=610,
                    lower_rank=rank,
                    upper_rank=rank,
                ),
            )
            bridges.append(
                bridge_rank_evidence(
                    profile=student,
                    plan=query_plan,
                    task=task,
                    table=table,
                    extracted_row=row,
                    candidates=(candidate(f"official-{year}"),),
                    coverage_status=EvidenceStatus.OFFICIAL,
                )
            )
        snapshot = build_research_snapshot(
            student,
            query_plan,
            tuple(bridges),
            DecisionPolicySnapshot.load_default(),
        )

        scenario = locate_rank(student, research_snapshot=snapshot)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.basis, "profile_reported_score_table")
        self.assertEqual(
            (
                scenario.optimistic_rank,
                scenario.central_rank,
                scenario.conservative_rank,
            ),
            (17100, 18500, 19800),
        )
        self.assertEqual(scenario.contributing_years, (2024, 2025))
        self.assertEqual(
            set(scenario.source_ids),
            {"official-2024", "official-2025", "profile-reported-score"},
        )
        self.assertIn("profile_score_basis_raw", scenario.reasons)

    def test_authenticated_snapshot_projects_explicit_joint_rank_to_province(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        payload = profile().to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload["rank_observations"].append(
            {
                **payload["rank_observations"][0],
                "scope": "province_joint",
                "rank": 18200,
                "cohort_size": 210000,
                "source": "joint_exam_report",
            }
        )
        student = PlanningProfile.create(payload)
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table"
            and item.year == query_plan.research_year
        )
        table = score_table()
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (
                bridge_rank_evidence(
                    profile=student,
                    plan=query_plan,
                    task=task,
                    table=table,
                    extracted_row=table.rows[0],
                    candidates=(candidate("joint-profile-support"),),
                    coverage_status=EvidenceStatus.OFFICIAL,
                ),
            ),
            DecisionPolicySnapshot.load_default(),
        )

        scenario = locate_rank(student, research_snapshot=snapshot)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.basis, "profile_reported_province_rank")
        self.assertEqual(scenario.central_rank, 17334)
        self.assertEqual(scenario.confidence, "medium")
        self.assertEqual(
            set(scenario.source_ids),
            {"joint-profile-support", "profile-reported-rank"},
        )
        self.assertIn("profile_scope_province_joint", scenario.reasons)

    def test_city_joint_profile_rank_maps_into_the_provincial_cohort(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        payload = profile().to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload["rank_observations"].append(
            {
                **payload["rank_observations"][0],
                "exam_date": "2026-06-01",
                "scope": "city_joint",
                "rank": 100,
                "cohort_size": 1000,
                "source": "joint_exam_report",
            }
        )
        student = PlanningProfile.create(payload)
        query_plan = plan(student)
        score_task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == query_plan.research_year
        )
        table = score_table()
        score_bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=score_task,
            table=table,
            extracted_row=table.rows[0],
            candidates=(candidate("official-provincial-cohort"),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (score_bridge,),
            DecisionPolicySnapshot.load_default(),
        )

        scenario = locate_rank(student, research_snapshot=snapshot)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.central_rank, 20000)
        self.assertEqual(scenario.confidence, "low")
        self.assertIn("profile_scope_city_joint", scenario.reasons)
        self.assertEqual(
            set(scenario.source_ids),
            {"official-provincial-cohort", "profile-reported-rank"},
        )
        self.assertIn("official_score_table", scenario.channel_kinds)

    def test_authenticated_joint_event_replaces_the_same_profile_event(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        payload = profile().to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload["rank_observations"].append(
            {
                **payload["rank_observations"][0],
                "exam_date": "2026-06-01",
                "scope": "city_joint",
                "rank": 100,
                "cohort_size": 1000,
                "source": "joint_exam_report",
            }
        )
        student = PlanningProfile.create(payload)
        query_plan = plan(student)
        score_task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == query_plan.research_year
        )
        table = score_table()
        score_bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=score_task,
            table=table,
            extracted_row=table.rows[0],
            candidates=(candidate("official-provincial-cohort"),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        authenticated_joint = joint_bridge(
            student,
            query_plan,
            query_plan.research_year,
            100,
            100,
            100,
            cohort_size=1000,
            rank_scope="city_joint",
            exam_date="2026-06-01",
        )
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (score_bridge, authenticated_joint),
            DecisionPolicySnapshot.load_default(),
        )

        scenario = locate_rank(student, research_snapshot=snapshot)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(
            (
                scenario.optimistic_rank,
                scenario.central_rank,
                scenario.conservative_rank,
            ),
            (20000, 20000, 20000),
        )
        self.assertEqual(
            scenario.channel_kinds,
            ("joint_exam", "official_score_table"),
        )
        self.assertNotIn("profile-reported-rank", scenario.source_ids)
        self.assertIn("official-provincial-cohort", scenario.source_ids)

    def test_authenticated_joint_uses_the_snapshot_research_year_window(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        student = profile()
        query_plan = plan(student)
        score_task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == query_plan.research_year
        )
        table = score_table()
        score_bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=score_task,
            table=table,
            extracted_row=table.rows[0],
            candidates=(candidate("official-provincial-cohort"),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        fallback_year = query_plan.research_year - 3
        authenticated_joint = joint_bridge(
            student,
            query_plan,
            fallback_year,
            80,
            120,
            160,
            cohort_size=1000,
        )
        snapshot = build_research_snapshot(
            student,
            query_plan,
            (score_bridge, authenticated_joint),
            DecisionPolicySnapshot.load_default(),
        )

        scenario = locate_rank(student, research_snapshot=snapshot)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.rejected_channel_count, 0)
        self.assertIn(fallback_year, scenario.contributing_years)

    def test_authenticated_b_and_c_score_tables_map_rank_with_lower_confidence(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        student = unresolved_official_score_profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == query_plan.research_year
        )
        table = score_table()
        cases = (
            (
                EvidenceStatus.OFFICIAL,
                (candidate("official-current"),),
                "official_score_table",
            ),
            (
                EvidenceStatus.CORROBORATED,
                (
                    candidate(
                        "b-current-one",
                        tier=SourceTier.B,
                        publisher="独立乙一",
                        host="b-current-one.example.cn",
                    ),
                    candidate(
                        "b-current-two",
                        tier=SourceTier.B,
                        publisher="独立乙二",
                        host="b-current-two.example.cn",
                    ),
                ),
                "score_table_reference",
            ),
            (
                EvidenceStatus.REFERENCE,
                tuple(
                    candidate(
                        f"c-current-{index}",
                        tier=SourceTier.C,
                        publisher=f"独立丙{index}",
                        host=f"c-current-{index}.example.cn",
                    )
                    for index in range(1, 4)
                ),
                "score_table_reference",
            ),
        )
        widths = {}
        for expected_status, sources, expected_kind in cases:
            with self.subTest(status=expected_status):
                bridge = bridge_rank_evidence(
                    profile=student,
                    plan=query_plan,
                    task=task,
                    table=table,
                    extracted_row=table.rows[0],
                    candidates=sources,
                    coverage_status=expected_status,
                )
                snapshot = build_research_snapshot(
                    student,
                    query_plan,
                    (bridge,),
                    DecisionPolicySnapshot.load_default(),
                )

                self.assertEqual(len(snapshot.score_rows), 1)
                self.assertEqual(snapshot.rank_facts[0]["value"]["kind"], expected_kind)
                self.assertEqual(snapshot.rank_facts[0]["status"], expected_status.value)
                scenario = locate_rank(student, research_snapshot=snapshot)
                self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
                self.assertEqual(scenario.central_rank, 18000)
                self.assertIn(expected_status.value, scenario.channel_statuses)
                self.assertIn(expected_kind, scenario.channel_kinds)
                if expected_kind == "score_table_reference":
                    self.assertNotIn("official_score_table", scenario.channel_kinds)
                self.assertIn("year_fallback:0", scenario.reasons)
                self.assertGreaterEqual(
                    set(scenario.source_ids),
                    {item.source_id for item in sources} | {"profile-reported-score"},
                )
                widths[expected_status] = (
                    scenario.conservative_rank - scenario.optimistic_rank
                )

        self.assertLess(
            widths[EvidenceStatus.OFFICIAL],
            widths[EvidenceStatus.CORROBORATED],
        )
        self.assertLess(
            widths[EvidenceStatus.CORROBORATED],
            widths[EvidenceStatus.REFERENCE],
        )

    def test_conflicting_reference_score_rows_are_not_averaged(self):
        from scripts.research_snapshot import (
            ResearchSnapshotError,
            build_research_snapshot,
        )

        student = unresolved_official_score_profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == query_plan.research_year
        )
        base = score_table()
        conflicting_row = replace(
            base.rows[0],
            values={"score": 610, "rank": 19000, "cumulative_count": 19000},
        )
        conflicting_table = replace(
            base,
            rows=(conflicting_row,),
            coverage=ExtractedCoverage(
                lower_score=610,
                upper_score=610,
                lower_rank=19000,
                upper_rank=19000,
            ),
        )

        def reference_bridge(table, row, prefix):
            return bridge_rank_evidence(
                profile=student,
                plan=query_plan,
                task=task,
                table=table,
                extracted_row=row,
                candidates=tuple(
                    candidate(
                        f"{prefix}-{index}",
                        tier=SourceTier.C,
                        publisher=f"{prefix}独立发布方{index}",
                        host=f"{prefix}-{index}.example.cn",
                    )
                    for index in range(1, 4)
                ),
                coverage_status=EvidenceStatus.REFERENCE,
            )

        with self.assertRaisesRegex(ResearchSnapshotError, "duplicate score rows"):
            build_research_snapshot(
                student,
                query_plan,
                (
                    reference_bridge(base, base.rows[0], "left"),
                    reference_bridge(
                        conflicting_table,
                        conflicting_row,
                        "right",
                    ),
                ),
                DecisionPolicySnapshot.load_default(),
            )

    def test_builds_validated_runtime_dataset_without_province_file(self):
        from scripts.research_snapshot import (
            ProvinceResearchSnapshot,
            build_research_snapshot,
        )

        student, query_plan, rank_bridge, admission_bridge = bridges()
        policy = DecisionPolicySnapshot.load_default()

        snapshot = build_research_snapshot(
            student,
            query_plan,
            (rank_bridge, admission_bridge),
            policy,
        )

        self.assertEqual(snapshot.config.province, "湖北")
        self.assertEqual(snapshot.config.directory, Path("."))
        self.assertEqual(snapshot.score_rows[0].year, 2025)
        self.assertEqual(snapshot.admission_rows[0].school_code, "SYN-A01")
        self.assertEqual(snapshot.policy_id, policy.policy_id)
        self.assertEqual(snapshot.profile_digest, student.digest)
        self.assertEqual(snapshot.query_plan_digest, rank_bridge.query_plan_digest)
        self.assertRegex(snapshot.digest, r"^sha256:[0-9a-f]{64}$")
        rendered = json.dumps(snapshot.to_dict(), ensure_ascii=False, allow_nan=False)
        self.assertNotIn("C:\\\\", rendered)
        with self.assertRaises(TypeError):
            ProvinceResearchSnapshot()
        with self.assertRaises(FrozenInstanceError):
            snapshot.policy_id = "forged"

    def test_rejects_hand_built_facts_and_mutated_dependency_bindings(self):
        from scripts.research_snapshot import ResearchSnapshotError, build_research_snapshot
        from scripts.validate_data import admission_row_hash

        student, query_plan, rank_bridge, admission_bridge = bridges()
        policy = DecisionPolicySnapshot.load_default()
        with self.assertRaises(TypeError):
            build_research_snapshot(
                student,
                query_plan,
                (rank_bridge.fact.to_dict(),),
                policy,
            )

        object.__setattr__(rank_bridge, "query_plan_digest", "sha256:" + "0" * 64)
        with self.assertRaises((ResearchSnapshotError, ValueError)):
            build_research_snapshot(
                student,
                query_plan,
                (rank_bridge, admission_bridge),
                policy,
            )

        student, query_plan, rank_bridge, admission_bridge = bridges()
        object.__setattr__(admission_bridge, "admission_row_hash", "sha256:" + "0" * 64)
        with self.assertRaises(ResearchSnapshotError):
            build_research_snapshot(
                student,
                query_plan,
                (rank_bridge, admission_bridge),
                policy,
            )

        student, query_plan, rank_bridge, admission_bridge = bridges()
        forged_row = ValidatedAdmissionRow.from_mapping(
            {
                **admission_bridge.dataset_row.to_dict(),
                "school_name": "协同篡改大学",
            }
        )
        object.__setattr__(admission_bridge, "dataset_row", forged_row)
        object.__setattr__(
            admission_bridge, "admission_row_hash", admission_row_hash(forged_row)
        )
        object.__setattr__(
            admission_bridge,
            "source_ids",
            ("forged-one", "forged-two", "forged-three"),
        )
        object.__setattr__(
            admission_bridge, "evidence_status", EvidenceStatus.REFERENCE
        )
        object.__setattr__(
            admission_bridge, "evidence_method", "three-source-consensus"
        )
        with self.assertRaises(ResearchSnapshotError):
            build_research_snapshot(
                student,
                query_plan,
                (rank_bridge, admission_bridge),
                policy,
            )

        student, query_plan, rank_bridge, admission_bridge = bridges()
        object.__setattr__(policy, "policy_id", "forged-policy")
        with self.assertRaises(ResearchSnapshotError):
            build_research_snapshot(
                student,
                query_plan,
                (rank_bridge, admission_bridge),
                policy,
            )

    def test_public_builder_rejects_even_validator_created_in_memory_snapshot(self):
        from scripts.research_snapshot import build_research_snapshot

        student, query_plan, rank_bridge, admission_bridge = bridges()
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(
                Path(temporary).resolve(),
                CapabilityReport(CapabilityTier.OFFLINE),
            )
            store.add_candidate(candidate())
            store.add_candidate(
                candidate(
                    "official-admission",
                    publisher="湖北省普通批发布机关",
                    host="admission.hubei.gov.cn",
                )
            )
            rank_bridge.persist(store)
            admission_bridge.persist(store)
            store.finalize()
            validation = validate_bundle_snapshot(store.session_path)
            self.assertEqual(validation.issues, ())
            self.assertIsNotNone(validation.snapshot)

            direct = type(validation.snapshot)._create(
                validation.snapshot.manifest,
                validation.snapshot.capability,
                validation.snapshot.retrieval_dates,
                validation.snapshot.facts,
                validation.snapshot.rejections,
            )
            for label, in_memory in (
                ("validator-created", validation.snapshot),
                ("self-signed", direct),
            ):
                with self.subTest(snapshot=label), self.assertRaises(TypeError):
                    build_research_snapshot(
                        student,
                        query_plan,
                        in_memory,
                        DecisionPolicySnapshot.load_default(),
                    )

    def test_host_internal_bundle_is_revalidated_on_every_persisted_replay(self):
        import scripts.research_snapshot as research_module

        student, query_plan, rank_bridge, admission_bridge = bridges()
        with tempfile.TemporaryDirectory() as temporary:
            for mutation in ("fact", "manifest", "artifact"):
                with self.subTest(mutation=mutation):
                    workspace = Path(temporary) / mutation
                    workspace.mkdir()
                    store = EvidenceStore.create(
                        workspace.resolve(),
                        CapabilityReport(CapabilityTier.OFFLINE),
                    )
                    store.add_candidate(candidate())
                    store.add_candidate(
                        candidate(
                            "official-admission",
                            publisher="湖北省普通批发布机关",
                            host="admission.hubei.gov.cn",
                        )
                    )
                    rank_bridge.persist(store)
                    admission_bridge.persist(store)
                    store.finalize()

                    snapshot = research_module.build_research_snapshot(
                        student,
                        query_plan,
                        store.session_path,
                        DecisionPolicySnapshot.load_default(),
                    )
                    self.assertEqual(len(snapshot.score_rows), 1)
                    self.assertEqual(len(snapshot.admission_rows), 1)

                    if mutation == "fact":
                        artifact = store.session_path / "normalized" / "facts.jsonl"
                        original = artifact.read_text(encoding="utf-8")
                        tampered = original.replace('"score":610', '"score":609', 1)
                        self.assertNotEqual(tampered, original)
                        artifact.write_text(tampered, encoding="utf-8", newline="\n")
                    elif mutation == "manifest":
                        artifact = store.session_path / "manifest.json"
                        original = artifact.read_text(encoding="utf-8")
                        original_hash = json.loads(original)["manifest_hash"]
                        tampered = original.replace(
                            original_hash,
                            "sha256:" + "0" * 64,
                            1,
                        )
                        self.assertNotEqual(tampered, original)
                        artifact.write_text(tampered, encoding="utf-8", newline="\n")
                    else:
                        (store.session_path / "normalized" / "facts.jsonl").unlink()

                    research_module = importlib.reload(research_module)
                    with self.assertRaises(
                        research_module.ResearchSnapshotError
                    ) as rejected:
                        research_module.build_research_snapshot(
                            student,
                            query_plan,
                            store.session_path,
                            DecisionPolicySnapshot.load_default(),
                        )
                    self.assertNotIn(
                        str(store.session_path),
                        str(rejected.exception),
                    )

    def test_rank_locator_consumes_snapshot_and_never_averages_conflicting_channels(self):
        from scripts.rank_locator import locate_rank
        from scripts.research_snapshot import build_research_snapshot

        student = profile()
        query_plan = plan(student)
        policy = DecisionPolicySnapshot.load_default()
        bounded = build_research_snapshot(
            student,
            query_plan,
            (
                provincial_cohort_bridge(student, query_plan),
                joint_bridge(student, query_plan, 2025, 80, 120, 160),
            ),
            policy,
        )

        scenario = locate_rank(student, research_snapshot=bounded)

        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.central_rank, 24000)
        self.assertEqual(
            (scenario.optimistic_rank, scenario.conservative_rank),
            (16000, 32000),
        )

        conflict_snapshot = build_research_snapshot(
            student,
            query_plan,
            (
                provincial_cohort_bridge(student, query_plan),
                joint_bridge(student, query_plan, 2025, 80, 120, 160),
                joint_bridge(student, query_plan, 2026, 700, 750, 800),
            ),
            policy,
        )
        conflict = locate_rank(student, research_snapshot=conflict_snapshot)
        self.assertEqual(conflict.status, EvidenceStatus.CONFLICT)
        self.assertIsNone(conflict.central_rank)
        self.assertIn("authenticated_channel_interval_conflict", conflict.reasons)


if __name__ == "__main__":
    unittest.main()
