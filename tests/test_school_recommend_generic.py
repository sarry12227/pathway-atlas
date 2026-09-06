# -*- coding: utf-8 -*-
"""Province-neutral, evidence-aware school matching behavior tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path


SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from contracts import (  # noqa: E402
    EvidenceStatus,
    OrdinaryBatchPolicy,
    RecommendationProfile,
    RecommendationResult,
)
from school_recommend import (  # noqa: E402
    SchoolRecommendError,
    is_in_province,
    parse_secondary_subjects,
    personalize_school_recommendations,
    recommend_schools as public_recommend_schools,
)
from adapters.school_fit_bridge import (  # noqa: E402
    validate_school_fit_enriched_admission_row,
)
from planning_profile import PlanningProfile  # noqa: E402
from rank_locator import RankScenario  # noqa: E402
from province_registry import discover_provinces  # noqa: E402
from validate_data import (  # noqa: E402
    ValidatedAdmissionRow,
    admission_row_hash,
    validate_runtime_admission_row,
)
from tests.test_planning_profile import reference_payload  # noqa: E402


def ordinary_policy(**changes):
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
    values.update(changes)
    return OrdinaryBatchPolicy(**values)


def recommend_schools(rows, value, policy=None):
    """Keep existing behavior cases terse while exercising the explicit API."""

    return public_recommend_schools(rows, value, policy or ordinary_policy())


def admission_row(**changes):
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
        "school_type": "综合",
        "school_province": "上海市",
        "city_location": "上海",
        "remarks": "",
        "evidence_status": "official",
        "coverage_status": "official",
        "source_ids": ["source-2025-01"],
        "coverage_min_rank": 5000,
        "coverage_max_rank": 12000,
    }
    row.update(changes)
    return row


def authenticated_school_fit_row(
    *,
    enrollment: bool = False,
    subject: bool = False,
    charter: bool = False,
    **changes,
):
    """Build a fit-enriched fixture whose metadata is bound to its base row."""

    raw = admission_row(**changes)
    ordinary_evidence = {
        name: raw.pop(name)
        for name in (
            "evidence_status",
            "coverage_status",
            "source_ids",
            "coverage_min_rank",
            "coverage_max_rank",
        )
    }
    fit_values = {
        name: raw.pop(name)
        for name in (
            "majors_in_group",
            "school_province",
            "city_location",
            "institution_type",
            "adjustment_required",
            "affordable_for",
            "required_secondary_subjects",
            "secondary_subject_rule",
        )
        if name in raw
    }
    base = ValidatedAdmissionRow.from_mapping(raw)
    enriched = base.to_dict()
    enriched["admission_evidence_row_hash"] = admission_row_hash(base)
    source_ids = []

    if enrollment:
        enrollment_source = f"fit-enrollment-{raw['school_code']}"
        source_ids.append(enrollment_source)
        for name in (
            "majors_in_group",
            "school_province",
            "city_location",
            "institution_type",
        ):
            if name in fit_values:
                value = fit_values[name]
                if name == "majors_in_group" and isinstance(value, str):
                    value = tuple(json.loads(value))
                enriched[name] = value
        enriched.update(
            {
                "school_fit_enrollment_source_ids": (enrollment_source,),
                "school_fit_enrollment_status": "official",
            }
        )
    if subject:
        subject_source = f"fit-subject-{raw['school_code']}"
        source_ids.append(subject_source)
        required = fit_values.get("required_secondary_subjects", ())
        enriched["required_secondary_subjects"] = tuple(sorted(required))
        enriched["secondary_subject_rule"] = fit_values.get(
            "secondary_subject_rule", "all"
        )
        enriched.update(
            {
                "school_fit_subject_source_ids": (subject_source,),
                "school_fit_subject_status": "official",
            }
        )
    if charter:
        charter_source = f"fit-charter-{raw['school_code']}"
        source_ids.append(charter_source)
        enriched["charter_adjustment_required"] = fit_values.get(
            "adjustment_required", False
        )
        enriched.update(
            {
                "school_fit_charter_source_ids": (charter_source,),
                "school_fit_charter_status": "official",
            }
        )

    enriched["school_fit_source_ids"] = tuple(sorted(source_ids))
    validated = ValidatedAdmissionRow.from_mapping(enriched)
    recovered, recovered_hash = validate_school_fit_enriched_admission_row(validated)
    if recovered != base or recovered_hash != admission_row_hash(base):
        raise AssertionError("school-fit fixture failed base-row replay")
    result = validated.to_dict()
    result.update(ordinary_evidence)
    return result


def profile(**changes):
    values = {
        "rank": 8000,
        "target_province": "上海",
        "subject_group": "物理",
        "secondary_subjects": frozenset({"化学", "生物"}),
    }
    values.update(changes)
    return RecommendationProfile(**values)


def planning_profile(**changes):
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
    for section in ("preparation_assets", "constraints", "priorities"):
        if section in changes:
            payload[section].update(changes.pop(section))
    payload.update(changes)
    return PlanningProfile.create(payload)


def exact_rank(rank=8000):
    return RankScenario._create(
        status=EvidenceStatus.OFFICIAL,
        basis="official_score_table",
        optimistic_rank=rank,
        central_rank=rank,
        conservative_rank=rank,
        confidence="high",
        source_ids=("rank-official",),
        contributing_years=(2025,),
        backtest_error=None,
        reasons=("official_score_table",),
        channel_kinds=("official_score_table",),
        channel_statuses=("official",),
        rejected_channel_count=0,
    )


def interval_rank():
    return RankScenario._create(
        status=EvidenceStatus.INFERRED,
        basis="authenticated_interval",
        optimistic_rank=6000,
        central_rank=8000,
        conservative_rank=10000,
        confidence="medium",
        source_ids=("rank-a", "rank-b"),
        contributing_years=(2024, 2025),
        backtest_error=0.02,
        reasons=("authenticated_interval",),
        channel_kinds=("joint_exam",),
        channel_statuses=("corroborated",),
        rejected_channel_count=0,
    )


class ProvinceNormalizationTest(unittest.TestCase):
    def test_exact_canonical_province_equality_is_not_substring_matching(self):
        self.assertTrue(is_in_province("上海市", "上海"))
        self.assertTrue(is_in_province("演示甲省", "演示甲"))
        self.assertFalse(is_in_province("江苏", "上海"))
        self.assertFalse(is_in_province("上海交通大学", "上海"))
        self.assertFalse(is_in_province("", "上海"))
        self.assertFalse(is_in_province(None, "上海"))

    def test_autonomous_regions_and_special_regions_use_official_short_names(self):
        pairs = (
            ("内蒙古自治区", "内蒙古"),
            ("广西壮族自治区", "广西"),
            ("宁夏回族自治区", "宁夏"),
            ("新疆维吾尔自治区", "新疆"),
            ("香港特别行政区", "香港"),
            ("澳门特别行政区", "澳门"),
        )
        for full_name, short_name in pairs:
            with self.subTest(full_name=full_name):
                self.assertTrue(is_in_province(full_name, short_name))

    def test_invalid_official_suffix_variants_never_canonicalize(self):
        invalid_pairs = (
            ("上海省", "上海市"),
            ("香港省", "香港特别行政区"),
            ("新疆自治区", "新疆维吾尔自治区"),
            ("广西自治区", "广西壮族自治区"),
            ("上海省", "上海省"),
            ("新疆自治区", "新疆自治区"),
        )
        for invalid, other in invalid_pairs:
            with self.subTest(invalid=invalid, other=other):
                self.assertFalse(is_in_province(invalid, other))

        self.assertTrue(is_in_province("演示甲省", "演示甲"))
        self.assertTrue(is_in_province("XX省", "XX"))


class PolicyAndCityPreferenceTest(unittest.TestCase):
    def test_explicit_policy_changes_classification_and_is_retained(self):
        row = admission_row(min_rank=6500)
        challenge_policy = ordinary_policy(challenge_delta_lt=-1000)
        stable_policy = ordinary_policy(
            policy_id="synthetic-ordinary-batch-v2",
            basis_id="synthetic-policy-basis-v2",
            challenge_delta_lt=-2000,
        )

        challenge = public_recommend_schools([row], profile(), challenge_policy)
        stable = public_recommend_schools([row], profile(), stable_policy)

        self.assertEqual(challenge.items[0].strategy, "冲")
        self.assertEqual(stable.items[0].strategy, "稳")
        self.assertEqual(challenge.ordinary_batch_policy, challenge_policy)
        self.assertEqual(stable.ordinary_batch_policy.basis_id, "synthetic-policy-basis-v2")

    def test_two_authenticated_province_configs_classify_the_same_delta_differently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for province, policy in (
                ("阈值甲省", ordinary_policy(challenge_delta_lt=-1000)),
                (
                    "阈值乙省",
                    ordinary_policy(
                        policy_id="synthetic-ordinary-batch-v2",
                        basis_id="synthetic-policy-basis-v2",
                        challenge_delta_lt=-2000,
                    ),
                ),
            ):
                directory = root / province
                directory.mkdir()
                (directory / "province.json").write_text(
                    json.dumps(
                        {
                            "province": province,
                            "mode": "3+1+2",
                            "primary_subjects": ["物理", "历史"],
                            "secondary_subjects": ["化学", "生物"],
                            "score_scale": 750,
                            "schema_version": "1.0",
                            "ordinary_batch_policy": policy.to_dict(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            configs = discover_provinces(root)

            first = public_recommend_schools(
                [admission_row(min_rank=6500)], profile(), configs["阈值甲省"].ordinary_batch_policy
            )
            second = public_recommend_schools(
                [admission_row(min_rank=6500)], profile(), configs["阈值乙省"].ordinary_batch_policy
            )

        self.assertEqual(first.items[0].strategy, "冲")
        self.assertEqual(second.items[0].strategy, "稳")

    def test_exact_target_city_orders_after_school_intent_and_marks_reason(self):
        rows = [
            admission_row(
                school_name="甲意向大学",
                school_code="D001",
                city_location="外地市",
                min_rank=8001,
            ),
            admission_row(
                school_name="乙城市大学",
                school_code="D002",
                city_location="目标市",
                min_rank=8002,
            ),
            admission_row(
                school_name="丙普通大学",
                school_code="D003",
                city_location="目标市新区",
                min_rank=8000,
            ),
        ]
        selected = recommend_schools(
            rows,
            profile(target_schools=("甲意向大学",), target_cities=("目标市",)),
        )

        self.assertEqual(
            tuple(item.school_name for item in selected.items),
            ("甲意向大学", "乙城市大学", "丙普通大学"),
        )
        city_item = selected.items[1]
        self.assertIn("用户意向城市", city_item.match_reason)
        self.assertEqual(city_item.recommend_level, "★★★")
        self.assertNotIn("用户意向城市", selected.items[2].match_reason)

    def test_empty_target_cities_preserve_existing_deterministic_order(self):
        rows = [
            admission_row(
                school_name="乙大学", school_code="D002", city_location="目标市", min_rank=8002
            ),
            admission_row(
                school_name="甲大学", school_code="D001", city_location="外地市", min_rank=8001
            ),
        ]

        result = recommend_schools(rows, profile(target_cities=()))

        self.assertEqual(tuple(item.school_name for item in result.items), ("甲大学", "乙大学"))


class SecondarySubjectParserTest(unittest.TestCase):
    def test_parser_accepts_explicit_string_and_sequence_formats(self):
        self.assertEqual(parse_secondary_subjects("化学、生物 / 地理"),
                         frozenset({"化学", "生物", "地理"}))
        self.assertEqual(parse_secondary_subjects(["化学", "生物"]),
                         frozenset({"化学", "生物"}))
        self.assertEqual(parse_secondary_subjects(None), frozenset())

    def test_explicit_any_and_all_rules_are_applied(self):
        any_row = admission_row(
            school_name="任一大学",
            required_secondary_subjects=["化学", "地理"],
            secondary_subject_rule="any",
        )
        all_row = admission_row(
            school_name="全部大学",
            school_code="D002",
            required_secondary_subjects=["化学", "地理"],
            secondary_subject_rule="all",
        )

        result = recommend_schools([any_row, all_row], profile())

        self.assertEqual(tuple(item.school_name for item in result.items),
                         ("任一大学",))
        self.assertEqual(result.excluded_by_subject_count, 1)
        self.assertTrue(result.items[0].subject_match)

    def test_normalized_requirement_precedes_legacy_remarks(self):
        row = admission_row(
            required_secondary_subjects=["化学"],
            secondary_subject_rule="all",
            remarks="再选科目：地理",
        )
        result = recommend_schools([row], profile(secondary_subjects=frozenset({"化学"})))
        self.assertEqual(len(result.items), 1)

    def test_legacy_and_or_remarks_remain_compatible(self):
        rows = [
            admission_row(school_name="化生大学", remarks="再选科目：化学和生物"),
            admission_row(school_name="任选大学", school_code="D002",
                          remarks="再选科目：化学或地理"),
        ]
        result = recommend_schools(
            rows,
            profile(secondary_subjects=frozenset({"化学", "生物"})),
        )
        self.assertEqual({item.school_name for item in result.items},
                         {"化生大学", "任选大学"})

    def test_unknown_explicit_rule_fails_closed_before_rank_parsing(self):
        row = admission_row(
            required_secondary_subjects=["化学"],
            secondary_subject_rule="xor",
            min_score="600?",
            min_rank="6**",
        )
        result = recommend_schools([row], profile())
        self.assertEqual(result.items, ())
        self.assertEqual(result.excluded_by_subject_count, 1)
        self.assertNotEqual(result.coverage_status, EvidenceStatus.MASKED)


class EvidenceAndCoverageTest(unittest.TestCase):
    def test_zero_score_placeholder_is_counted_and_never_recommended(self):
        zero = admission_row(min_score=0)
        result = recommend_schools([zero], profile())
        self.assertEqual(result.items, ())
        self.assertEqual(result.zero_score_excluded_count, 1)
        self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)
        self.assertIn("0分占位已剔除：1 行", result.warnings)

    def test_zero_score_with_masked_rank_accumulates_both_risks(self):
        row = admission_row(min_score=0, min_rank="6**")
        result = recommend_schools([row], profile())
        self.assertEqual(result.items, ())
        self.assertEqual(result.zero_score_excluded_count, 1)
        self.assertEqual(result.coverage_status, EvidenceStatus.MASKED)
        self.assertIn("0分占位已剔除：1 行", result.warnings)
        self.assertTrue(any("屏蔽值" in warning for warning in result.warnings))

    def test_explicit_masked_zero_score_accumulates_both_risks(self):
        row = admission_row(min_score=0, masked=True)
        result = recommend_schools([row], profile())
        self.assertEqual(result.items, ())
        self.assertEqual(result.zero_score_excluded_count, 1)
        self.assertEqual(result.coverage_status, EvidenceStatus.MASKED)
        self.assertIn("0分占位已剔除：1 行", result.warnings)
        self.assertTrue(any("屏蔽值" in warning for warning in result.warnings))

    def test_non_strict_zero_like_values_are_masked_not_zero_placeholders(self):
        for value in (False, 0.0, "0?"):
            with self.subTest(value=value):
                result = recommend_schools([admission_row(min_score=value)], profile())
                self.assertEqual(result.items, ())
                self.assertEqual(result.zero_score_excluded_count, 0)
                self.assertEqual(result.coverage_status, EvidenceStatus.MASKED)

    def test_mixed_zero_placeholder_keeps_only_valid_items_and_count(self):
        zero = admission_row(school_name="占位大学", min_score=0)
        valid = admission_row(school_name="有效大学", school_code="D002")
        result = recommend_schools([zero, valid], profile())
        self.assertEqual(tuple(item.school_name for item in result.items), ("有效大学",))
        self.assertEqual(result.zero_score_excluded_count, 1)
        self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)

    def test_subject_filter_runs_before_invalid_numeric_fields(self):
        row = admission_row(
            required_secondary_subjects=["思想政治"],
            secondary_subject_rule="all",
            min_score="600?",
            min_rank="6**",
        )
        result = recommend_schools([row], profile())
        self.assertEqual(result.items, ())
        self.assertEqual(result.excluded_by_subject_count, 1)
        self.assertNotEqual(result.coverage_status, EvidenceStatus.MASKED)

    def test_admission_province_filter_runs_before_invalid_numeric_fields(self):
        invalid_other_province = admission_row(
            province="江苏",
            school_name="外省招生记录",
            min_score="600?",
            min_rank="6**",
        )
        invalid_missing_province = admission_row(
            province="",
            school_name="缺省份招生记录",
            min_score="600?",
            min_rank="6**",
        )
        result = recommend_schools(
            [invalid_other_province, invalid_missing_province, admission_row()],
            profile(),
        )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.coverage_status, EvidenceStatus.OFFICIAL)

    def test_masked_or_ocr_uncertain_values_are_never_recommended(self):
        masked = admission_row(min_rank="6**")
        uncertain = admission_row(
            school_name="OCR大学",
            school_code="D002",
            ocr_uncertain=True,
        )
        result = recommend_schools([masked, uncertain], profile())
        self.assertEqual(result.coverage_status, EvidenceStatus.MASKED)
        self.assertEqual(result.items, ())
        self.assertEqual(result.empty_reason, "unusable_evidence")
        self.assertTrue(any("屏蔽" in warning for warning in result.warnings))

    def test_float_bool_and_nan_are_not_coerced_to_exact_integers(self):
        rows = [
            admission_row(school_name="浮点大学", min_rank=8000.0),
            admission_row(school_name="布尔大学", school_code="D002", min_score=True),
            admission_row(school_name="非数大学", school_code="D003", min_score=float("nan")),
        ]
        result = recommend_schools(rows, profile())
        self.assertEqual(result.items, ())
        self.assertEqual(result.coverage_status, EvidenceStatus.MASKED)

    def test_status_precedence_is_conflict_then_masked_then_partial_then_missing(self):
        cases = (
            (("missing", "partial", "masked", "conflict"), EvidenceStatus.CONFLICT),
            (("missing", "partial", "masked"), EvidenceStatus.MASKED),
            (("missing", "partial"), EvidenceStatus.PARTIAL),
            (("missing",), EvidenceStatus.MISSING),
        )
        for statuses, expected in cases:
            rows = [
                admission_row(
                    school_name=f"状态大学-{status}",
                    school_code=f"D{index}",
                    evidence_status=("official" if status == "partial" else status),
                    coverage_status=status,
                )
                for index, status in enumerate(statuses)
            ]
            with self.subTest(statuses=statuses):
                result = recommend_schools(rows, profile())
                self.assertEqual(result.coverage_status, expected)

    def test_aggregate_status_keeps_every_distinct_risk_warning(self):
        partial = admission_row(
            school_name="部分大学",
            evidence_status="official",
            coverage_status="partial",
            coverage_max_rank=7000,
        )
        conflict = admission_row(
            school_name="冲突大学",
            school_code="D002",
            evidence_status="conflict",
        )
        result = recommend_schools([partial, conflict], profile())
        self.assertEqual(result.coverage_status, EvidenceStatus.CONFLICT)
        self.assertEqual(result.items, ())
        self.assertEqual(
            tuple(item.school_name for item in result.observations),
            ("部分大学",),
        )
        self.assertEqual(
            result.warnings[:2],
            (
                "证据存在冲突，冲突行未进入精确推荐。",
                "数据覆盖不完整；结论仅适用于当前已验证覆盖范围内。",
            ),
        )
        self.assertEqual(len(result.warnings), len(set(result.warnings)))

    def test_reference_fact_with_partial_coverage_is_nonnumeric_inside_declared_range(self):
        row = admission_row(evidence_status="reference", coverage_status="partial")
        inside = recommend_schools([row], profile(rank=8000))

        self.assertEqual(inside.items, ())
        self.assertEqual(inside.coverage_status, EvidenceStatus.PARTIAL)
        self.assertIsNone(inside.verified_rank_coverage)
        self.assertEqual(len(inside.observations), 1)
        self.assertEqual(inside.observations[0].school_name, "演示大学")
        self.assertFalse(hasattr(inside.observations[0], "min_rank"))
        self.assertEqual(inside.empty_reason, "partial_observations_only")
        self.assertTrue(any("不进入精确冲稳保" in value for value in inside.warnings))

    def test_partial_coverage_outside_verified_range_is_non_numeric_observation(self):
        row = admission_row(evidence_status="reference", coverage_status="partial")
        outside = recommend_schools([row], profile(rank=13000))

        self.assertEqual(outside.items, ())
        self.assertEqual(outside.coverage_status, EvidenceStatus.PARTIAL)
        self.assertIsNone(outside.verified_rank_coverage)
        self.assertEqual(len(outside.observations), 1)
        observation = outside.observations[0]
        self.assertEqual(observation.school_name, "演示大学")
        self.assertEqual(observation.evidence_status, EvidenceStatus.PARTIAL)
        self.assertEqual(observation.source_ids, ("source-2025-01",))
        self.assertFalse(hasattr(observation, "min_score"))
        self.assertFalse(hasattr(observation, "min_rank"))
        self.assertTrue(any("不进入精确冲稳保" in value for value in outside.warnings))
        self.assertEqual(outside.empty_reason, "partial_observations_only")

    def test_numeric_result_rejects_partial_items_even_with_matching_coverage(self):
        exact = recommend_schools([admission_row()], profile(rank=8000))
        for unusable in (
            EvidenceStatus.PARTIAL,
            EvidenceStatus.INFERRED,
            EvidenceStatus.MISSING,
            EvidenceStatus.MASKED,
            EvidenceStatus.CONFLICT,
        ):
            for coverage in (EvidenceStatus.PARTIAL, EvidenceStatus.OFFICIAL):
                with self.subTest(
                    unusable=unusable, coverage=coverage
                ), self.assertRaisesRegex(ValueError, "numeric recommendations"):
                    replace(
                        exact,
                        items=(replace(exact.items[0], evidence_status=unusable),),
                        coverage_status=coverage,
                        verified_rank_coverage=(5000, 12000),
                    )

    def test_exact_numeric_items_can_keep_partial_aggregate_coverage(self):
        exact = recommend_schools([admission_row()], profile(rank=8000))
        for accepted in (
            EvidenceStatus.OFFICIAL,
            EvidenceStatus.CORROBORATED,
            EvidenceStatus.REFERENCE,
        ):
            with self.subTest(accepted=accepted):
                result = replace(
                    exact,
                    items=(replace(exact.items[0], evidence_status=accepted),),
                    coverage_status=EvidenceStatus.PARTIAL,
                )
                self.assertEqual(result.items[0].min_rank, 8000)
                self.assertEqual(result.items[0].evidence_status, accepted)
                self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)

    def test_coverage_is_not_inferred_from_recommendation_hits(self):
        row = admission_row()
        row.pop("coverage_min_rank")
        row.pop("coverage_max_rank")
        result = recommend_schools([row], profile())
        self.assertEqual(result.items, ())
        self.assertIsNone(result.verified_rank_coverage)
        self.assertEqual(result.coverage_status, EvidenceStatus.MISSING)
        self.assertEqual(result.empty_reason, "missing_verified_coverage")

    def test_true_empty_differs_from_outside_verified_coverage(self):
        no_band_match = admission_row(min_rank=20000)
        verified = recommend_schools([no_band_match], profile(rank=8000))
        outside_row = admission_row(
            min_rank=20000,
            coverage_min_rank=5000,
            coverage_max_rank=7000,
        )
        outside = recommend_schools([outside_row], profile(rank=8000))
        self.assertEqual(verified.empty_reason, "no_match_within_verified_coverage")
        self.assertEqual(outside.empty_reason, "rank_outside_verified_coverage")

    def test_years_are_sorted_and_single_year_warning_is_exact(self):
        single = recommend_schools([admission_row(year=2025)], profile())
        self.assertIn("仅覆盖 2025", single.warnings)

        rows = [
            admission_row(year=2025),
            admission_row(year=2023, school_name="往年大学", school_code="D002"),
            admission_row(year=2024, school_name="中间大学", school_code="D003",
                          evidence_status="masked"),
        ]
        result = recommend_schools(rows, profile())
        self.assertEqual(result.input_years, (2023, 2024, 2025))
        self.assertEqual(result.usable_years, (2023, 2025))


class ResultContractTest(unittest.TestCase):
    def test_result_and_nested_items_are_immutable_and_json_safe(self):
        input_row = admission_row()
        result = recommend_schools([input_row], profile())
        self.assertIsInstance(result, RecommendationResult)
        self.assertIsInstance(result.items, tuple)
        self.assertEqual(json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
                         ["items"][0]["evidence_status"], "official")
        self.assertEqual(result.items[0].source_ids, ("source-2025-01",))
        self.assertEqual(result.items[0].data_year, 2025)
        self.assertEqual(result.items[0].school_province, "上海市")
        self.assertTrue(result.items[0].province_match)
        self.assertTrue(result.items[0].subject_match)
        with self.assertRaises(FrozenInstanceError):
            result.empty_reason = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.items[0].school_name = "changed"

    def test_result_is_a_snapshot_and_does_not_mutate_inputs(self):
        input_row = admission_row(source_ids=["source-original"])
        original = dict(input_row)
        result = recommend_schools([input_row], profile())
        self.assertEqual(input_row, original)

        input_row["school_name"] = "被修改大学"
        input_row["source_ids"].append("source-late")
        self.assertEqual(result.items[0].school_name, "演示大学")
        self.assertEqual(result.items[0].source_ids, ("source-original",))

    def test_profile_is_frozen_and_json_safe(self):
        value = profile()
        encoded = json.dumps(value.to_dict(), ensure_ascii=False)
        self.assertEqual(json.loads(encoded)["secondary_subjects"], ["化学", "生物"])
        with self.assertRaises(FrozenInstanceError):
            value.rank = 1

    def test_profile_snapshots_mutable_constructor_collections(self):
        selected = {"化学"}
        schools = ["演示大学"]
        value = RecommendationProfile(
            rank=8000,
            target_province="上海",
            subject_group="物理",
            secondary_subjects=selected,
            target_schools=schools,
        )
        selected.add("地理")
        schools.append("后来大学")
        self.assertEqual(value.secondary_subjects, frozenset({"化学"}))
        self.assertEqual(value.target_schools, ("演示大学",))
        json.dumps(value.to_dict(), ensure_ascii=False)
        with self.assertRaises(TypeError):
            RecommendationProfile(
                rank=8000,
                target_province="上海",
                subject_group="物理",
                secondary_subjects="化学生物",
            )

    def test_profile_validates_scalar_and_collection_boundaries(self):
        invalid_scalars = (
            {"rank": True, "target_province": "上海", "subject_group": "物理"},
            {"rank": 0, "target_province": "上海", "subject_group": "物理"},
            {"rank": 8000, "target_province": None, "subject_group": "物理"},
            {"rank": 8000, "target_province": " ", "subject_group": "物理"},
            {"rank": 8000, "target_province": "上海", "subject_group": None},
            {"rank": 8000, "target_province": "上海", "subject_group": " "},
        )
        for values in invalid_scalars:
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    RecommendationProfile(**values)

        for field in (
            "secondary_subjects",
            "target_major_categories",
            "target_cities",
            "target_schools",
        ):
            for invalid in ([1], [["化学"]], [" "], "化学生物"):
                with self.subTest(field=field, invalid=invalid):
                    values = {
                        "rank": 8000,
                        "target_province": "上海",
                        "subject_group": "物理",
                        field: invalid,
                    }
                    with self.assertRaises((TypeError, ValueError)):
                        RecommendationProfile(**values)

    def test_profile_strips_and_snapshots_all_collection_elements(self):
        subjects = [" 化学 ", "生物"]
        majors = [" 计算机 "]
        cities = [" 上海 "]
        schools = [" 演示大学 "]
        value = RecommendationProfile(
            rank=8000,
            target_province=" 上海 ",
            subject_group=" 物理 ",
            secondary_subjects=subjects,
            target_major_categories=majors,
            target_cities=cities,
            target_schools=schools,
        )
        subjects.append("地理")
        majors[0] = "后来专业"
        cities.clear()
        schools.append("后来大学")
        self.assertEqual(value.target_province, "上海")
        self.assertEqual(value.subject_group, "物理")
        self.assertEqual(value.secondary_subjects, frozenset({"化学", "生物"}))
        self.assertEqual(value.target_major_categories, ("计算机",))
        self.assertEqual(value.target_cities, ("上海",))
        self.assertEqual(value.target_schools, ("演示大学",))

    def test_invalid_profile_rank_is_a_controlled_error(self):
        with self.assertRaises(SchoolRecommendError) as caught:
            recommend_schools([admission_row()], {"rank": True, "target_province": "上海"})
        self.assertEqual(caught.exception.code, "REC_001")

    def test_mapping_profile_does_not_bypass_collection_validation(self):
        with self.assertRaises(SchoolRecommendError) as mapping_error:
            recommend_schools(
                [admission_row()],
                {
                    "rank": 8000,
                    "target_province": "上海",
                    "subject_group": "物理",
                    "target_cities": "上海",
                },
            )
        self.assertEqual(mapping_error.exception.code, "REC_001")


class FullProfileSchoolDecisionTest(unittest.TestCase):
    def test_partial_rows_inside_scenario_remain_nonnumeric_without_delta_policy(self):
        result = personalize_school_recommendations(
            [
                admission_row(
                    evidence_status="reference",
                    coverage_status="partial",
                )
            ],
            planning_profile(),
            rank_scenario=interval_rank(),
        )

        self.assertEqual(result.items, ())
        self.assertEqual(result.decisions, ())
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].school_name, "演示大学")
        self.assertFalse(hasattr(result.observations[0], "min_rank"))
        self.assertTrue(any("位次差策略不可用" in item for item in result.warnings))

    def test_empty_institution_types_is_unknown_and_reject_filters_each_major_group(self):
        rows = [
            authenticated_school_fit_row(
                enrollment=True,
                charter=True,
                school_name="混合专业组大学",
                school_code="M001",
                major_group_name="",
                major_group_code="",
                program_group="第00组",
                adjustment_required=True,
                institution_type="public",
            ),
            authenticated_school_fit_row(
                enrollment=True,
                charter=True,
                school_name="混合专业组大学",
                school_code="M001",
                major_group_name="",
                major_group_code="",
                program_group="第01组",
                adjustment_required=False,
                institution_type="public",
            ),
        ]

        result = personalize_school_recommendations(
            rows,
            planning_profile(
                constraints={
                    "institution_types": [],
                    "adjustment_preference": "reject",
                }
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )

        self.assertEqual(tuple(item.school_name for item in result.items), ("混合专业组大学",))
        self.assertEqual(result.items[0].major_groups[0].major_group_name, "第01组")
        self.assertNotIn(
            "SCHOOL_INSTITUTION_TYPE_BLOCKED",
            {reason.code for reason in result.decision("混合专业组大学").reasons},
        )

    def test_within_tier_order_uses_priority_adjustment_evidence_and_stable_ids(self):
        school_target = authenticated_school_fit_row(
            enrollment=True,
            charter=True,
            school_name="A目标院校",
            school_code="S001",
            major_group_code="GA",
            majors_in_group='["法学"]',
            adjustment_required=False,
            evidence_status="reference",
            coverage_status="reference",
            source_ids=["reference-a"],
        )
        major_target = authenticated_school_fit_row(
            enrollment=True,
            charter=True,
            school_name="Z目标专业",
            school_code="S002",
            major_group_code="GB",
            majors_in_group='["计算机科学与技术"]',
            adjustment_required=False,
            evidence_status="official",
            coverage_status="official",
            source_ids=["official-b"],
        )
        common_priorities = {
            "target_schools": ["A目标院校"],
            "target_majors": ["计算机"],
            "target_regions": [],
        }
        school_first = personalize_school_recommendations(
            [major_target, school_target],
            planning_profile(
                priorities={**common_priorities, "school_vs_major": "school_first"}
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        major_first = personalize_school_recommendations(
            [school_target, major_target],
            planning_profile(
                priorities={**common_priorities, "school_vs_major": "major_first"}
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        evidence_only = personalize_school_recommendations(
            [school_target, major_target],
            planning_profile(
                priorities={
                    "target_schools": [],
                    "target_majors": [],
                    "target_regions": [],
                    "school_vs_major": "unknown",
                }
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )

        self.assertEqual(school_first.items[0].school_name, "A目标院校")
        self.assertEqual(major_first.items[0].school_name, "Z目标专业")
        self.assertEqual(evidence_only.items[0].school_name, "Z目标专业")

        adjustment_rows = [
            authenticated_school_fit_row(
                enrollment=True,
                charter=True,
                school_name="A无需调剂",
                school_code="A001",
                adjustment_required=False,
            ),
            authenticated_school_fit_row(
                enrollment=True,
                charter=True,
                school_name="Z接受调剂",
                school_code="A002",
                adjustment_required=True,
            ),
        ]
        adjustment_accept = personalize_school_recommendations(
            adjustment_rows,
            planning_profile(
                constraints={"adjustment_preference": "accept"},
                priorities={"target_regions": []},
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        shuffled = personalize_school_recommendations(
            list(reversed(adjustment_rows)),
            planning_profile(
                constraints={"adjustment_preference": "accept"},
                priorities={"target_regions": []},
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        expected = ("Z接受调剂", "A无需调剂")
        self.assertEqual(tuple(item.school_name for item in adjustment_accept.items), expected)
        self.assertEqual(tuple(item.school_name for item in shuffled.items), expected)

    def test_target_reason_presence_changes_commitment_only_for_a_matching_target(self):
        rows = [admission_row(school_name="演示大学", school_code="T001")]
        without_reason = personalize_school_recommendations(
            rows,
            planning_profile(
                priorities={"target_schools": ["演示大学"]},
                target_school_reasons=[],
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        committed = personalize_school_recommendations(
            rows,
            planning_profile(
                priorities={"target_schools": ["演示大学"]},
                target_school_reasons=["家庭已确认该目标"],
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        unrelated = personalize_school_recommendations(
            rows,
            planning_profile(
                priorities={"target_schools": ["其他大学"]},
                target_school_reasons=["家庭已确认其他目标"],
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )

        self.assertIn(
            "SCHOOL_TARGET_SCHOOL_MATCH",
            {reason.code for reason in without_reason.decision("演示大学").reasons},
        )
        self.assertEqual(
            next(
                reason.input_fields
                for reason in without_reason.decision("演示大学").reasons
                if reason.code == "SCHOOL_TARGET_SCHOOL_MATCH"
            ),
            ("priorities.target_schools", "target_school_reasons"),
        )
        self.assertIn(
            "SCHOOL_TARGET_SCHOOL_COMMITTED",
            {reason.code for reason in committed.decision("演示大学").reasons},
        )
        self.assertEqual(
            next(
                reason.input_fields
                for reason in committed.decision("演示大学").reasons
                if reason.code == "SCHOOL_TARGET_SCHOOL_COMMITTED"
            ),
            ("priorities.target_schools", "target_school_reasons"),
        )
        self.assertFalse(
            {
                "SCHOOL_TARGET_SCHOOL_MATCH",
                "SCHOOL_TARGET_SCHOOL_COMMITTED",
            }
            & {reason.code for reason in unrelated.decision("演示大学").reasons}
        )

    def test_authenticated_public_rows_make_same_rank_target_profiles_differ(self):
        canonical_rows = (
            {
                "year": 2025,
                "province": "上海",
                "subject_group": "物理",
                "school_code": "D101",
                "school_name": "武汉计算机大学",
                "program_group": "第01组",
                "min_score": 620,
                "min_rank": 8001,
                "remarks": "",
                "city_location": "武汉",
                "school_province": "湖北",
                "majors_in_group": ("计算机科学与技术",),
                "institution_type": "public",
                "affordable_for": ("limited", "moderate", "flexible"),
                "adjustment_required": False,
            },
            {
                "year": 2025,
                "province": "上海",
                "subject_group": "物理",
                "school_code": "D102",
                "school_name": "上海医科大学",
                "program_group": "第01组",
                "min_score": 620,
                "min_rank": 8002,
                "remarks": "",
                "city_location": "上海",
                "school_province": "上海",
                "majors_in_group": ("临床医学",),
                "institution_type": "public",
                "affordable_for": ("limited", "moderate", "flexible"),
                "adjustment_required": False,
            },
        )
        validated = tuple(
            validate_runtime_admission_row(
                row,
                province="上海",
                subject_group="物理",
                score_scale=750,
                allowed_years=(2025,),
            )
            for row in canonical_rows
        )
        self.assertNotEqual(admission_row_hash(validated[0]), admission_row_hash(validated[1]))
        rows = []
        for index, row in enumerate(validated):
            replayed = row.to_dict()
            replayed.update(
                {
                    "evidence_status": "official",
                    "coverage_status": "official",
                    "source_ids": [f"public-admission-{index}"],
                    "coverage_min_rank": 5000,
                    "coverage_max_rank": 12000,
                }
            )
            rows.append(
                authenticated_school_fit_row(enrollment=True, **replayed)
            )
        computing = personalize_school_recommendations(
            rows,
            planning_profile(
                priorities={
                    "target_majors": ["计算机"],
                    "target_regions": ["武汉"],
                    "target_schools": [],
                    "school_vs_major": "major_first",
                }
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )
        medicine = personalize_school_recommendations(
            rows,
            planning_profile(
                priorities={
                    "target_majors": ["临床医学"],
                    "target_regions": ["上海"],
                    "target_schools": [],
                    "school_vs_major": "major_first",
                }
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )

        self.assertEqual(computing.recommendations.items[0].school_name, "武汉计算机大学")
        self.assertEqual(medicine.recommendations.items[0].school_name, "上海医科大学")
        computing_codes = {
            reason.code for reason in computing.decision("武汉计算机大学").reasons
        }
        same_school_other_profile_codes = {
            reason.code for reason in medicine.decision("武汉计算机大学").reasons
        }
        self.assertIn("SCHOOL_TARGET_MAJOR_COMMITTED", computing_codes)
        self.assertIn("SCHOOL_TARGET_REGION_MATCH", computing_codes)
        self.assertNotEqual(computing_codes, same_school_other_profile_codes)
        self.assertNotEqual(
            computing.decision("武汉计算机大学").stable_key,
            medicine.decision("上海医科大学").stable_key,
        )

    def test_excluded_region_institution_type_and_subject_emit_block_codes(self):
        rows = [
            authenticated_school_fit_row(
                enrollment=True,
                school_name="排除地区大学",
                school_code="D201",
                city_location="武汉",
                school_province="湖北",
            ),
            authenticated_school_fit_row(
                enrollment=True,
                school_name="民办大学",
                school_code="D202",
                institution_type="private",
            ),
            authenticated_school_fit_row(
                enrollment=True,
                subject=True,
                school_name="选科不符大学",
                school_code="D203",
                required_secondary_subjects=["政治"],
                secondary_subject_rule="all",
            ),
            authenticated_school_fit_row(
                enrollment=True,
                school_name="保留大学",
                school_code="D204",
            ),
        ]
        result = personalize_school_recommendations(
            rows,
            planning_profile(
                constraints={
                    "excluded_regions": ["武汉"],
                    "institution_types": ["public"],
                },
                priorities={"target_regions": []},
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )

        self.assertEqual(
            tuple(item.school_name for item in result.recommendations.items),
            ("保留大学",),
        )
        self.assertIn(
            "SCHOOL_EXCLUDED_REGION",
            {reason.code for reason in result.decision("排除地区大学").reasons},
        )
        self.assertIn(
            "SCHOOL_INSTITUTION_TYPE_BLOCKED",
            {reason.code for reason in result.decision("民办大学").reasons},
        )
        self.assertIn(
            "SCHOOL_SUBJECT_MISMATCH",
            {reason.code for reason in result.decision("选科不符大学").reasons},
        )

    def test_risk_changes_tier_caps_without_changing_historical_tiers(self):
        rows = [
            admission_row(
                school_name=f"冲刺大学{index}",
                school_code=f"R{index}",
                min_rank=7000 + index,
            )
            for index in range(5)
        ]
        conservative = personalize_school_recommendations(
            rows,
            planning_profile(constraints={"risk_preference": "conservative"}),
            ordinary_policy(tier_caps={"冲": 5, "稳": 5, "保": 5}),
            rank_scenario=interval_rank(),
        )
        aggressive = personalize_school_recommendations(
            rows,
            planning_profile(constraints={"risk_preference": "aggressive"}),
            ordinary_policy(tier_caps={"冲": 5, "稳": 5, "保": 5}),
            rank_scenario=interval_rank(),
        )

        self.assertEqual({item.strategy for item in conservative.recommendations.items}, {"冲"})
        self.assertEqual({item.strategy for item in aggressive.recommendations.items}, {"冲"})
        self.assertEqual(len(conservative.recommendations.items), 1)
        self.assertEqual(len(aggressive.recommendations.items), 5)
        self.assertTrue(
            any(
                "SCHOOL_RISK_CAP_EXCLUDED" in {reason.code for reason in decision.reasons}
                for decision in conservative.decisions
                if decision.outcome == "excluded"
            )
        )

    def test_unknown_cost_and_adjustment_data_are_explicitly_uncertain(self):
        result = personalize_school_recommendations(
            [admission_row()],
            planning_profile(
                constraints={
                    "budget_level": "limited",
                    "adjustment_preference": "reject",
                }
            ),
            ordinary_policy(),
            rank_scenario=exact_rank(),
        )

        reasons = result.decision("演示大学").reasons
        self.assertIn("SCHOOL_AFFORDABILITY_UNVERIFIED", {item.code for item in reasons})
        self.assertIn("SCHOOL_ADJUSTMENT_UNVERIFIED", {item.code for item in reasons})
        self.assertTrue(
            all(
                item.effect == "uncertain"
                for item in reasons
                if item.code.endswith("_UNVERIFIED")
            )
        )

    def test_snapshot_path_needs_no_placeholder_rank_delta_policy(self):
        result = personalize_school_recommendations(
            [admission_row(min_rank=9000)],
            planning_profile(),
            rank_scenario=interval_rank(),
        )

        self.assertEqual(result.policy_status, "rank_delta_policy_unavailable")
        self.assertIsNone(result.compatibility_result)
        self.assertEqual(tuple(item.school_name for item in result.items), ("演示大学",))
        self.assertEqual(result.items[0].strategy, "稳")
        self.assertIn("位次差策略不可用", "；".join(result.warnings))



if __name__ == "__main__":
    unittest.main()
