# -*- coding: utf-8 -*-
"""Province-neutral, evidence-aware school matching behavior tests."""

from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import FrozenInstanceError


SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from contracts import (  # noqa: E402
    EvidenceStatus,
    RecommendationProfile,
    RecommendationResult,
)
from school_recommend import (  # noqa: E402
    SchoolRecommendError,
    is_in_province,
    parse_secondary_subjects,
    recommend_schools,
)


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
        "source_ids": ["source-2025-01"],
        "coverage_min_rank": 5000,
        "coverage_max_rank": 12000,
    }
    row.update(changes)
    return row


def profile(**changes):
    values = {
        "rank": 8000,
        "target_province": "上海",
        "subject_group": "物理",
        "secondary_subjects": frozenset({"化学", "生物"}),
    }
    values.update(changes)
    return RecommendationProfile(**values)


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

    def test_mixed_zero_placeholder_keeps_only_valid_items_and_legacy_count(self):
        zero = admission_row(school_name="占位大学", min_score=0)
        valid = admission_row(school_name="有效大学", school_code="D002")
        result = recommend_schools([zero, valid], profile())
        self.assertEqual(tuple(item.school_name for item in result.items), ("有效大学",))
        self.assertEqual(result.zero_score_excluded_count, 1)
        self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)

        legacy = recommend_schools(
            [zero, valid], year=2025, estimated_prov_rank=8000,
            target_province="上海",
        )
        self.assertEqual(legacy["statistics"]["zero_score_excluded_count"], 1)
        names = {
            item["school_name"]
            for values in legacy["recommendations"].values()
            for item in values
        }
        self.assertEqual(names, {"有效大学"})

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
                    evidence_status=status,
                )
                for index, status in enumerate(statuses)
            ]
            with self.subTest(statuses=statuses):
                result = recommend_schools(rows, profile())
                self.assertEqual(result.coverage_status, expected)

    def test_aggregate_status_keeps_every_distinct_risk_warning(self):
        partial = admission_row(
            school_name="部分大学",
            evidence_status="partial",
        )
        conflict = admission_row(
            school_name="冲突大学",
            school_code="D002",
            evidence_status="conflict",
        )
        result = recommend_schools([partial, conflict], profile())
        self.assertEqual(result.coverage_status, EvidenceStatus.CONFLICT)
        self.assertEqual(tuple(item.school_name for item in result.items), ("部分大学",))
        self.assertEqual(
            result.warnings[:2],
            (
                "证据存在冲突，冲突行未进入精确推荐。",
                "数据覆盖不完整；结论仅适用于当前已验证覆盖范围内。",
            ),
        )
        self.assertEqual(len(result.warnings), len(set(result.warnings)))

    def test_partial_exact_rows_are_used_only_inside_explicit_verified_coverage(self):
        row = admission_row(evidence_status="partial")
        inside = recommend_schools([row], profile(rank=8000))
        outside = recommend_schools([row], profile(rank=13000))
        self.assertEqual(len(inside.items), 1)
        self.assertEqual(inside.coverage_status, EvidenceStatus.PARTIAL)
        self.assertTrue(any("当前已验证覆盖范围内" in value
                            for value in inside.warnings))
        self.assertEqual(outside.items, ())
        self.assertEqual(outside.empty_reason, "rank_outside_verified_coverage")

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

    def test_mapping_and_legacy_adapters_do_not_bypass_collection_validation(self):
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

        with self.assertRaises(SchoolRecommendError) as legacy_error:
            recommend_schools(
                [admission_row()],
                year=2025,
                estimated_prov_rank=8000,
                target_city="上海",
            )
        self.assertEqual(legacy_error.exception.code, "REC_001")


class DispatchContractTest(unittest.TestCase):
    def test_profile_mode_rejects_every_explicit_legacy_keyword_even_default_values(self):
        legacy_keywords = (
            {"year": None},
            {"estimated_prov_rank": None},
            {"subject_group": "物理"},
            {"target_province": None},
            {"target_major_category": None},
            {"target_city": None},
            {"target_schools_preference": None},
            {"secondary_subjects": None},
            {"params": None},
        )
        for legacy_kwargs in legacy_keywords:
            with self.subTest(keyword=next(iter(legacy_kwargs))):
                with self.assertRaises(SchoolRecommendError) as caught:
                    recommend_schools([admission_row()], profile(), **legacy_kwargs)
                self.assertEqual(caught.exception.code, "REC_003")

    def test_legacy_mode_requires_both_year_and_estimated_rank(self):
        cases = (
            {},
            {"year": 2025},
            {"estimated_prov_rank": 8000},
        )
        for legacy_kwargs in cases:
            with self.subTest(legacy_kwargs=legacy_kwargs):
                with self.assertRaises(SchoolRecommendError) as caught:
                    recommend_schools([admission_row()], **legacy_kwargs)
                self.assertEqual(caught.exception.code, "REC_001")


if __name__ == "__main__":
    unittest.main()
