from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.adapters.pathway_bridge import bridge_pathway_policy_evidence
from scripts.adapters.rank_bridge import bridge_rank_evidence
from scripts.action_plan import ActionItem
from scripts.contracts import EvidenceStatus, OrdinaryBatchPolicy, SourceTier
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.evidence import EvidenceStore
from scripts.generate_report import EvidenceReportInputError, build_pathway_atlas_model
from scripts.path_recommend import PathwayPolicy, PathwayProfile, evaluate_pathways
from scripts.planning_profile import PlanningProfile
from scripts.query_plan import build_query_plan, load_province_catalog
from scripts.rank_locator import RankScenario
from scripts.report_model import StudentProfile, build_report_model, render_markdown
from scripts.research_snapshot import build_research_snapshot
from scripts.school_recommend import personalize_school_recommendations, recommend_schools
from scripts.validate_evidence import validate_bundle_snapshot
from tests.test_generate_report_evidence import capability, evidence_snapshot
from tests.test_pathway_evidence_bridge import candidate as pathway_candidate
from tests.test_pathway_evidence_bridge import project as pathway_projection
from tests.test_rank_evidence_bridge import candidate as rank_candidate
from tests.test_research_snapshot import bridges
from tests.test_scenario_recommendations import policy, profile, rows, scenario
from tests.test_planning_profile import reference_payload


ROOT = Path(__file__).resolve().parents[1]


def school_anchor_bridge(student, query_plan, year: int, province_rank: int):
    task = next(
        item
        for item in query_plan.tasks
        if item.kind == "joy_report" and item.year == year
    )
    values = {
        "scope": "school_anchor",
        "school_name": student.high_school,
        "class_level": student.class_level,
        "school_rank": 120,
        "province_rank": province_rank,
        "school_score": 610,
        "max_score": 750,
        "cohort_size": 1000,
    }
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
    candidates = tuple(
        rank_candidate(
            f"school-{year}-{index}",
            tier=SourceTier.C,
            publisher=f"学校成绩参考发布方{year}-{index}",
            host=f"school-{year}-{index}.example.cn",
        )
        for index in range(1, 4)
    )
    return bridge_rank_evidence(
        profile=student,
        plan=query_plan,
        task=task,
        table=table,
        extracted_row=row,
        candidates=candidates,
        coverage_status=EvidenceStatus.REFERENCE,
    )


def pathway_result():
    policy_record = PathwayPolicy(
        policy_id="pathway-reference",
        pathway_type="comprehensive_evaluation",
        title="虚构高校综合评价",
        institution="虚构高校",
        province="湖北",
        subject_mode="3+1+2",
        valid_year=2025,
        eligibility_requirements=("完成高考报名", "完成学校初审"),
        disqualifying_facts=(),
        professional_options=("虚构专业",),
        training_arrangements="按公开培养方案执行",
        transition_rules="按公开考核规则执行",
        outcomes="按公开培养方案毕业",
        service_employment_obligations="无额外服务期",
        penalty_exit_rules="可按公开规则退出",
        fees_and_subsidies="按公开收费标准执行",
        policy_source_ids=("policy-c1", "policy-c2", "policy-c3"),
        evidence_status=EvidenceStatus.REFERENCE,
        calculation_basis="三项独立公开参考来源一致",
        target_year=2026,
        data_year=2025,
        fallback_distance=1,
        year_basis="historical_fallback",
        timeline=("本学期准备材料", "报名前复核当年简章"),
        preparation_actions=("整理成绩与活动材料", "跟踪高校官方通知"),
    )
    return evaluate_pathways(
        PathwayProfile(
            rank=22000,
            province="湖北",
            subject_mode="3+1+2",
            current_year=2026,
            eligibility_facts=("完成高考报名",),
        ),
        (policy_record,),
    )


class PathwayAtlasBlackboxTest(unittest.TestCase):
    def test_true_end_to_end_driver_uses_public_loaders_validators_and_bridges(self):
        path = ROOT / "tests" / "test_end_to_end_planning.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden_calls = {
            "PlanningProfile.create",
            "ValidatedAdmissionRow.from_mapping",
            "EvidenceFact",
            "ProvinceResearchSnapshot",
            "FactClaim",
        }
        calls = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name):
                    calls.add(f"{owner.id}.{node.func.attr}")
        self.assertTrue(
            {"build_profile_from_questionnaire", "validate_runtime_admission_row",
             "bridge_admission_evidence", "bridge_rank_evidence",
             "bridge_pathway_policy_evidence"}.issubset(calls)
        )
        self.assertEqual(calls & forbidden_calls, set())

    def test_school_decision_wrapper_projects_explicit_and_unavailable_policy_states(self):
        payload = reference_payload()
        payload.update(
            {
                "province": "上海",
                "city": "上海",
                "high_school": None,
                "grade": "高三",
                "exam_year": 2026,
                "class_level": None,
                "subject_group": "物理",
                "secondary_subjects": ["化学", "生物"],
                "rank_observations": [],
                "best_rank": None,
                "usual_rank": None,
            }
        )
        student = PlanningProfile.create(payload)
        rank = RankScenario._create(
            status=EvidenceStatus.OFFICIAL,
            basis="official_score_table",
            optimistic_rank=8000,
            central_rank=8000,
            conservative_rank=8000,
            confidence="high",
            source_ids=("rank-official",),
            contributing_years=(2025,),
            backtest_error=None,
            reasons=("official_score_table",),
            channel_kinds=("official_score_table",),
            channel_statuses=("official",),
            rejected_channel_count=0,
        )
        report_profile = StudentProfile(
            province=student.province,
            subject_mode=student.subject_mode,
            subject_group=student.subject_group,
            secondary_subjects=student.secondary_subjects,
            rank=rank.central_rank,
            grade=student.grade,
            current_year=2026,
        )
        explicit_policy = OrdinaryBatchPolicy(
            schema_version="1.0",
            policy_id="synthetic-ordinary-batch-v1",
            basis_id="synthetic-policy-basis-v1",
            search_delta_min=-8000,
            search_delta_max=6000,
            challenge_delta_lt=-2000,
            stable_delta_le=2000,
            tier_caps={"冲": 3, "稳": 4, "保": 5},
        )
        row = {
            "year": 2025,
            "province": "上海",
            "school_name": "演示大学",
            "school_code": "D001",
            "subject_group": "物理",
            "major_group_name": "第01组",
            "major_group_code": "G01",
            "min_score": 620,
            "min_rank": 8000,
            "majors_in_group": '["计算机科学与技术"]',
            "school_level": "211",
            "school_province": "上海",
            "city_location": "上海",
            "remarks": "",
            "evidence_status": "official",
            "coverage_status": "official",
            "source_ids": ["source-2025-01"],
            "coverage_min_rank": 5000,
            "coverage_max_rank": 12000,
        }
        available = personalize_school_recommendations(
            [row],
            student,
            explicit_policy,
            rank_scenario=rank,
        )
        unavailable = personalize_school_recommendations(
            [row],
            student,
            rank_scenario=rank,
        )

        available_model = build_report_model(
            report_profile,
            available,
            rank=rank,
            pathways=None,
            evidence=evidence_snapshot(),
        )
        unavailable_model = build_report_model(
            report_profile,
            unavailable,
            rank=rank,
            pathways=None,
            evidence=evidence_snapshot(),
        )

        self.assertEqual(available.ordinary_batch_policy, explicit_policy)
        self.assertEqual(available_model.ordinary_batch_policy, explicit_policy)
        self.assertEqual(
            available_model.recommendation_policy_status,
            "ordinary_batch_policy_available",
        )
        self.assertIsNone(unavailable.ordinary_batch_policy)
        self.assertIsNone(unavailable_model.ordinary_batch_policy)
        self.assertEqual(
            unavailable_model.recommendation_policy_status,
            "rank_delta_policy_unavailable",
        )

    def test_typed_bundle_replays_rank_school_and_pathway_into_one_report(self):
        student, query_plan, rank_bridge, admission_bridge = bridges()
        school_2025 = school_anchor_bridge(student, query_plan, 2025, 20000)
        school_2026 = school_anchor_bridge(student, query_plan, 2026, 20000)
        pathway_source = pathway_candidate()
        projection = pathway_projection(
            student=student,
            query_plan=query_plan,
            candidates=(pathway_source,),
        )
        pathway_bridge = bridge_pathway_policy_evidence(projection)
        registered_sources = (
            rank_candidate(),
            rank_candidate(
                "official-admission",
                publisher="湖北省普通批发布机关",
                host="admission.hubei.gov.cn",
            ),
            *school_2025.candidates,
            *school_2026.candidates,
            pathway_source,
        )
        reviewed = DecisionPolicySnapshot.load_default()

        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(
                Path(temporary).resolve(),
                capability(),
            )
            for source in registered_sources:
                store.add_candidate(source)
            rank_bridge.persist(store)
            school_2025.persist(store)
            school_2026.persist(store)
            admission_bridge.persist(store)
            pathway_bridge.persist(store)
            store.finalize()
            validation = validate_bundle_snapshot(store.session_path)
            self.assertEqual(validation.issues, ())
            research = build_research_snapshot(
                student,
                query_plan,
                store.session_path,
                reviewed,
            )

            original_research_year = research.research_year
            original_digest = research.digest
            try:
                object.__setattr__(
                    research, "research_year", original_research_year - 1
                )
                tampered_payload = research.to_dict()
                tampered_payload.pop("digest")
                object.__setattr__(
                    research,
                    "digest",
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            tampered_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                    ).hexdigest(),
                )
                with self.assertRaisesRegex(
                    EvidenceReportInputError,
                    "画像或查询计划",
                ):
                    build_pathway_atlas_model(
                        student,
                        research,
                        store.session_path,
                        query_plan,
                        decision_policy=reviewed,
                    )
            finally:
                object.__setattr__(research, "research_year", original_research_year)
                object.__setattr__(research, "digest", original_digest)

            model = build_pathway_atlas_model(
                student,
                research,
                store.session_path,
                query_plan,
                decision_policy=reviewed,
            )
            self.assertTrue(model.action_items)
            self.assertTrue(all(isinstance(item, ActionItem) for item in model.action_items))
            self.assertFalse(any(
                item.reason_code == "long_lead_readiness"
                and item.pathway_ids
                for item in model.action_items
            ))
            with tempfile.TemporaryDirectory() as other_temporary:
                other_store = EvidenceStore.create(
                    Path(other_temporary).resolve(),
                    capability(),
                )
                for source in (
                    *registered_sources,
                    rank_candidate(
                        "unused-reference",
                        tier=SourceTier.C,
                        publisher="未参与计算的参考发布方",
                        host="unused-reference.example.cn",
                    ),
                ):
                    other_store.add_candidate(source)
                rank_bridge.persist(other_store)
                school_2025.persist(other_store)
                school_2026.persist(other_store)
                admission_bridge.persist(other_store)
                pathway_bridge.persist(other_store)
                other_store.finalize()
                other_validation = validate_bundle_snapshot(other_store.session_path)
                self.assertEqual(other_validation.issues, ())
                with self.assertRaisesRegex(
                    EvidenceReportInputError,
                    "证据包",
                ):
                    build_pathway_atlas_model(
                        student,
                        research,
                        other_store.session_path,
                        query_plan,
                        decision_policy=reviewed,
                    )

            other_payload = student.to_dict()
            other_payload.pop("mode")
            other_payload.pop("digest")
            other_payload["gender"] = (
                "男" if other_payload["gender"] != "男" else "女"
            )
            other_student = PlanningProfile.create(other_payload)
            other_plan = build_query_plan(
                other_student,
                load_province_catalog(),
                reviewed,
            )
            mixed_projection = pathway_projection(
                student=other_student,
                query_plan=other_plan,
                candidates=(pathway_source,),
            )
            mixed_pathway_bridge = bridge_pathway_policy_evidence(mixed_projection)
            with tempfile.TemporaryDirectory() as mixed_temporary:
                mixed_store = EvidenceStore.create(
                    Path(mixed_temporary).resolve(),
                    capability(),
                )
                for source in registered_sources:
                    mixed_store.add_candidate(source)
                rank_bridge.persist(mixed_store)
                school_2025.persist(mixed_store)
                school_2026.persist(mixed_store)
                admission_bridge.persist(mixed_store)
                mixed_pathway_bridge.persist(mixed_store)
                mixed_store.finalize()
                mixed_validation = validate_bundle_snapshot(mixed_store.session_path)
                self.assertEqual(mixed_validation.issues, ())
                mixed_research = build_research_snapshot(
                    student,
                    query_plan,
                    mixed_store.session_path,
                    reviewed,
                )
                with self.assertRaisesRegex(
                    EvidenceReportInputError,
                    "画像或查询计划",
                ):
                    build_pathway_atlas_model(
                        student,
                        mixed_research,
                        mixed_store.session_path,
                        query_plan,
                        decision_policy=reviewed,
                    )

        markdown = render_markdown(model)
        self.assertEqual(model.rank.central_rank, 20020)
        self.assertEqual(
            tuple(item.school_name for item in model.recommendations),
            ("合成示例大学",),
        )
        self.assertEqual(
            model.recommendation_policy_status,
            "rank_delta_policy_unavailable",
        )
        self.assertIsNone(model.ordinary_batch_policy)
        school = next(
            item for item in model.school_decisions if item.school_name == "合成示例大学"
        )
        self.assertEqual(school.outcome, "included")
        # The non-collapsed inferred interval keeps the exact school visible,
        # but correctly classifies its 20,000 cutoff as a challenge against
        # the 20,020 central estimate instead of preserving the old safe label.
        self.assertIn("SCHOOL_RANK_CHALLENGE", {reason.code for reason in school.reasons})
        trace_fields = {item.field for item in student.to_decision_trace()}
        self.assertTrue(
            all(set(reason.input_fields) <= trace_fields for reason in school.reasons)
        )
        self.assertEqual(
            next(
                reason.input_fields
                for reason in school.reasons
                if reason.dimension == "evidence_quality"
            ),
            (),
        )
        evaluated_pathways = tuple(
            item for item in model.pathways if item.decision_reasons
        )
        observations = tuple(
            item for item in model.pathways if not item.decision_reasons
        )
        self.assertEqual(len(evaluated_pathways), 1)
        self.assertTrue(observations)
        self.assertTrue(
            all(
                item.status == "pending_verification"
                and item.investment_decision == "观察"
                and item.qualification_status == "待核验"
                and item.target_year is None
                and item.data_year is None
                for item in observations
            )
        )
        self.assertEqual(len(evaluated_pathways[0].decision_reasons), 8)
        self.assertTrue(
            all(
                set(reason.input_fields) <= trace_fields
                for reason in evaluated_pathways[0].decision_reasons
            )
        )
        pathway_code = evaluated_pathways[0].decision_reasons[0].code.replace("_", "\\_")
        for literal in (
            "合成示例大学",
            "SCHOOL\\_RANK\\_CHALLENGE",
            "普通批位次差策略：不可用",
            projection.title,
            pathway_code,
            "基于公开数据由 AI 整理，仅供参考；不构成升学建议或录取承诺",
        ):
            self.assertIn(literal, markdown)

    def test_scenario_schools_and_decisive_pathway_share_one_report_model(self):
        safe_rows = []
        for index, value in enumerate(rows()):
            safe = dict(value)
            safe["source_ids"] = [
                f"admission-{index}-a",
                f"admission-{index}-b",
                f"admission-{index}-c",
            ]
            safe_rows.append(safe)
        recommendations = recommend_schools(
            safe_rows, profile(), policy(), rank_scenario=scenario()
        )
        model = build_report_model(
            StudentProfile(
                province="湖北",
                subject_mode="3+1+2",
                subject_group="历史",
                secondary_subjects=("地理", "政治"),
                rank=22000,
                grade="高二",
                current_year=2026,
            ),
            recommendations,
            rank=scenario(),
            pathways=pathway_result(),
            evidence=evidence_snapshot(),
        )
        markdown = render_markdown(model)
        for literal in (
            "乐观位次：18000",
            "中性位次：22000",
            "保守位次：27000",
            "观察大学",
            "重点准备",
            "待核验",
            "完成学校初审",
            "本学期准备材料",
            "历史回退 2025→2026",
            "多源参考",
            "基于公开数据由 AI 整理，仅供参考；不构成升学建议或录取承诺",
        ):
            self.assertIn(literal, markdown)

    def test_report_profile_can_represent_no_reliable_rank(self):
        value = StudentProfile(
            province="湖北",
            subject_mode="3+1+2",
            subject_group="历史",
            secondary_subjects=("地理", "政治"),
            rank=None,
            grade="高一",
            current_year=2026,
        )
        self.assertIsNone(value.rank)

    def test_tracked_runtime_never_calls_pathway_evaluator_with_literal_empty_policy(self):
        findings = []
        for path in (ROOT / "scripts").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                function = node.func
                name = function.id if isinstance(function, ast.Name) else (
                    function.attr if isinstance(function, ast.Attribute) else ""
                )
                if name != "evaluate_pathways" or len(node.args) < 2:
                    continue
                argument = node.args[1]
                if isinstance(argument, (ast.Tuple, ast.List)) and not argument.elts:
                    findings.append((path.name, node.lineno))
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
