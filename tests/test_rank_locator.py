from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest

from scripts.contracts import EvidenceStatus
from scripts.planning_profile import PlanningMode, PlanningProfile, load_planning_profile
from scripts.rank_calc import RankAnchor, RankScope
from scripts.rank_locator import RankScenario, _locate_rank_legacy
from scripts.validate_data import ValidatedScoreRow


ROOT = Path(__file__).resolve().parents[1]


def profile_payload(*, official: bool = False, multiple: bool = False) -> dict:
    observations = [
        {
            "exam_date": "2026-06-01",
            "scope": "province_official" if official else "school",
            "score": 610,
            "max_score": 750,
            "rank": 120 if not official else 18000,
            "cohort_size": 1000 if not official else 200000,
        }
    ]
    if multiple:
        observations.append(
            {
                "exam_date": "2026-04-01",
                "scope": "school",
                "score": 590,
                "max_score": 750,
                "rank": 240,
                "cohort_size": 1000,
            }
        )
    return {
        "schema_version": "2.0",
        "gender": "不便回答",
        "province": "湖北",
        "city": "武汉",
        "high_school": "武汉市示例中学",
        "grade": "高二" if not official else "高三",
        "exam_year": 2028 if not official else 2026,
        "class_level": "重点班",
        "subject_mode": "3+1+2",
        "subject_group": "历史",
        "secondary_subjects": ["地理", "政治"],
        "score_basis": "原始分",
        "rank_observations": observations,
        "best_rank": 80,
        "usual_rank": 140,
        "awards": [],
        "activities": [],
        "target_schools": [],
        "target_school_reasons": [],
        "target_majors": ["历史学"],
        "target_major_reasons": ["长期兴趣"],
        "target_regions": ["武汉"],
        "excluded_regions": [],
        "future_plan": "继续深造",
        "concerns": ["院校定位"],
        "desired_outcomes": ["院校范围", "多元路径"],
        "eligibility_facts": ["接受异地就读"],
    }


def score_rows(*, subject_group: str = "历史") -> tuple[ValidatedScoreRow, ...]:
    rows = []
    for year, cohort in ((2026, 200000), (2025, 190000), (2024, 180000)):
        rows.extend(
            (
                ValidatedScoreRow.from_mapping(
                    {
                        "year": year,
                        "score": 610,
                        "rank": 18000 + (2026 - year) * 1000,
                        "cumulative_count": 18000 + (2026 - year) * 1000,
                        "subject_group": subject_group,
                    }
                ),
                ValidatedScoreRow.from_mapping(
                    {
                        "year": year,
                        "score": 100,
                        "rank": cohort,
                        "cumulative_count": cohort,
                        "subject_group": subject_group,
                    }
                ),
            )
        )
    return tuple(rows)


def channel_fact(
    profile: PlanningProfile,
    channel_id: str,
    *,
    kind: str,
    year: int,
    lower: float,
    central: float,
    upper: float,
    backtest_error: float | None,
    coverage: float = 1.0,
    comparability: float = 1.0,
    province: str = "湖北",
    subject_group: str = "历史",
    high_school: str | None = "武汉市示例中学",
    class_level: str | None = "重点班",
    source_ids: tuple[str, ...] = ("src-a", "src-b", "src-c"),
    status: str = "reference",
) -> dict:
    return {
        "field": f"rank_channel:{channel_id}",
        "value": {
            "schema_version": "1.0",
            "channel_id": channel_id,
            "kind": kind,
            "profile_digest": profile.digest,
            "province": province,
            "subject_group": subject_group,
            "high_school": high_school,
            "class_level": class_level,
            "year": year,
            "lower_percentile": lower,
            "central_percentile": central,
            "upper_percentile": upper,
            "coverage": coverage,
            "comparability": comparability,
            "backtest_error": backtest_error,
        },
        "status": status,
        "source_ids": list(source_ids),
    }


def school_anchor(anchor_id: str, year: int, school_rank: int, province_rank: int):
    return RankAnchor(
        anchor_id=anchor_id,
        year=year,
        school_name="武汉市示例中学",
        scope_type=RankScope.NAMED_PROGRAM,
        scope_value="重点班",
        school_rank=school_rank,
        province_rank=province_rank,
        school_score=None,
        source_ids=(f"src-{anchor_id}",),
        evidence_status=EvidenceStatus.OFFICIAL,
        coverage_status=EvidenceStatus.OFFICIAL,
        coverage_min_school_rank=1,
        coverage_max_school_rank=1000,
    )


def anchor_fact(profile: PlanningProfile, anchor: RankAnchor, **overrides) -> dict:
    value = {
        "schema_version": "1.0",
        "profile_digest": profile.digest,
        "province": profile.province,
        "subject_group": profile.subject_group,
        "class_level": profile.class_level,
        **anchor.to_dict(),
    }
    value.update(overrides)
    return {
        "field": f"rank_anchor:{anchor.anchor_id}",
        "value": value,
        "status": anchor.evidence_status.value,
        "source_ids": list(anchor.source_ids),
    }


class RankLocatorTest(unittest.TestCase):
    def test_user_reported_province_rank_is_an_inferred_profile_interval(self):
        profile = load_planning_profile(profile_payload(official=True))
        scenario = _locate_rank_legacy(profile, evidence_facts=(), score_rows=score_rows())
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.basis, "profile_reported_province_rank")
        self.assertEqual(scenario.source_ids, ("profile-reported-rank",))
        self.assertLess(scenario.optimistic_rank, scenario.central_rank)
        self.assertLess(scenario.central_rank, scenario.conservative_rank)
        self.assertEqual(scenario.central_rank, 18000)
        self.assertEqual(scenario.confidence, "low")
        self.assertIn("profile_source_user_reported", scenario.reasons)

    def test_explicit_joint_exam_ranks_form_scope_weighted_numeric_scenarios(self):
        official_payload = profile_payload(official=True)
        official_payload["rank_observations"][0].update(
            {
                "rank": 18200,
                "cohort_size": 210000,
                "source": "official_score",
            }
        )
        official = _locate_rank_legacy(
            load_planning_profile(official_payload),
            evidence_facts=(),
            score_rows=(),
        )

        scenarios = {}
        for scope in ("province_joint", "city_joint"):
            payload = profile_payload()
            joint_rank, joint_cohort = (
                (18200, 210000)
                if scope == "province_joint"
                else (1820, 21000)
            )
            payload["rank_observations"].append(
                {
                    "exam_date": "2026-06-01",
                    "scope": scope,
                    "score": 610,
                    "max_score": 750,
                    "rank": joint_rank,
                    "cohort_size": joint_cohort,
                    "source": "joint_exam_report",
                }
            )
            with self.subTest(scope=scope):
                scenario = _locate_rank_legacy(
                    load_planning_profile(payload),
                    evidence_facts=(),
                    score_rows=score_rows(),
                )
                self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
                self.assertEqual(scenario.basis, "profile_reported_province_rank")
                self.assertEqual(scenario.central_rank, 17334)
                self.assertEqual(scenario.source_ids, ("profile-reported-rank",))
                self.assertIn("profile_source_joint_exam_report", scenario.reasons)
                self.assertIn(f"profile_scope_{scope}", scenario.reasons)
                scenarios[scope] = scenario

        def width(item):
            return item.conservative_rank - item.optimistic_rank

        self.assertLess(width(official), width(scenarios["province_joint"]))
        self.assertLess(
            width(scenarios["province_joint"]),
            width(scenarios["city_joint"]),
        )
        self.assertEqual(scenarios["province_joint"].confidence, "medium")
        self.assertEqual(scenarios["city_joint"].confidence, "low")

    def test_profile_reported_score_uses_latest_table_but_remains_inferred(self):
        payload = profile_payload(official=True)
        payload["rank_observations"][0]["rank"] = None
        payload["exam_year"] = 2028
        payload["grade"] = "高二"
        payload["score_basis"] = "赋分"
        payload["rank_observations"][0]["source"] = "joint_exam_report"
        profile = load_planning_profile(payload)
        scenario = _locate_rank_legacy(profile, evidence_facts=(), score_rows=score_rows())
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.basis, "profile_reported_score_table")
        self.assertEqual(scenario.central_rank, 18000)
        self.assertEqual(scenario.contributing_years, (2026,))
        self.assertIn("profile-reported-score", scenario.source_ids)
        self.assertIn("year_fallback:0", scenario.reasons)

    def test_score_basis_changes_profile_score_mapping_uncertainty(self):
        assigned = profile_payload(official=True)
        assigned["rank_observations"][0].update(
            {"rank": None, "source": "joint_exam_report"}
        )
        assigned["score_basis"] = "赋分"
        uncertain = json.loads(json.dumps(assigned, ensure_ascii=False))
        uncertain["score_basis"] = "不确定"

        assigned_scenario = _locate_rank_legacy(
            load_planning_profile(assigned), evidence_facts=(), score_rows=score_rows()
        )
        uncertain_scenario = _locate_rank_legacy(
            load_planning_profile(uncertain), evidence_facts=(), score_rows=score_rows()
        )

        assigned_width = (
            assigned_scenario.conservative_rank - assigned_scenario.optimistic_rank
        )
        uncertain_width = (
            uncertain_scenario.conservative_rank - uncertain_scenario.optimistic_rank
        )
        self.assertLess(assigned_width, uncertain_width)
        self.assertIn("profile_score_basis_assigned", assigned_scenario.reasons)
        self.assertIn("profile_score_basis_uncertain", uncertain_scenario.reasons)

    def test_school_anchor_ensemble_produces_ordered_inferred_scenario(self):
        profile = load_planning_profile(profile_payload())
        anchors = (
            school_anchor("a", 2025, 110, 17000),
            school_anchor("b", 2026, 120, 18000),
        )
        scenario = _locate_rank_legacy(
            profile,
            evidence_facts=(),
            score_rows=score_rows(),
            anchors=anchors,
        )
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.basis, "school_anchor_ensemble")
        self.assertLessEqual(scenario.optimistic_rank, scenario.central_rank)
        self.assertLessEqual(scenario.central_rank, scenario.conservative_rank)
        self.assertEqual(scenario.contributing_years, (2025, 2026))
        self.assertIsNotNone(scenario.backtest_error)

    def test_best_and_usual_school_ranks_constrain_anchor_scenarios(self):
        baseline_payload = profile_payload()
        baseline = load_planning_profile(baseline_payload)
        stronger_best_payload = json.loads(
            json.dumps(baseline_payload, ensure_ascii=False)
        )
        stronger_best_payload["best_rank"] = 40
        weaker_usual_payload = json.loads(
            json.dumps(baseline_payload, ensure_ascii=False)
        )
        weaker_usual_payload["usual_rank"] = 200
        anchors = (
            school_anchor("a", 2025, 110, 17000),
            school_anchor("b", 2026, 120, 18000),
        )

        baseline_scenario = _locate_rank_legacy(
            baseline, evidence_facts=(), score_rows=score_rows(), anchors=anchors
        )
        stronger_best = _locate_rank_legacy(
            load_planning_profile(stronger_best_payload),
            evidence_facts=(),
            score_rows=score_rows(),
            anchors=anchors,
        )
        weaker_usual = _locate_rank_legacy(
            load_planning_profile(weaker_usual_payload),
            evidence_facts=(),
            score_rows=score_rows(),
            anchors=anchors,
        )

        self.assertLess(
            stronger_best.optimistic_rank, baseline_scenario.optimistic_rank
        )
        self.assertGreater(weaker_usual.central_rank, baseline_scenario.central_rank)
        self.assertIn("profile_best_rank_bound", stronger_best.reasons)
        self.assertIn("profile_usual_rank_anchor", weaker_usual.reasons)

    def test_observation_source_changes_profile_rank_interval_reliability(self):
        reported_payload = profile_payload(official=True)
        reported_payload["rank_observations"][0]["source"] = "user_reported"
        joint_payload = json.loads(json.dumps(reported_payload, ensure_ascii=False))
        joint_payload["rank_observations"][0]["source"] = "joint_exam_report"

        reported = _locate_rank_legacy(
            load_planning_profile(reported_payload),
            evidence_facts=(),
            score_rows=score_rows(),
        )
        joint = _locate_rank_legacy(
            load_planning_profile(joint_payload),
            evidence_facts=(),
            score_rows=score_rows(),
        )

        reported_width = reported.conservative_rank - reported.optimistic_rank
        joint_width = joint.conservative_rank - joint.optimistic_rank
        self.assertLess(joint_width, reported_width)
        self.assertNotEqual(joint.confidence, reported.confidence)
        self.assertIn("profile_source_joint_exam_report", joint.reasons)

    def test_authenticated_anchor_facts_bind_province_subject_class_and_profile(self):
        profile = load_planning_profile(profile_payload())
        anchors = (
            school_anchor("a", 2025, 110, 17000),
            school_anchor("b", 2026, 120, 18000),
        )
        valid = tuple(anchor_fact(profile, anchor) for anchor in anchors)
        scenario = _locate_rank_legacy(profile, evidence_facts=valid, score_rows=score_rows())
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertEqual(scenario.basis, "school_anchor_ensemble")

        for index, override in enumerate(
            (
                {"province": "湖南"},
                {"subject_group": "物理"},
                {"class_level": "普通班"},
                {"profile_digest": "sha256:" + "0" * 64},
            )
        ):
            with self.subTest(override=override):
                rejected = tuple(
                    anchor_fact(profile, anchor, **override) for anchor in anchors
                )
                missing = _locate_rank_legacy(
                    profile, evidence_facts=rejected, score_rows=score_rows()
                )
                self.assertEqual(missing.status, EvidenceStatus.MISSING)
                self.assertEqual(missing.rejected_channel_count, 2)

    def test_joint_distribution_and_group_channels_use_backtest_weighted_median(self):
        profile = load_planning_profile(profile_payload())
        facts = (
            channel_fact(
                profile,
                "joint",
                kind="joint_exam",
                year=2026,
                lower=0.08,
                central=0.10,
                upper=0.12,
                backtest_error=0.01,
            ),
            channel_fact(
                profile,
                "distribution",
                kind="score_distribution",
                year=2025,
                lower=0.09,
                central=0.11,
                upper=0.13,
                backtest_error=0.015,
            ),
            channel_fact(
                profile,
                "outlier",
                kind="group_prior",
                year=2026,
                lower=0.70,
                central=0.80,
                upper=0.90,
                backtest_error=0.30,
                coverage=0.2,
                comparability=0.5,
            ),
        )
        scenario = _locate_rank_legacy(profile, evidence_facts=facts, score_rows=score_rows())
        self.assertEqual(scenario.status, EvidenceStatus.INFERRED)
        self.assertLess(scenario.central_rank, 40000)
        self.assertEqual(
            set(scenario.channel_kinds),
            {"joint_exam", "score_distribution", "group_prior"},
        )
        self.assertEqual(scenario.channel_statuses, ("reference",))

    def test_untested_channels_are_capped_and_confidence_stays_low(self):
        profile = load_planning_profile(profile_payload())
        facts = (
            channel_fact(
                profile,
                "tested",
                kind="joint_exam",
                year=2026,
                lower=0.09,
                central=0.10,
                upper=0.11,
                backtest_error=0.02,
            ),
            channel_fact(
                profile,
                "untested",
                kind="group_prior",
                year=2026,
                lower=0.75,
                central=0.80,
                upper=0.85,
                backtest_error=None,
            ),
        )
        scenario = _locate_rank_legacy(profile, evidence_facts=facts, score_rows=score_rows())
        self.assertLess(scenario.central_rank, 50000)
        self.assertEqual(scenario.confidence, "low")
        self.assertIn("untested_weight_capped", scenario.reasons)

    def test_wrong_profile_context_is_rejected_before_combination(self):
        profile = load_planning_profile(profile_payload())
        attacks = (
            {"province": "湖南"},
            {"subject_group": "物理"},
            {"high_school": "另一所学校"},
            {"class_level": "普通班"},
        )
        for index, override in enumerate(attacks):
            with self.subTest(override=override):
                fact = channel_fact(
                    profile,
                    f"bad-{index}",
                    kind="group_prior",
                    year=2026,
                    lower=0.1,
                    central=0.12,
                    upper=0.14,
                    backtest_error=0.02,
                    **override,
                )
                scenario = _locate_rank_legacy(
                    profile, evidence_facts=(fact,), score_rows=score_rows()
                )
                self.assertEqual(scenario.status, EvidenceStatus.MISSING)
                self.assertEqual(scenario.rejected_channel_count, 1)

    def test_recent_exam_volatility_widens_but_never_reverses_bounds(self):
        stable = load_planning_profile(profile_payload())
        volatile = load_planning_profile(profile_payload(multiple=True))
        stable_fact = channel_fact(
            stable,
            "stable",
            kind="joint_exam",
            year=2026,
            lower=0.10,
            central=0.12,
            upper=0.14,
            backtest_error=0.02,
        )
        volatile_fact = channel_fact(
            volatile,
            "volatile",
            kind="joint_exam",
            year=2026,
            lower=0.10,
            central=0.12,
            upper=0.14,
            backtest_error=0.02,
        )
        stable_result = _locate_rank_legacy(
            stable, evidence_facts=(stable_fact,), score_rows=score_rows()
        )
        volatile_result = _locate_rank_legacy(
            volatile, evidence_facts=(volatile_fact,), score_rows=score_rows()
        )
        stable_width = stable_result.conservative_rank - stable_result.optimistic_rank
        volatile_width = volatile_result.conservative_rank - volatile_result.optimistic_rank
        self.assertGreater(volatile_width, stable_width)
        self.assertLessEqual(volatile_result.optimistic_rank, volatile_result.central_rank)
        self.assertLessEqual(volatile_result.central_rank, volatile_result.conservative_rank)

    def test_no_calibration_or_cohort_data_returns_explicit_missing(self):
        payload = profile_payload()
        payload["rank_observations"] = []
        payload["best_rank"] = None
        payload["usual_rank"] = None
        profile = load_planning_profile(payload)
        self.assertEqual(profile.mode, PlanningMode.LOW_INFORMATION)
        scenario = _locate_rank_legacy(profile, evidence_facts=(), score_rows=())
        self.assertEqual(scenario.status, EvidenceStatus.MISSING)
        self.assertIsNone(scenario.central_rank)
        self.assertEqual(scenario.confidence, "none")

    def test_result_is_factory_only_frozen_and_deterministic(self):
        profile = load_planning_profile(profile_payload(official=True))
        first = _locate_rank_legacy(profile, evidence_facts=(), score_rows=score_rows())
        second = _locate_rank_legacy(profile, evidence_facts=(), score_rows=score_rows())
        self.assertEqual(first.to_dict(), second.to_dict())
        json.dumps(first.to_dict(), ensure_ascii=False, allow_nan=False)
        with self.assertRaises(TypeError):
            RankScenario()
        with self.assertRaises(TypeError):
            replace(first, central_rank=1)
        with self.assertRaises(FrozenInstanceError):
            first.central_rank = 1

    def test_rank_scenario_schema_is_strict_and_matches_serialization(self):
        schema = json.loads(
            (ROOT / "schemas" / "rank-estimate.schema.json").read_text("utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        profile = load_planning_profile(profile_payload(official=True))
        payload = _locate_rank_legacy(
            profile, evidence_facts=(), score_rows=score_rows()
        ).to_dict()
        self.assertEqual(set(schema["required"]), set(payload))
        self.assertEqual(set(schema["properties"]), set(payload))
        self.assertLessEqual(
            set(payload["channel_statuses"]),
            set(schema["properties"]["channel_statuses"]["items"]["enum"]),
        )
        self.assertEqual(
            set(schema["properties"]["basis"]["enum"]),
            {
                "authenticated_interval",
                "authenticated_interval_intersection",
                "conflicting_authenticated_channels",
                "multi_channel_ensemble",
                "official_province_rank",
                "official_score_table",
                "profile_reported_province_rank",
                "profile_reported_score_table",
                "school_anchor_ensemble",
                "school_anchor_interval",
                "unavailable",
            },
        )


if __name__ == "__main__":
    unittest.main()
