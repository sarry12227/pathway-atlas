from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceManifest,
    EvidenceFact,
    EvidenceStatus,
    RecommendationItem,
    RecommendationProfile,
    RecommendationResult,
    SourceCandidate,
    SourceTier,
)
from scripts.evidence import EvidenceStore
from scripts.path_recommend import PathwayItem, PathwayResult
from scripts.rank_calc import RankEstimate
from scripts.report_model import StudentProfile, build_report_model, render_markdown


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


def manifest() -> EvidenceManifest:
    return EvidenceManifest(
        schema_version="1.0",
        session_id="11111111111111111111111111111111",
        capability_tier=CapabilityTier.STANDARD,
        candidates_filename="candidates.jsonl",
        facts_filename="normalized/facts.jsonl",
        rejected_count=0,
        manifest_hash="sha256:" + "1" * 64,
    )


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


def recommendations(**overrides) -> RecommendationResult:
    values = {
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


def pathway_result() -> PathwayResult:
    pending = PathwayItem(
        policy_id="policy-1",
        pathway_type="special_program",
        title="虚构专项",
        institution="虚构乙大学",
        status="pending_verification",
        eligibility="pending_verification",
        missing_constraints=("服务期未核实",),
        policy_source_ids=("s1",),
        evidence_status=EvidenceStatus.CONFLICT,
        calculation_basis="仅核验资格条件，未执行位次换算",
        target_rank=None,
    )
    return PathwayResult(
        items=(pending,),
        formal_shortlist=(),
        target_rank=None,
        transformation=None,
        model_source_ids=(),
        warnings=("未提供有依据的位次模型",),
    )


class EvidenceReportModelTest(unittest.TestCase):
    def build(self, **overrides):
        values = {
            "profile": student(),
            "capability": capability(),
            "recommendations": recommendations(),
            "rank": rank_estimate(),
            "pathways": pathway_result(),
            "manifest": manifest(),
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
        self.assertIn(manifest().manifest_hash, text)
        self.assertGreaterEqual(text.count("AI 生成，仅供参考"), 3)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

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
        warnings = ["合成提示"]
        model = replace(self.build(), warnings=warnings)
        warnings.append("后加内容")
        self.assertEqual(model.warnings, ("合成提示",))
        payload = model.to_dict()
        payload["warnings"].append("外部修改")
        self.assertEqual(model.warnings, ("合成提示",))
        with self.assertRaises((TypeError, ValueError)):
            replace(model, source_ids=("../unsafe",))

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
        with self.assertRaises((TypeError, ValueError)):
            self.build(manifest=replace(manifest(), manifest_hash=""))
        with self.assertRaises((TypeError, ValueError)):
            self.build(capability=replace(capability(), degradations=["x\n## injected"]))
        with self.assertRaises((TypeError, ValueError)):
            self.build(manifest={"capability_tier": "official"})
        fake_full = CapabilityReport(
            tier=CapabilityTier.FULL,
            host_capabilities=(),
            available_capabilities=(),
            missing_capabilities=(),
            degradations=(),
            python_version="3.10.0",
            optional_modules=(),
        )
        with self.assertRaises((TypeError, ValueError)):
            self.build(
                capability=fake_full,
                manifest=replace(manifest(), capability_tier=CapabilityTier.FULL),
            )


class EvidenceReportCliTest(unittest.TestCase):
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
        self.assertIn("AI 生成，仅供参考", first.stdout)
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
                    },
                    unit=None,
                    status=EvidenceStatus.REFERENCE,
                    source_ids=("cli-s1", "cli-s2", "cli-s3"),
                    method="three-source-consensus",
                    notes="",
                )
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


if __name__ == "__main__":
    unittest.main()
