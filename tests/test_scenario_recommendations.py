from __future__ import annotations

import math
import unittest
from dataclasses import replace

from scripts.contracts import EvidenceStatus, OrdinaryBatchPolicy, RecommendationProfile
from scripts.rank_locator import RankScenario
from scripts.school_recommend import SchoolRecommendError, recommend_schools


def policy() -> OrdinaryBatchPolicy:
    return OrdinaryBatchPolicy(
        schema_version="1.0",
        policy_id="scenario-policy-v1",
        basis_id="scenario-basis-v1",
        search_delta_min=-10000,
        search_delta_max=10000,
        challenge_delta_lt=-2000,
        stable_delta_le=2000,
        tier_caps={"冲": 5, "稳": 5, "保": 5},
    )


def profile() -> RecommendationProfile:
    return RecommendationProfile(
        rank=22000,
        target_province="湖北",
        subject_group="历史",
        secondary_subjects=frozenset({"地理", "政治"}),
        rank_basis="inferred",
        optimistic_rank=18000,
        conservative_rank=27000,
        rank_confidence="medium",
        rank_source_ids=("rank-a", "rank-b", "rank-c"),
    )


def scenario() -> RankScenario:
    return RankScenario._create(
        status=EvidenceStatus.INFERRED,
        basis="multi_channel_ensemble",
        optimistic_rank=18000,
        central_rank=22000,
        conservative_rank=27000,
        confidence="medium",
        source_ids=("rank-a", "rank-b", "rank-c"),
        contributing_years=(2024, 2025, 2026),
        backtest_error=0.02,
        reasons=("deterministic_weighted_median",),
        channel_kinds=("joint_exam", "school_anchor"),
        channel_statuses=("official", "reference"),
        rejected_channel_count=0,
    )


def row(school: str, year: int, min_rank: int, **changes) -> dict:
    value = {
        "year": year,
        "province": "湖北",
        "school_name": school,
        "school_code": f"S-{school}",
        "subject_group": "历史",
        "major_group_name": "第01组",
        "major_group_code": "G01",
        "min_score": 600,
        "min_rank": min_rank,
        "majors_in_group": '["历史学"]',
        "school_level": "双一流",
        "school_type": "综合",
        "school_province": "湖北",
        "city_location": "武汉",
        "remarks": "",
        "evidence_status": "reference",
        "coverage_status": "reference",
        "source_ids": [f"{school}-{year}-a", f"{school}-{year}-b", f"{school}-{year}-c"],
        "coverage_min_rank": 1000,
        "coverage_max_rank": 50000,
    }
    value.update(changes)
    return value


def rows() -> list[dict]:
    thresholds = {
        "保底大学": (27000, 27500, 28000),
        "稳妥大学": (24000, 23000, 20000),
        "冲刺大学": (20000, 19000, 18000),
        "观察大学": (17000, 16000, 15000),
    }
    return [
        row(school, year, rank)
        for school, ranks in thresholds.items()
        for year, rank in zip((2024, 2025, 2026), ranks)
    ]


class ScenarioRecommendationTest(unittest.TestCase):
    def test_three_rank_scenarios_classify_safe_stable_rush_and_observe(self):
        result = recommend_schools(
            rows(), profile(), policy(), rank_scenario=scenario()
        )
        strategies = {item.school_name: item.strategy for item in result.items}
        self.assertEqual(
            strategies,
            {
                "保底大学": "保",
                "稳妥大学": "稳",
                "冲刺大学": "冲",
                "观察大学": "观察",
            },
        )
        self.assertEqual(result.rank_basis, "inferred")
        self.assertEqual(result.rank_bounds, (18000, 22000, 27000))
        self.assertEqual(result.rank_confidence, "medium")
        self.assertEqual(result.rank_source_ids, ("rank-a", "rank-b", "rank-c"))

    def test_majority_is_ceil_two_thirds_of_each_groups_comparable_years(self):
        result = recommend_schools(
            rows(), profile(), policy(), rank_scenario=scenario()
        )
        by_school = {item.school_name: item for item in result.items}
        stable = by_school["稳妥大学"]
        self.assertEqual(stable.supporting_years, (2024, 2025, 2026))
        self.assertEqual(stable.required_year_majority, math.ceil(2 * 3 / 3))
        self.assertEqual(stable.scenario_reach_counts, (3, 2, 0))

    def test_only_latest_three_of_four_comparable_years_enter_the_vote(self):
        expanded = rows() + [row("稳妥大学", 2023, 50000)]
        result = recommend_schools(
            expanded, profile(), policy(), rank_scenario=scenario()
        )
        stable = next(item for item in result.items if item.school_name == "稳妥大学")
        self.assertEqual(stable.supporting_years, (2024, 2025, 2026))
        self.assertEqual(stable.strategy, "稳")

    def test_context_and_group_identity_never_mix(self):
        mixed = rows() + [
            row("稳妥大学", 2026, 50000, province="湖南"),
            row("稳妥大学", 2026, 50000, subject_group="物理"),
            row("稳妥大学", 2026, 50000, major_group_code="G02"),
        ]
        result = recommend_schools(
            mixed, profile(), policy(), rank_scenario=scenario()
        )
        stable = next(item for item in result.items if item.school_name == "稳妥大学")
        self.assertEqual(stable.scenario_reach_counts, (3, 2, 0))

    def test_reference_rows_remain_reference_and_one_year_lowers_confidence(self):
        one_year = [row("单年大学", 2026, 25000)]
        result = recommend_schools(
            one_year, profile(), policy(), rank_scenario=scenario()
        )
        item = result.items[0]
        self.assertEqual(item.evidence_status, EvidenceStatus.REFERENCE)
        self.assertEqual(item.strategy, "稳")
        self.assertEqual(item.scenario_confidence, "low")
        self.assertIn("仅覆盖 2026", result.warnings)

    def test_official_singleton_coverage_cannot_support_a_wider_rank_scenario(self):
        singleton = [
            row(
                "单点覆盖大学",
                2026,
                22000,
                evidence_status="official",
                coverage_status="official",
                coverage_min_rank=22000,
                coverage_max_rank=22000,
                source_ids=("official-singleton-2026",),
            )
        ]

        result = recommend_schools(
            singleton,
            profile(),
            policy(),
            rank_scenario=scenario(),
        )

        self.assertEqual(result.items, ())
        self.assertEqual(result.usable_years, ())
        self.assertEqual(result.verified_rank_coverage, (22000, 22000))
        self.assertEqual(result.rank_bounds, (18000, 22000, 27000))

    def test_partial_scenario_rows_remain_observations_within_declared_interval(self):
        partial_rows = [
            row(
                "部分线索大学",
                year,
                min_rank,
                coverage_status="partial",
                source_ids=(f"partial-school-{year}",),
            )
            for year, min_rank in zip((2024, 2025, 2026), (24000, 23000, 22000))
        ]
        result = recommend_schools(
            partial_rows,
            profile(),
            policy(),
            rank_scenario=scenario(),
        )
        self.assertEqual(result.items, ())
        self.assertEqual(result.empty_reason, "partial_observations_only")
        self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)
        self.assertEqual(result.usable_years, ())
        self.assertIsNone(result.verified_rank_coverage)
        self.assertEqual(
            {(item.school_name, item.data_year) for item in result.observations},
            {("部分线索大学", year) for year in (2024, 2025, 2026)},
        )
        for item in result.observations:
            self.assertEqual(item.evidence_status, EvidenceStatus.PARTIAL)
            self.assertEqual(item.source_ids, (f"partial-school-{item.data_year}",))
            self.assertFalse(hasattr(item, "min_score"))
            self.assertFalse(hasattr(item, "min_rank"))
            self.assertFalse(hasattr(item, "delta"))
            self.assertFalse(hasattr(item, "strategy"))

    def test_partial_scenario_rows_are_observations_when_either_bound_is_outside(self):
        for coverage_min, coverage_max in ((18001, 50000), (1000, 26999)):
            partial_rows = [
                row(
                    "部分线索大学",
                    year,
                    min_rank,
                    coverage_status="partial",
                    coverage_min_rank=coverage_min,
                    coverage_max_rank=coverage_max,
                    source_ids=(f"partial-school-{year}",),
                )
                for year, min_rank in zip(
                    (2024, 2025, 2026), (24000, 23000, 22000)
                )
            ]
            with self.subTest(
                coverage_min=coverage_min, coverage_max=coverage_max
            ):
                result = recommend_schools(
                    partial_rows,
                    profile(),
                    policy(),
                    rank_scenario=scenario(),
                )
                self.assertEqual(result.items, ())
                self.assertEqual(result.empty_reason, "partial_observations_only")
                self.assertEqual(result.coverage_status, EvidenceStatus.PARTIAL)
                self.assertEqual(result.usable_years, ())
                self.assertIsNone(result.verified_rank_coverage)
                self.assertEqual(
                    {item.school_name for item in result.observations},
                    {"部分线索大学"},
                )
                self.assertTrue(
                    all(not hasattr(item, "min_rank") for item in result.observations)
                )
                self.assertTrue(
                    any("不进入精确冲稳保" in item for item in result.warnings)
                )

    def test_profile_and_rank_scenario_must_be_exactly_bound(self):
        mismatched = RankScenario._create(
            **{
                **scenario().to_dict(),
                "central_rank": 22001,
            }
        )
        with self.assertRaises((TypeError, ValueError, SchoolRecommendError)):
            recommend_schools(
                rows(), profile(), policy(), rank_scenario=mismatched
            )

    def test_legacy_single_rank_api_keeps_existing_policy_classification(self):
        legacy = RecommendationProfile(
            rank=22000,
            target_province="湖北",
            subject_group="历史",
            secondary_subjects=frozenset({"地理", "政治"}),
        )
        result = recommend_schools(rows(), legacy, policy())
        self.assertEqual(result.rank_basis, "official")
        self.assertEqual(result.rank_bounds, (22000, 22000, 22000))

    def test_official_rank_locator_scenario_is_accepted_without_relabeling(self):
        official_profile = RecommendationProfile(
            rank=22000,
            target_province="湖北",
            subject_group="历史",
            secondary_subjects=frozenset({"地理", "政治"}),
            rank_basis="official",
            optimistic_rank=22000,
            conservative_rank=22000,
            rank_confidence="high",
            rank_source_ids=("official-score-table-2026",),
        )
        official_scenario = RankScenario._create(
            status=EvidenceStatus.OFFICIAL,
            basis="official_score_table",
            optimistic_rank=22000,
            central_rank=22000,
            conservative_rank=22000,
            confidence="high",
            source_ids=("official-score-table-2026",),
            contributing_years=(2026,),
            backtest_error=None,
            reasons=("official_score_table",),
            channel_kinds=("official_rank",),
            channel_statuses=("official",),
            rejected_channel_count=0,
        )
        result = recommend_schools(
            rows(), official_profile, policy(), rank_scenario=official_scenario
        )
        self.assertEqual(result.rank_basis, "official")
        self.assertEqual(result.rank_confidence, "high")
        self.assertEqual(result.rank_bounds, (22000, 22000, 22000))

    def test_preferences_sort_within_strategy_and_are_visible_in_reason(self):
        preferred = [
            row(
                "普通大学",
                year,
                rank,
                city_location="襄阳",
                majors_in_group='["历史学"]',
            )
            for year, rank in zip((2024, 2025, 2026), (24000, 23000, 20000))
        ]
        intended = [
            row(
                "意向大学",
                year,
                rank,
                city_location="上海",
                majors_in_group='["计算机科学"]',
            )
            for year, rank in zip((2024, 2025, 2026), (24000, 23000, 20000))
        ]
        personalized = replace(
            profile(),
            target_schools=("意向大学",),
            target_cities=("上海",),
            target_major_categories=("计算机",),
        )
        result = recommend_schools(
            preferred + intended,
            personalized,
            policy(),
            rank_scenario=scenario(),
        )
        self.assertEqual([item.school_name for item in result.items], ["意向大学", "普通大学"])
        reason = result.items[0].match_reason
        self.assertIn("用户意向院校", reason)
        self.assertIn("用户意向城市", reason)
        self.assertIn("专业倾向匹配：计算机", reason)

    def test_latest_plan_shrink_lowers_confidence_but_keeps_judgment(self):
        shrinking = [
            row("缩招大学", 2024, 24000, plan_count=100),
            row("缩招大学", 2025, 23000, plan_count=100),
            row("缩招大学", 2026, 20000, plan_count=70),
        ]
        result = recommend_schools(
            shrinking, profile(), policy(), rank_scenario=scenario()
        )
        item = result.items[0]
        self.assertEqual(item.strategy, "稳")
        self.assertEqual(item.scenario_confidence, "low")
        self.assertIn("招生计划明显缩减", item.match_reason)

    def test_scenario_candidates_still_honor_province_search_window(self):
        distant = [row("远距大学", year, 40000) for year in (2024, 2025, 2026)]
        result = recommend_schools(
            rows() + distant, profile(), policy(), rank_scenario=scenario()
        )
        self.assertNotIn("远距大学", {item.school_name for item in result.items})

    def test_result_replace_revalidates_rank_bounds_and_inferred_sources(self):
        result = recommend_schools(
            rows(), profile(), policy(), rank_scenario=scenario()
        )
        with self.assertRaises((TypeError, ValueError)):
            replace(result, rank_bounds=(27000, 22000, 18000))
        with self.assertRaises((TypeError, ValueError)):
            replace(result, rank_source_ids=())


if __name__ == "__main__":
    unittest.main()
