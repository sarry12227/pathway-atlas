from __future__ import annotations

import ast
from pathlib import Path
import unittest

from scripts.contracts import EvidenceStatus
from scripts.generate_report import build_pathway_atlas_model
from scripts.path_recommend import PathwayPolicy, PathwayProfile, evaluate_pathways
from scripts.planning_profile import PlanningProfile
from scripts.report_model import StudentProfile, build_report_model, render_markdown
from scripts.school_recommend import recommend_schools
from scripts.validate_data import admission_row_hash, validate_dataset_snapshot
from scripts.validate_evidence import ValidatedEvidenceSnapshot
from tests.test_generate_report_evidence import capability, evidence_snapshot
from tests.test_pathway_evidence_bridge import fact, policy_value, snapshot
from tests.test_scenario_recommendations import policy, profile, rows, scenario


ROOT = Path(__file__).resolve().parents[1]


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
    def test_v2_three_plus_three_uses_canonical_combination_for_score_and_school_rows(self):
        validation = validate_dataset_snapshot(
            ROOT / "tests" / "fixtures" / "provinces" / "demo-33"
        )
        self.assertEqual(validation.issues, ())
        self.assertIsNotNone(validation.snapshot)
        dataset = validation.snapshot
        assert dataset is not None
        admission = dataset.admission_rows[0]
        admission_values = admission.to_dict()
        planning_profile = PlanningProfile.create(
            {
                "schema_version": "2.0",
                "gender": "不便回答",
                "province": "演示乙市",
                "city": None,
                "high_school": None,
                "grade": "高三",
                "exam_year": 2026,
                "class_level": None,
                "subject_mode": "3+3",
                "subject_group": "物理",
                "secondary_subjects": ["化学", "地理"],
                "score_basis": "赋分",
                "rank_observations": [
                    {
                        "exam_date": "2026-06-24",
                        "scope": "province_official",
                        "score": 620,
                        "max_score": 660,
                        "rank": None,
                        "cohort_size": 10000,
                        "source": "official_score",
                    }
                ],
                "best_rank": None,
                "usual_rank": None,
                "awards": [],
                "activities": [],
                "target_schools": [],
                "target_school_reasons": [],
                "target_majors": [],
                "target_major_reasons": [],
                "target_regions": [],
                "excluded_regions": [],
                "future_plan": "继续深造",
                "concerns": ["院校定位"],
                "desired_outcomes": ["冲稳保清单"],
                "eligibility_facts": ["完成高考报名"],
            }
        )
        admission_fact = {
            "fact_id": "admission-demo-33",
            "field": "admission_record:demo-33",
            "value": {
                "year": admission_values["year"],
                "province": admission_values["province"],
                "subject_group": admission_values["subject_group"],
                "school_code": admission_values["school_code"],
                "program_group": admission_values["program_group"],
                "remarks": admission_values["remarks"],
                "min_score": admission_values["min_score"],
                "min_rank": admission_values["min_rank"],
                "coverage_min_rank": 1,
                "coverage_max_rank": 10000,
                "coverage_status": "reference",
                "row_hash": admission_row_hash(admission),
            },
            "unit": None,
            "status": "reference",
            "source_ids": ["three-c1", "three-c2", "three-c3"],
            "method": "three-source-consensus",
            "notes": "synthetic exact row",
        }
        pathway = policy_value(
            "pathway-demo-33",
            province="演示乙市",
            subject_mode="3+3",
            evidence_status="reference",
            policy_source_ids=["path33-c1", "path33-c2", "path33-c3"],
        )
        raw_evidence = snapshot(admission_fact, fact(pathway))
        manifest_evidence = evidence_snapshot()
        evidence = ValidatedEvidenceSnapshot._create(
            manifest_evidence.manifest,
            capability(),
            manifest_evidence.retrieval_dates,
            raw_evidence.facts,
            raw_evidence.rejections,
        )

        model = build_pathway_atlas_model(planning_profile, dataset, evidence)

        self.assertEqual(model.profile.subject_selection_key, "物理+化学+地理")
        self.assertEqual(model.rank.central_rank, 800)
        self.assertEqual(tuple(item.school_name for item in model.recommendations), ("虚构乙大学",))
        self.assertEqual(tuple(item.title for item in model.pathways), ("pathway-demo-33 合成招生政策",))

    def test_v2_profile_runs_one_authenticated_school_and_pathway_pipeline(self):
        validation = validate_dataset_snapshot(
            ROOT / "tests" / "fixtures" / "provinces" / "demo-312"
        )
        self.assertEqual(validation.issues, ())
        self.assertIsNotNone(validation.snapshot)
        dataset = validation.snapshot
        assert dataset is not None
        admission = dataset.admission_rows[0]
        profile_payload = {
            "schema_version": "2.0",
            "gender": "不便回答",
            "province": "演示甲省",
            "city": "示例市",
            "high_school": "示例中学",
            "grade": "高一",
            "exam_year": 2028,
            "class_level": "重点班",
            "subject_mode": "3+1+2",
            "subject_group": "物理",
            "secondary_subjects": ["化学", "地理"],
            "score_basis": "原始分",
            "rank_observations": [
                {
                    "exam_date": "2026-06-24",
                    "scope": "province_official",
                    "score": 650,
                    "max_score": 750,
                    "rank": None,
                    "cohort_size": 10000,
                    "source": "official_score",
                }
            ],
            "best_rank": None,
            "usual_rank": None,
            "awards": [],
            "activities": [],
            "target_schools": ["虚构甲大学"],
            "target_school_reasons": ["专业匹配"],
            "target_majors": ["虚构专业"],
            "target_major_reasons": ["长期兴趣"],
            "target_regions": ["示例市"],
            "excluded_regions": [],
            "future_plan": "继续深造",
            "concerns": ["院校定位", "多元路径"],
            "desired_outcomes": ["冲稳保清单", "路径行动表"],
            "eligibility_facts": ["完成高考报名"],
        }
        planning_profile = PlanningProfile.create(profile_payload)
        admission_values = admission.to_dict()
        admission_fact = {
            "fact_id": "admission-demo",
            "field": "admission_record:demo",
            "value": {
                "year": admission_values["year"],
                "province": admission_values["province"],
                "subject_group": admission_values["subject_group"],
                "school_code": admission_values["school_code"],
                "program_group": admission_values["program_group"],
                "remarks": admission_values["remarks"],
                "min_score": admission_values["min_score"],
                "min_rank": admission_values["min_rank"],
                "coverage_min_rank": 1,
                "coverage_max_rank": 10000,
                "coverage_status": "reference",
                "row_hash": admission_row_hash(admission),
            },
            "unit": None,
            "status": "reference",
            "source_ids": ["admission-c1", "admission-c2", "admission-c3"],
            "method": "three-source-consensus",
            "notes": "synthetic exact row",
        }
        pathway = policy_value(
            "pathway-demo",
            province="演示甲省",
            subject_mode="3+1+2",
            target_year=2028,
            data_year=2026,
            fallback_distance=2,
            year_basis="historical_fallback",
            evidence_status="reference",
            policy_source_ids=["path-c1", "path-c2", "path-c3"],
        )
        raw_evidence = snapshot(admission_fact, fact(pathway))
        manifest_evidence = evidence_snapshot()
        evidence = ValidatedEvidenceSnapshot._create(
            manifest_evidence.manifest,
            capability(),
            manifest_evidence.retrieval_dates,
            raw_evidence.facts,
            raw_evidence.rejections,
        )

        model = build_pathway_atlas_model(planning_profile, dataset, evidence)
        markdown = render_markdown(model)

        self.assertEqual(model.rank.central_rank, 1000)
        self.assertEqual(model.rank.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(len(model.recommendations), 1)
        self.assertEqual(len(model.pathways), 1)
        for literal in (
            "虚构甲大学",
            "admission-c1、admission-c2、admission-c3",
            "pathway-demo 合成招生政策",
            "重点准备",
            "报名前复核当年简章",
            "历史回退 2026→2028",
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
            "AI 生成，仅供参考；不构成录取承诺",
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
