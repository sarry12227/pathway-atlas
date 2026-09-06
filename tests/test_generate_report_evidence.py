from __future__ import annotations

import json
import io
from dataclasses import fields, replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import shutil

from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceFact,
    EvidenceManifest,
    EvidenceStatus,
    OrdinaryBatchPolicy,
    RecommendationItem,
    RecommendationProfile,
    RecommendationResult,
    SourceCandidate,
    SourceTier,
)
from scripts.evidence import EvidenceStore
from scripts.path_recommend import (
    PATHWAY_DISPLAY_EVIDENCE_FIELDS,
    PathwayFieldEvidenceOrigin,
    PathwayPolicy,
    PathwayProfile,
    PathwayResult,
    RankAdjustmentModel,
    evaluate_pathways,
)
from scripts.rank_calc import RankEstimate
from scripts.rank_locator import RankScenario
from scripts.report_model import ReportModel, StudentProfile, build_report_model, render_markdown
from scripts.validate_evidence import validate_bundle_snapshot
from scripts.validate_evidence import ValidatedEvidenceSnapshot
from scripts import generate_report as report_cli
from scripts.school_recommend import recommend_schools
from scripts.validate_data import (
    ValidatedAdmissionRow,
    admission_row_hash,
    validate_dataset_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


def capability(tier: CapabilityTier = CapabilityTier.STANDARD) -> CapabilityReport:
    return CapabilityReport(
        tier=tier,
        host_capabilities=("browse", "search"),
        available_capabilities=("browse", "search"),
        missing_capabilities=("vision",),
        degradations=("图片表格未覆盖",),
        python_version="3.10.0",
        optional_modules=(),
    )


def evidence_snapshot():
    result = validate_bundle_snapshot(
        ROOT / "tests" / "fixtures" / "evidence" / "three-source-consensus"
    )
    if result.snapshot is None:
        raise AssertionError(result.issues)
    return result.snapshot


def student(**overrides) -> StudentProfile:
    values = {
        "province": "演示甲省",
        "subject_mode": "3+1+2",
        "subject_group": "物理",
        "secondary_subjects": ("化学", "地理"),
        "rank": 4200,
        "grade": "高三",
        "current_year": 2026,
    }
    values.update(overrides)
    return StudentProfile(**values)


def school_item(**overrides) -> RecommendationItem:
    values = {
        "school_name": "虚构甲大学",
        "school_level": "演示层次",
        "city": "演示市",
        "school_province": "演示甲省",
        "province_match": True,
        "subject_match": True,
        "min_score": 645,
        "min_rank": 4300,
        "delta": 100,
        "related_majors": "虚构专业",
        "remarks": "仅为合成测试",
        "major_groups": (),
        "match_reason": "位次区间匹配",
        "recommend_level": "★★",
        "strategy": "稳",
        "data_year": 2026,
        "source_ids": ("s2",),
        "evidence_status": EvidenceStatus.REFERENCE,
    }
    values.update(overrides)
    return RecommendationItem(**values)


def ordinary_policy(**overrides) -> OrdinaryBatchPolicy:
    values = {
        "schema_version": "1.0",
        "policy_id": "synthetic-ordinary-batch-v1",
        "basis_id": "synthetic-policy-basis-v1",
        "search_delta_min": -8000,
        "search_delta_max": 6000,
        "challenge_delta_lt": -2000,
        "stable_delta_le": 2000,
        "tier_caps": {"冲": 3, "稳": 4, "保": 5},
    }
    values.update(overrides)
    return OrdinaryBatchPolicy(**values)


def recommendations(**overrides) -> RecommendationResult:
    values = {
        "ordinary_batch_policy": ordinary_policy(),
        "items": (school_item(),),
        "excluded_by_subject_count": 0,
        "zero_score_excluded_count": 0,
        "input_years": (2026,),
        "usable_years": (2026,),
        "verified_rank_coverage": (1, 10000),
        "coverage_status": EvidenceStatus.PARTIAL,
        "empty_reason": None,
        "warnings": ("仅覆盖 2026",),
    }
    values.update(overrides)
    return RecommendationResult(**values)


def partial_task3_recommendations(rank: int = 4200) -> RecommendationResult:
    return recommend_schools(
        [
            {
                "year": 2026,
                "province": "演示甲省",
                "school_name": "部分覆盖大学",
                "school_code": "PARTIAL1",
                "subject_group": "物理",
                "major_group_name": "部分覆盖专业组",
                "major_group_code": "P01",
                "min_score": 620,
                "min_rank": 4300,
                "school_province": "演示甲省",
                "city_location": "演示市",
                "evidence_status": "reference",
                "coverage_status": "partial",
                "source_ids": ("s2",),
                "coverage_min_rank": 4000,
                "coverage_max_rank": 5000,
            }
        ],
        RecommendationProfile(
            rank=rank,
            target_province="演示甲省",
            subject_group="物理",
            secondary_subjects=frozenset(("化学", "地理")),
        ),
        ordinary_policy(),
    )


def rank_estimate() -> RankEstimate:
    return RankEstimate(
        status=EvidenceStatus.INFERRED,
        lower_rank=3900,
        upper_rank=4500,
        median_rank=4200,
        method="school_rank_offset_median_observed_spread",
        confidence="moderate",
        input_anchor_count=2,
        usable_anchor_count=2,
        rejected_anchor_count=0,
        rejection_reasons=(),
        reason_code=None,
        reasons=("student_score_not_used_no_versioned_model",),
        contributing_anchor_ids=("a1", "a2"),
        contributing_years=(2025, 2026),
        contributing_source_ids=("s3", "s4"),
        tolerance_rank=300,
    )


def _legacy_pathway_policy(*, formal: bool) -> PathwayPolicy:
    """Controlled v1 compatibility input; the engine owns every field trail."""

    return PathwayPolicy(
        policy_id="policy-formal" if formal else "policy-1",
        pathway_type="special_program",
        title="虚构正式专项" if formal else "虚构专项",
        institution="虚构乙大学",
        province="演示甲省",
        subject_mode="3+1+2",
        valid_year=2026,
        eligibility_requirements=() if formal else ("服务期未核实",),
        disqualifying_facts=(),
        professional_options=("虚构专业",),
        training_arrangements="合成培养安排",
        transition_rules="合成转段规则",
        outcomes="合成出口说明",
        service_employment_obligations=(
            "合成服务就业说明" if formal else None
        ),
        penalty_exit_rules="合成退出规则",
        fees_and_subsidies="合成费用说明",
        policy_source_ids=(("s5",) if formal else ("s1",)),
        evidence_status=(
            EvidenceStatus.OFFICIAL if formal else EvidenceStatus.CONFLICT
        ),
        calculation_basis=(
            "已验证政策资格与版本化模型共同形成目标"
            if formal
            else "仅核验资格条件"
        ),
        timeline=("报名前复核当年简章",),
        preparation_actions=("整理申请材料",),
    )


def _legacy_pathway_profile() -> PathwayProfile:
    return PathwayProfile(
        rank=4200,
        province="演示甲省",
        subject_mode="3+1+2",
        current_year=2026,
        eligibility_facts=(),
    )


def pathway_rank_scenario() -> RankScenario:
    return RankScenario._create(
        status=EvidenceStatus.INFERRED,
        basis="authenticated_interval_intersection",
        optimistic_rank=3900,
        central_rank=4200,
        conservative_rank=4500,
        confidence="medium",
        source_ids=("s3", "s4"),
        contributing_years=(2025, 2026),
        backtest_error=None,
        reasons=("synthetic_rank_scenario",),
        channel_kinds=("school_anchor",),
        channel_statuses=("reference",),
        rejected_channel_count=0,
    )


def pathway_rank_model() -> RankAdjustmentModel:
    return RankAdjustmentModel(
        model_id="model-report",
        province="演示甲省",
        subject_mode="3+1+2",
        cohort_years=(2025, 2026),
        source_ids=("s6",),
        evidence_status=EvidenceStatus.OFFICIAL,
        method="documented_rank_delta",
        pathway_types=("special_program",),
        applicability_rank_min=1,
        applicability_rank_max=10000,
        score_table_rank_min=1,
        score_table_rank_max=10000,
        rank_delta=-700,
    )


def pathway_result() -> PathwayResult:
    return evaluate_pathways(
        _legacy_pathway_profile(),
        (_legacy_pathway_policy(formal=False),),
        rank_scenario=pathway_rank_scenario(),
    )


def formal_pathway_result() -> PathwayResult:
    return evaluate_pathways(
        _legacy_pathway_profile(),
        (_legacy_pathway_policy(formal=True),),
        pathway_rank_model(),
        rank_scenario=pathway_rank_scenario(),
    )


class EvidenceReportModelTest(unittest.TestCase):
    def build(self, **overrides):
        values = {
            "profile": student(),
            "recommendations": recommendations(),
            "rank": rank_estimate(),
            "pathways": pathway_result(),
            "evidence": evidence_snapshot(),
        }
        values.update(overrides)
        return build_report_model(**values)

    def test_report_shows_provenance_coverage_and_repeated_disclaimer(self):
        text = render_markdown(self.build())
        self.assertIn("能力档位：标准档", text)
        self.assertIn("证据状态：冲突", text)
        self.assertIn("证据置信度：无", text)
        self.assertIn("查询覆盖", text)
        self.assertIn("来源编号", text)
        self.assertIn("s1、s2、s3、s4", text)
        self.assertIn(evidence_snapshot().manifest_hash, text)
        self.assertGreaterEqual(
            text.count("基于公开数据由 AI 整理，仅供参考"),
            3,
        )
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

    def test_report_retains_and_renders_complete_ordinary_batch_policy(self):
        policy = ordinary_policy(
            policy_id="synthetic-ordinary-batch-v2",
            basis_id="synthetic-policy-basis-v2",
            search_delta_min=-7000,
            search_delta_max=5000,
            challenge_delta_lt=-1500,
            stable_delta_le=2500,
            tier_caps={"冲": 2, "稳": 3, "保": 4},
        )

        report = self.build(recommendations=recommendations(ordinary_batch_policy=policy))
        text = render_markdown(report)

        self.assertEqual(report.ordinary_batch_policy, policy)
        for literal in (
            "synthetic-ordinary-batch-v2",
            "synthetic-policy-basis-v2",
            "-7000",
            "5000",
            "-1500",
            "2500",
            "冲=2、稳=3、保=4",
        ):
            self.assertIn(literal, text)

    def test_direct_task3_partial_result_stays_directional_inside_declared_coverage(self):
        result = partial_task3_recommendations()
        self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)
        self.assertIsNone(result.verified_rank_coverage)
        self.assertEqual(result.items, ())
        self.assertEqual(result.observations[0].school_name, "部分覆盖大学")
        self.assertEqual(result.observations[0].evidence_status, EvidenceStatus.PARTIAL)
        report = self.build(recommendations=result)
        text = render_markdown(report)
        self.assertEqual(report.recommendation_coverage_status, EvidenceStatus.PARTIAL)
        self.assertEqual(report.recommendations, ())
        self.assertEqual(report.school_observations[0].school_name, "部分覆盖大学")
        self.assertEqual(report.school_observations[0].evidence_status, EvidenceStatus.PARTIAL)
        self.assertIn("s2", report.source_ids)
        self.assertIn("部分覆盖大学", text)
        self.assertIn("部分覆盖", text)
        self.assertIn("仅作方向性观察", text)
        self.assertNotIn("| 620 |", text)
        self.assertNotIn("| 4300 |", text)

    def test_partial_report_observations_do_not_gain_numbers_from_declared_bounds(self):
        report = self.build(recommendations=partial_task3_recommendations())
        self.assertEqual(report.recommendations, ())
        item = report.school_observations[0]
        self.assertEqual(item.evidence_status, EvidenceStatus.PARTIAL)
        self.assertFalse(hasattr(item, "min_score"))
        self.assertFalse(hasattr(item, "min_rank"))

        def rebuild(**changes):
            values = {field.name: getattr(report, field.name) for field in fields(report)}
            values.update(changes)
            return ReportModel._create(**values)

        for bounds in (None, (1, 10000), (4301, 5000)):
            with self.subTest(bounds=bounds):
                changed = rebuild(verified_rank_coverage=bounds)
                self.assertEqual(changed.recommendations, ())
                self.assertEqual(changed.school_observations, report.school_observations)
        with self.assertRaises(ValueError):
            rebuild(recommendation_empty_reason=None)

    def test_partial_report_outside_coverage_stays_directional(self):
        result = partial_task3_recommendations(rank=6000)
        self.assertEqual(result.items, ())
        self.assertEqual(result.empty_reason, "partial_observations_only")
        self.assertIsNone(result.verified_rank_coverage)

        report = self.build(
            profile=student(rank=6000),
            recommendations=result,
            rank=None,
        )
        text = render_markdown(report)
        self.assertEqual(report.recommendations, ())
        self.assertEqual(report.school_observations[0].school_name, "部分覆盖大学")
        self.assertIn("仅作方向性观察", text)
        self.assertNotIn("| 620 |", text)
        self.assertNotIn("| 4300 |", text)

    def test_machine_ids_with_phone_shaped_digits_bypass_human_text_scanning(self):
        snapshot = evidence_snapshot()
        session_id = "a13800138000bcdef123456789abcdef"
        manifest_hash = "sha256:" + session_id * 2
        manifest = EvidenceManifest(
            schema_version=snapshot.manifest.schema_version,
            session_id=session_id,
            capability_tier=snapshot.manifest.capability_tier,
            candidates_filename=snapshot.manifest.candidates_filename,
            facts_filename=snapshot.manifest.facts_filename,
            rejected_count=snapshot.manifest.rejected_count,
            manifest_hash=manifest_hash,
        )
        machine_snapshot = ValidatedEvidenceSnapshot._create(
            manifest,
            snapshot.capability,
            snapshot.retrieval_dates,
            snapshot.facts,
            snapshot.rejections,
        )

        try:
            report = self.build(evidence=machine_snapshot)
        except ValueError as error:
            self.fail(f"strict machine identifiers were treated as human text: {error}")
        text = render_markdown(report)

        self.assertEqual(report.manifest_session_id, session_id)
        self.assertEqual(report.manifest_hash, manifest_hash)
        self.assertIn(session_id, text)
        self.assertIn(manifest_hash, text)

    def test_rank_is_described_as_inference_with_interval_and_anchors(self):
        text = render_markdown(self.build())
        self.assertIn("推断位次区间：3900–4500", text)
        self.assertIn("容差：±300 位", text)
        self.assertIn("贡献锚点：a1、a2", text)
        self.assertNotIn("官方位次：4200", text)
        self.assertNotIn("官方分数线", text)

    def test_missing_optional_results_degrade_without_abort(self):
        text = render_markdown(self.build(rank=None, pathways=None))
        self.assertIn("喜报位次证据不足", text)
        self.assertIn("多元升学数据不足", text)
        self.assertIn("普通批", text)

    def test_masked_rows_never_become_numeric_boundaries(self):
        masked = recommendations(
            items=(),
            usable_years=(),
            coverage_status=EvidenceStatus.MASKED,
            empty_reason="unusable_evidence",
            warnings=("存在屏蔽值 6**，未进入计算",),
        )
        text = render_markdown(self.build(recommendations=masked, rank=None))
        self.assertIn("屏蔽", text)
        self.assertNotIn("最低分：600", text)
        self.assertNotIn("| 600 |", text)

    def test_true_empty_and_partial_empty_use_different_wording(self):
        exact_empty = recommendations(
            items=(),
            coverage_status=EvidenceStatus.REFERENCE,
            empty_reason="no_match_within_verified_coverage",
            warnings=(),
        )
        partial_empty = recommendations(
            items=(),
            coverage_status=EvidenceStatus.PARTIAL,
            empty_reason="no_match_within_verified_coverage",
        )
        exact_text = render_markdown(self.build(recommendations=exact_empty))
        partial_text = render_markdown(self.build(recommendations=partial_empty))
        self.assertIn("经验证覆盖范围内未找到匹配院校", exact_text)
        self.assertIn("不能解释为没有符合院校", partial_text)

    def test_cross_result_source_union_and_status_precedence_are_deterministic(self):
        model = self.build()
        self.assertEqual(model.source_ids, ("s1", "s2", "s3", "s4"))
        self.assertEqual(model.evidence_status, EvidenceStatus.CONFLICT)
        self.assertEqual(render_markdown(model), render_markdown(model))
        json.dumps(model.to_dict(), ensure_ascii=False, sort_keys=True)

    def test_model_snapshots_mutable_sequences_and_replace_revalidates(self):
        model = self.build()
        expected_warnings = model.warnings
        payload = model.to_dict()
        payload["warnings"].append("外部修改")
        self.assertEqual(model.warnings, expected_warnings)
        with self.assertRaises(TypeError):
            replace(model, warnings=model.warnings)
        with self.assertRaises(TypeError):
            ReportModel()

    def test_recommendation_result_invariants_survive_direct_replace(self):
        model = self.build()
        item = model.recommendations[0]
        invalid_changes = (
            {"verified_rank_coverage": None},
            {"verified_rank_coverage": (5000, 10000)},
            {"recommendation_empty_reason": "no_match_within_verified_coverage"},
            {"recommendation_coverage_status": EvidenceStatus.MISSING},
            {"recommendation_coverage_status": EvidenceStatus.OFFICIAL},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(model, **changes)
        with self.assertRaises((TypeError, ValueError)):
            replace(model, recommendations=(replace(item, delta=999),))
        with self.assertRaises((TypeError, ValueError)):
            replace(model, recommendations=(replace(item, calculation_basis="伪造依据"),))
        outside_rank = 20000
        outside_delta = outside_rank - model.profile.rank
        with self.assertRaises((TypeError, ValueError)):
            replace(
                model,
                recommendations=(
                    replace(
                        item,
                        min_rank=outside_rank,
                        delta=outside_delta,
                        calculation_basis=(
                            "2026 年已验证投档记录；最低位次与用户位次差 "
                            f"Δ={outside_delta:+d}"
                        ),
                    ),
                ),
            )
        with self.assertRaises((TypeError, ValueError)):
            replace(model, warnings=())

    def test_capability_snapshot_rechecks_overlap_tier_and_degradation(self):
        model = self.build()
        self.assertEqual(model.host_capabilities, ("browse", "search"))
        self.assertEqual(model.python_version, "3.10.0")
        with self.assertRaises((TypeError, ValueError)):
            replace(
                model,
                available_capabilities=("browse", "search", "vision"),
                missing_capabilities=("vision",),
            )
        with self.assertRaises((TypeError, ValueError)):
            replace(model, host_capabilities=("browse",),)
        with self.assertRaises((TypeError, ValueError)):
            replace(model, degradations=(), warnings=())
        with self.assertRaises((TypeError, ValueError)):
            replace(
                model,
                optional_modules=("docx",),
                missing_capabilities=("docx", "vision"),
            )
        with self.assertRaises((TypeError, ValueError)):
            replace(
                model,
                capability_tier=CapabilityTier.OFFLINE,
                query_coverage="仅使用本地或用户提供的已验证证据包",
                python_version="3.9.18",
            )

    def test_unicode_pii_and_local_paths_are_rejected_contextually(self):
        profile_secrets = (
            "name: Zhang San",
            "WECHAT：student-id",
            "微 信 ： student-id",
            "电话：01012345678",
            "就读 学校：某中学",
            "高三（1）班",
            "住址：某路1号",
            "api_key：secret",
        )
        for secret in profile_secrets:
            with self.subTest(secret=secret), self.assertRaises((TypeError, ValueError)):
                student(province=secret)
        for secret in ("姓名：张三", "wechat: wx-student", "就读学校：某中学", "高三（1）班"):
            with self.subTest(visible_secret=secret), self.assertRaises((TypeError, ValueError)):
                self.build(
                    recommendations=recommendations(
                        items=(school_item(remarks=secret),)
                    )
                )
        visible_paths = (
            "前缀C:\\Users\\hp\\secret.txt",
            "\\\\server\\share\\secret.txt",
            "/home/hp/secret.txt",
            "~/secret.txt",
            "/tmp/secret.txt",
            "前缀/custom/private.txt",
            "https：//example.test/private",
        )
        for path in visible_paths:
            with self.subTest(path=path), self.assertRaises((TypeError, ValueError)):
                self.build(
                    recommendations=recommendations(
                        items=(school_item(remarks=path),)
                    )
                )
        # Context matters: an institution name is not a student-school label.
        self.build(
            recommendations=recommendations(
                items=(school_item(school_name="虚构学校大学"),)
            )
        )

    def test_retrieval_dates_coverage_actions_and_pathway_details_are_projected(self):
        text = render_markdown(self.build(pathways=formal_pathway_result()))
        self.assertIn("检索日期：2026-08-23", text)
        self.assertIn("输入年份：2026", text)
        self.assertIn("可用年份：2026", text)
        self.assertIn("当前最需要做的事", text)
        self.assertIn("虚构专业", text)
        self.assertIn("合成培养安排", text)
        self.assertIn("合成转段规则", text)
        self.assertIn("合成出口说明", text)
        self.assertIn("转换过程：模型 model-report", text)
        self.assertIn("4200 + (-700) = 3500", text)
        self.assertIn("模型来源编号：s6", text)
        self.assertRegex(text, r"有依据的路径目标位次：3500.*位次模型证据状态：官方")
        self.assertIn("正式路径政策证据状态：官方", text)
        self.assertIn("战略价值：high", text)
        self.assertIn("依赖行动：补齐或复核关键证据缺口（evidence-gap-review）", text)
        self.assertIn("关联院校：虚构甲大学", text)
        self.assertIn("关联路径：虚构正式专项", text)
        model = self.build(pathways=formal_pathway_result())
        self.assertEqual(model.pathway_target_evidence_status, EvidenceStatus.OFFICIAL)
        with self.assertRaises((TypeError, ValueError)):
            replace(model, pathway_target_evidence_status=EvidenceStatus.OFFICIAL)

    def test_pathway_field_trails_are_projected_and_render_direct_and_derived_evidence(self):
        result = formal_pathway_result()
        model = self.build(pathways=result)
        projected = model.pathways[0]
        text = render_markdown(model)

        self.assertEqual(projected.field_evidence, result.items[0].field_evidence)
        self.assertTrue(
            all(
                report_record is source_record
                for report_record, source_record in zip(
                    projected.field_evidence, result.items[0].field_evidence
                )
            )
        )
        self.assertEqual(
            tuple(record.field for record in projected.field_evidence),
            PATHWAY_DISPLAY_EVIDENCE_FIELDS,
        )
        self.assertEqual(len(projected.field_evidence), 22)
        evidence_by_field = {
            record.field: record for record in projected.field_evidence
        }
        direct = evidence_by_field["professional_options"]
        derived = evidence_by_field["investment_decision"]
        self.assertIs(direct.origin, PathwayFieldEvidenceOrigin.LEGACY)
        self.assertEqual(direct.locators, ("policy-record:policy-formal:professional_options",))
        self.assertEqual(direct.evidence_method, "legacy-policy-field-v1")
        self.assertRegex(direct.origin_binding, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(direct.digest, r"^sha256:[0-9a-f]{64}$")
        self.assertIs(
            derived.origin, PathwayFieldEvidenceOrigin.DERIVED_DECISION
        )
        self.assertIn("professional_options", derived.upstream_fields)
        self.assertTrue(derived.upstream_evidence_digests)
        self.assertRegex(derived.origin_binding, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(derived.digest, r"^sha256:[0-9a-f]{64}$")
        self.assertIn("逐字段证据审计", text)
        self.assertIn(
            "字段：professional_options（专业选项）；证据状态：官方；覆盖：完整；"
            "来源编号：s5；证据定位：policy-record:policy-formal:professional_options；"
            "抽取方式：legacy-policy-record；证据方法："
            "legacy-policy-field-v1；上游字段：professional_options；"
            "画像字段：无；提示：无",
            text,
        )
        self.assertIn(
            "字段：investment_decision（投入结论）；证据状态：推断；覆盖：完整；"
            "来源编号：s5；证据定位：policy-record:policy-formal:",
            text,
        )

    def test_report_pathway_and_markdown_reject_one_missing_field_trail(self):
        model = self.build(pathways=formal_pathway_result())
        pathway = model.pathways[0]
        missing_one = pathway.field_evidence[:-1]

        with self.assertRaisesRegex(ValueError, "field evidence is incomplete"):
            replace(pathway, field_evidence=missing_one)

        object.__setattr__(pathway, "field_evidence", missing_one)
        with self.assertRaisesRegex(ValueError, "field evidence is incomplete"):
            render_markdown(model)

    def test_retrieval_dates_and_action_items_are_strict_model_fields(self):
        model = self.build()
        with self.assertRaises((TypeError, ValueError)):
            replace(model, retrieval_dates=("2026-02-30",))
        with self.assertRaises((TypeError, ValueError)):
            replace(model, retrieval_dates=("9999-12-31",))
        with self.assertRaises((TypeError, ValueError)):
            replace(model, retrieval_dates=("2026-08-24",))
        with self.assertRaises((TypeError, ValueError)):
            replace(model, retrieval_dates=())
        with self.assertRaises((TypeError, ValueError)):
            replace(model, action_items=("随意发明一个建议",))

    def test_rehashed_future_candidate_snapshot_cannot_bind_to_current_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(
                ROOT / "tests" / "fixtures" / "evidence" / "three-source-consensus",
                bundle,
            )
            candidate_path = bundle / "candidates.jsonl"
            candidates = [
                json.loads(line)
                for line in candidate_path.read_text(encoding="utf-8").splitlines()
            ]
            for candidate in candidates:
                candidate["retrieved_at"] = "2099-01-02T00:00:00Z"
            candidate_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                    for item in candidates
                ),
                encoding="utf-8",
                newline="\n",
            )
            manifest_path = bundle / "manifest.json"
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            capability_payload = json.loads((bundle / "capability.json").read_text(encoding="utf-8"))
            rejection_lines = (bundle / "rejections.jsonl").read_text(encoding="utf-8").splitlines()
            store = object.__new__(EvidenceStore)
            store._capability = capability_payload
            store._rejections = {str(index): None for index in range(len(rejection_lines))}
            records = {
                name: (bundle / name).read_text(encoding="utf-8")
                for name in (
                    "capability.json", "candidates.jsonl", "context.jsonl",
                    "normalized/facts.jsonl", "rejections.jsonl",
                )
            }
            manifest_payload["manifest_hash"] = EvidenceStore._manifest_hash(store, records)
            manifest_path.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            result = validate_bundle_snapshot(bundle)
            self.assertEqual(result.issues, ())
            assert result.snapshot is not None

            with self.assertRaises((TypeError, ValueError)):
                self.build(evidence=result.snapshot)

    def test_direct_pathway_projection_cannot_bypass_status_invariants(self):
        model = self.build()
        pending = model.pathways[0]
        with self.assertRaises((TypeError, ValueError)):
            replace(pending, status="formal", eligibility="pending_verification")
        with self.assertRaises((TypeError, ValueError)):
            replace(pending, status="formal", eligibility="eligible")
        with self.assertRaises((TypeError, ValueError)):
            replace(
                model,
                pathway_target_rank=100,
                pathway_transformation="documented synthetic transform",
                model_source_ids=("s5",),
                source_ids=(*model.source_ids, "s5"),
            )

        formal = self.build(pathways=formal_pathway_result()).pathways[0]
        for missing_detail in (
            {"professional_options": ()},
            {"training_arrangements": None},
            {"transition_rules": None},
            {"outcomes": None},
            {"service_employment_obligations": None},
            {"penalty_exit_rules": None},
            {"fees_and_subsidies": None},
        ):
            with self.subTest(missing_detail=missing_detail):
                with self.assertRaises((TypeError, ValueError)):
                    replace(formal, **missing_detail)

    def test_free_text_is_one_line_escaped_and_pii_is_rejected(self):
        piped = school_item(school_name="虚构甲大学 | 伪造列")
        text = render_markdown(
            self.build(recommendations=recommendations(items=(piped,)))
        )
        self.assertIn(r"虚构甲大学 \| 伪造列", text)
        self.assertNotIn("| 虚构甲大学 | 伪造列 |", text)
        with self.assertRaises((TypeError, ValueError)):
            self.build(
                recommendations=recommendations(
                    items=(school_item(school_name="虚构大学\n## 注入"),)
                )
            )
        with self.assertRaises((TypeError, ValueError)):
            student(province="手机号 13800138000")

    def test_capability_and_manifest_are_strict_snapshots(self):
        model = self.build()
        self.assertEqual(model.manifest_hash, evidence_snapshot().manifest_hash)
        with self.assertRaises(TypeError):
            replace(model, manifest_hash="sha256:" + "0" * 64)
        with self.assertRaises((TypeError, ValueError)):
            self.build(evidence={"manifest_hash": "sha256:" + "0" * 64})


class EvidenceReportCliTest(unittest.TestCase):
    def test_markdown_gate_uses_shared_semantics_and_never_echoes_rejected_amount(self):
        safe = "武汉大学学费 30000元；国家助学金 6000元\n"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(report_cli, "render_markdown", return_value=safe), mock.patch(
            "sys.stdout", stdout
        ), mock.patch("sys.stderr", stderr):
            safe_code = report_cli.main(self.command()[2:])

        rejected_amount = "30600元"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            report_cli,
            "render_markdown",
            return_value=f"升学规划产品售价 {rejected_amount}\n",
        ), mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            rejected_code = report_cli.main(self.command()[2:])

        self.assertEqual(safe_code, 0, stderr.getvalue())
        self.assertEqual(rejected_code, 2)
        self.assertNotIn(rejected_amount, stderr.getvalue())
        self.assertIn("REPORT_002", stderr.getvalue())

    def test_markdown_cli_masks_publication_oserror_without_path_or_pii(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "student-13800138000.md"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                report_cli,
                "_publish_markdown",
                side_effect=OSError(f"failed {output} 张三 secret"),
            ), mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                code = report_cli.main(self.command("--output", str(output))[2:])

        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "错误[REPORT_002]：报告生成或发布失败\n")
        for forbidden in (str(output), output.name, "13800138000", "张三", "secret"):
            self.assertNotIn(forbidden, stderr.getvalue())
    def test_cli_profile_rejects_private_user_free_text_before_engine_use(self):
        payload = json.loads(
            (ROOT / "tests" / "fixtures" / "profiles" / "demo.json").read_text(
                encoding="utf-8"
            )
        )
        payload["target_schools"] = ["name：Zhang San"]
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile.json"
            profile.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(report_cli.EvidenceReportInputError):
                report_cli._load_public_profile(profile)

    def test_new_recommendation_path_consumes_authenticated_rows_not_a_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            shutil.copytree(
                ROOT / "tests" / "fixtures" / "provinces" / "demo-312",
                directory,
            )
            validated = report_cli.validate_dataset_snapshot(directory.resolve())
            self.assertEqual(validated.issues, ())
            assert validated.snapshot is not None
            original = directory / "authenticated-tou_dang.csv"
            (directory / "tou_dang.csv").rename(original)
            (directory / "tou_dang.csv").write_text(
                original.read_text(encoding="utf-8").replace("虚构甲大学", "替换大学"),
                encoding="utf-8",
            )
            profile = report_cli.RecommendationProfile(
                rank=4200,
                target_province="演示甲省",
                subject_group="物理",
                secondary_subjects=frozenset(("化学", "地理")),
            )

            result = report_cli._public_recommendations(
                validated.snapshot.admission_rows,
                profile,
                validated.snapshot.config.ordinary_batch_policy,
                (),
            )

        self.assertTrue(all(item.school_name != "替换大学" for item in result.items))

    def command(self, *extra: str) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "generate_report.py"),
            "--dataset",
            str(ROOT / "tests" / "fixtures" / "provinces" / "demo-312"),
            "--profile",
            str(ROOT / "tests" / "fixtures" / "profiles" / "demo.json"),
            "--evidence",
            str(ROOT / "tests" / "fixtures" / "evidence" / "three-source-consensus"),
            *extra,
        ]

    def test_synthetic_cli_is_offline_deterministic_and_uses_new_boundaries(self):
        first = subprocess.run(
            self.command(), capture_output=True, text=True, encoding="utf-8"
        )
        second = subprocess.run(
            self.command(), capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("演示甲省", first.stdout)
        self.assertIn("基于公开数据由 AI 整理，仅供参考", first.stdout)
        self.assertNotIn("张三", first.stdout)
        self.assertNotIn("13800138000", first.stdout)
        self.assertNotIn("legacy-local-dataset", first.stdout)
        self.assertNotIn("− 4000", first.stdout)
        self.assertNotIn("http://", first.stdout)
        self.assertNotIn("https://", first.stdout)

    def test_invalid_evidence_is_exit_2_and_no_report_is_emitted(self):
        result = subprocess.run(
            self.command(
                "--evidence",
                str(ROOT / "tests" / "fixtures" / "evidence" / "repost-conflict"),
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("证据包", result.stderr)

    def test_authenticated_visible_text_rejected_by_report_gate_is_exit_2(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(
                ROOT / "tests" / "fixtures" / "evidence" / "three-source-consensus",
                bundle,
            )
            capability_path = bundle / "capability.json"
            capability_payload = json.loads(capability_path.read_text(encoding="utf-8"))
            capability_payload["degradations"] = ["wechat: wx-student"]
            capability_path.write_text(
                json.dumps(capability_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            manifest_path = bundle / "manifest.json"
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            rejection_lines = (bundle / "rejections.jsonl").read_text(encoding="utf-8").splitlines()
            store = object.__new__(EvidenceStore)
            store._capability = capability_payload
            store._rejections = {str(index): None for index in range(len(rejection_lines))}
            records = {
                name: (bundle / name).read_text(encoding="utf-8")
                for name in (
                    "capability.json", "candidates.jsonl", "context.jsonl",
                    "normalized/facts.jsonl", "rejections.jsonl",
                )
            }
            manifest_payload["manifest_hash"] = EvidenceStore._manifest_hash(store, records)
            manifest_path.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            result = subprocess.run(
                self.command("--evidence", str(bundle)),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_public_cli_does_not_overwrite_an_existing_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.md"
            output.write_text("sentinel", encoding="utf-8")
            result = subprocess.run(
                self.command("--output", str(output)),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel")

    def test_row_scoped_accepted_fact_can_enter_ordinary_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            validation = validate_dataset_snapshot(
                ROOT / "tests" / "fixtures" / "provinces" / "demo-312"
            )
            self.assertEqual(validation.issues, ())
            assert validation.snapshot is not None
            row_hash = admission_row_hash(validation.snapshot.admission_rows[0])
            store = EvidenceStore.create(
                Path(temporary).resolve(), capability()
            )
            for index in range(1, 4):
                store.add_candidate(
                    SourceCandidate(
                        source_id=f"cli-s{index}",
                        url=f"https://publisher-{index}.example.test/article",
                        publisher=f"Synthetic Publisher {index}",
                        tier=SourceTier.C,
                        published_at=None,
                        retrieved_at="2026-08-24T00:00:00Z",
                        content_hash=f"sha256:cli-{index}",
                        citation_root=f"https://publisher-{index}.example.test/original",
                        summary="Synthetic admission record",
                    )
                )
            store.add_fact(
                EvidenceFact(
                    fact_id="admission-1",
                    field="admission_record:demo-1",
                    value={
                        "year": 2026,
                        "province": "演示甲省",
                        "subject_group": "物理",
                        "school_code": "SYN312A",
                        "program_group": "第01组",
                        "remarks": "",
                        "min_score": 645,
                        "min_rank": 1100,
                        "coverage_min_rank": 1,
                        "coverage_max_rank": 10000,
                        "coverage_status": "partial",
                        "row_hash": row_hash,
                    },
                    unit=None,
                    status=EvidenceStatus.REFERENCE,
                    source_ids=("cli-s1", "cli-s2", "cli-s3"),
                    method="three-source-consensus",
                    notes="",
                ),
                year=2026,
                extraction_method="manual-structured",
                locator="dataset[demo-312]/admission[row-1]",
            )
            store.finalize()
            result = subprocess.run(
                self.command("--evidence", str(store.session_path)),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("虚构甲大学", result.stdout)
        self.assertIn("cli-s1、cli-s2、cli-s3", result.stdout)
        self.assertIn("数据覆盖：部分覆盖", result.stdout)

    def test_cli_rejects_hash_bound_fact_with_contradictory_fixed_row_fields(self):
        validation = validate_dataset_snapshot(
            ROOT / "tests" / "fixtures" / "provinces" / "demo-312"
        )
        self.assertEqual(validation.issues, ())
        assert validation.snapshot is not None
        row_hash = admission_row_hash(validation.snapshot.admission_rows[0])
        mismatches = {
            "year": 2025,
            "province": "替换省",
            "subject_group": "历史",
            "school_code": "SYN-OTHER",
            "program_group": "第99组",
            "remarks": "矛盾备注",
            "min_score": 999,
            "min_rank": 9999,
        }
        for field, mismatch in mismatches.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                store = EvidenceStore.create(Path(temporary).resolve(), capability())
                for index in range(1, 4):
                    store.add_candidate(
                        SourceCandidate(
                            source_id=f"mismatch-s{index}",
                            url=f"https://mismatch-{index}.example.test/article",
                            publisher=f"Mismatch Publisher {index}",
                            tier=SourceTier.C,
                            published_at=None,
                            retrieved_at="2026-08-24T00:00:00Z",
                            content_hash=f"sha256:mismatch-{index}",
                            citation_root=f"https://mismatch-{index}.example.test/original",
                            summary="Contradictory admission record",
                        )
                    )
                fact_value = {
                    "year": 2026,
                    "province": "演示甲省",
                    "subject_group": "物理",
                    "school_code": "SYN312A",
                    "program_group": "第01组",
                    "remarks": "",
                    "min_score": 645,
                    "min_rank": 1100,
                    "coverage_min_rank": 1,
                    "coverage_max_rank": 10000,
                    "coverage_status": "partial",
                    "row_hash": row_hash,
                }
                fact_value[field] = mismatch
                store.add_fact(
                    EvidenceFact(
                        fact_id="contradictory-admission",
                        field="admission_record:contradictory",
                        value=fact_value,
                        unit=None,
                        status=EvidenceStatus.REFERENCE,
                        source_ids=("mismatch-s1", "mismatch-s2", "mismatch-s3"),
                        method="three-source-consensus",
                        notes="",
                    ),
                    year=2026,
                    extraction_method="manual-structured",
                    locator="dataset[demo-312]/admission[row-1]",
                )
                store.finalize()

                result = subprocess.run(
                    self.command("--evidence", str(store.session_path)),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("虚构甲大学 | 645 | 1100", result.stdout)
                self.assertNotIn("mismatch-s1", result.stdout)
                self.assertIn("数据覆盖：冲突", result.stdout)

    def test_row_hash_fact_index_deduplicates_identical_and_conflicts_on_difference(self):
        validation = validate_dataset_snapshot(
            ROOT / "tests" / "fixtures" / "provinces" / "demo-312"
        )
        self.assertEqual(validation.issues, ())
        assert validation.snapshot is not None
        original = validation.snapshot.admission_rows[0]
        fact = EvidenceFact(
            fact_id="admission-row-deduplication",
            field="admission_record:deduplication",
            value={
                "year": 2026,
                "province": "演示甲省",
                "subject_group": "物理",
                "school_code": "SYN312A",
                "program_group": "第01组",
                "remarks": "",
                "min_score": 645,
                "min_rank": 1100,
                "coverage_min_rank": 1,
                "coverage_max_rank": 10000,
                "coverage_status": "reference",
                "row_hash": admission_row_hash(original),
            },
            unit=None,
            status=EvidenceStatus.REFERENCE,
            source_ids=("cli-s1", "cli-s2", "cli-s3"),
            method="three-source-consensus",
            notes="",
        ).to_dict()
        profile = RecommendationProfile(
            rank=1100,
            target_province="演示甲省",
            subject_group="物理",
            secondary_subjects=frozenset(("化学", "地理")),
        )

        duplicate_result = report_cli._public_recommendations(
            (original,),
            profile,
            validation.snapshot.config.ordinary_batch_policy,
            (fact, fact),
        )
        self.assertEqual(len(duplicate_result.items), 1)
        self.assertEqual(
            duplicate_result.items[0].source_ids,
            ("cli-s1", "cli-s2", "cli-s3"),
        )
        self.assertEqual(
            duplicate_result.items[0].evidence_status,
            EvidenceStatus.REFERENCE,
        )
        self.assertEqual(duplicate_result.verified_rank_coverage, (1, 10000))
        self.assertEqual(duplicate_result.observations, ())

        conflicting_fact = dict(fact)
        conflicting_fact["value"] = dict(fact["value"])
        conflicting_fact["value"]["min_score"] = 999
        conflict_result = report_cli._public_recommendations(
            (original,),
            profile,
            validation.snapshot.config.ordinary_batch_policy,
            (fact, conflicting_fact),
        )
        self.assertEqual(conflict_result.items, ())
        self.assertEqual(conflict_result.coverage_status, EvidenceStatus.CONFLICT)
        self.assertIsNone(conflict_result.verified_rank_coverage)

    def test_whole_row_hash_rejects_every_non_numeric_row_mutation(self):
        validation = validate_dataset_snapshot(
            ROOT / "tests" / "fixtures" / "provinces" / "demo-312"
        )
        self.assertEqual(validation.issues, ())
        assert validation.snapshot is not None
        original = validation.snapshot.admission_rows[0]
        fact = EvidenceFact(
            fact_id="admission-row-binding",
            field="admission_record:binding",
            value={
                "year": 2026,
                "province": "演示甲省",
                "subject_group": "物理",
                "school_code": "SYN312A",
                "program_group": "第01组",
                "remarks": "",
                "min_score": 645,
                "min_rank": 1100,
                "coverage_min_rank": 1,
                "coverage_max_rank": 10000,
                "coverage_status": "partial",
                "row_hash": admission_row_hash(original),
            },
            unit=None,
            status=EvidenceStatus.REFERENCE,
            source_ids=("cli-s1", "cli-s2", "cli-s3"),
            method="three-source-consensus",
            notes="",
        ).to_dict()
        profile = RecommendationProfile(
            rank=1100,
            target_province="演示甲省",
            subject_group="物理",
            secondary_subjects=frozenset(("化学", "地理")),
        )
        mutations = {
            "school_name": "替换大学",
            "school_level": "替换层次",
            "city_location": "替换城市",
            "province_location": "替换省份",
            "majors_in_group": ("替换专业",),
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                payload = original.to_dict()
                payload[field] = value
                mutated = ValidatedAdmissionRow.from_mapping(payload)
                result = report_cli._public_recommendations(
                    (mutated,),
                    profile,
                    validation.snapshot.config.ordinary_batch_policy,
                    (fact,),
                )
                self.assertEqual(result.items, ())
                self.assertEqual(result.coverage_status, EvidenceStatus.CONFLICT)
        self.assertNotIn("cli-s1", result.to_dict().__repr__())


class MarkdownPublicationTest(unittest.TestCase):
    def test_private_write_is_fsynced_before_exclusive_atomic_publication(self):
        real_fsync = report_cli.os.fsync
        visibility = []
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.md"

            def observe_fsync(descriptor):
                visibility.append(output.exists())
                return real_fsync(descriptor)

            with mock.patch.object(report_cli.os, "fsync", side_effect=observe_fsync):
                report_cli._publish_markdown("完整报告\n", output)

            self.assertEqual(visibility, [False])
            self.assertEqual(output.read_text(encoding="utf-8"), "完整报告\n")
            self.assertEqual(list(Path(temporary).iterdir()), [output])

    def test_partial_private_write_and_publish_failures_leave_no_owned_files(self):
        for failure_point in ("fsync", "link"):
            with self.subTest(failure_point=failure_point), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "report.md"
                target = report_cli.os.fsync if failure_point == "fsync" else report_cli.os.link
                with mock.patch.object(
                    report_cli.os,
                    failure_point,
                    side_effect=OSError(f"synthetic {failure_point} failure"),
                ):
                    with self.assertRaisesRegex(OSError, f"synthetic {failure_point} failure"):
                        report_cli._publish_markdown("partial bytes", output)
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_competing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.md"

            def competitor(_source, destination):
                Path(destination).write_text("RIVAL", encoding="utf-8")
                raise FileExistsError("synthetic competitor")

            with mock.patch.object(report_cli.os, "link", side_effect=competitor):
                with self.assertRaisesRegex(FileExistsError, "synthetic competitor"):
                    report_cli._publish_markdown("ours", output)
            self.assertEqual(output.read_text(encoding="utf-8"), "RIVAL")
            self.assertEqual(list(Path(temporary).iterdir()), [output])

    def test_cleanup_error_never_masks_primary_publish_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.md"
            with mock.patch.object(
                report_cli.os, "link", side_effect=OSError("primary publish failure")
            ), mock.patch.object(
                report_cli.Path, "unlink", autospec=True, side_effect=OSError("cleanup failure")
            ):
                with self.assertRaisesRegex(OSError, "primary publish failure"):
                    report_cli._publish_markdown("ours", output)


if __name__ == "__main__":
    unittest.main()
