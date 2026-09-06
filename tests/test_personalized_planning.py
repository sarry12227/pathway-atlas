from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import generate_report
from scripts.decision_policy import DecisionReason, DecisionPolicySnapshot, risk_tier_caps
from scripts.planning_profile import PlanningProfile
from scripts.rank_locator import locate_rank
from tests.test_planning_profile import reference_payload


class PersonalizedPlanningProfileTest(unittest.TestCase):
    def test_every_v3_field_has_one_declared_decision_dimension(self):
        profile = PlanningProfile.create(reference_payload())
        trace = profile.to_decision_trace()

        self.assertEqual({item.field for item in trace}, set(profile.decision_field_names()))
        self.assertEqual(len({item.field for item in trace}), len(trace))
        self.assertTrue(all(item.use == "decision_input" for item in trace))
        self.assertTrue(all(item.consumers for item in trace))
        self.assertTrue(all(item.reason for item in trace))

    def test_trace_covers_nested_inputs_that_change_decision_dimensions(self):
        profile = PlanningProfile.create(reference_payload())
        trace = {item.field: item for item in profile.to_decision_trace()}

        self.assertEqual(trace["preparation_assets.awards"].consumers, ("pathway_decision",))
        self.assertEqual(trace["constraints.risk_preference"].consumers, ("school_ordering",))
        self.assertEqual(
            trace["constraints.budget_level"].consumers,
            ("school_ordering", "pathway_decision"),
        )
        self.assertEqual(
            trace["priorities.concerns"].consumers,
            ("pathway_decision", "action_plan", "report"),
        )
        self.assertEqual(
            trace["priorities.desired_outcomes"].consumers,
            ("pathway_decision", "action_plan", "report"),
        )
        self.assertEqual(
            trace["pathway_preferences.strong_foundation"].consumers,
            ("pathway_decision",),
        )
        self.assertEqual(
            trace["grade"].consumers,
            ("pathway_decision", "action_plan"),
        )

    def test_every_registered_school_or_pathway_input_has_single_mutation_parity(self):
        from scripts.contracts import EvidenceStatus, OrdinaryBatchPolicy
        from scripts.rank_locator import RankScenario
        from scripts.school_recommend import (
            SchoolRecommendError,
            personalize_school_recommendations,
        )
        from tests.test_path_recommend_generic import (
            evaluate_pathways,
            pathway_policy,
        )

        payload = reference_payload()
        payload.update(
            {
                "gender": "不便回答",
                "province": "湖北",
                "city": "武汉",
                "grade": "高三",
                "subject_group": "物理",
                "secondary_subjects": ["化学", "生物"],
                "preparation_assets": {
                    "subject_strengths": ["物理"],
                    "awards": ["合成竞赛奖项"],
                    "research_experiences": ["合成研究经历"],
                    "activities": ["合成志愿活动"],
                    "english_readiness": "developing",
                    "interview_readiness": "unknown",
                    "physical_readiness": "ready",
                },
                "constraints": {
                    "excluded_regions": ["北京"],
                    "budget_level": "flexible",
                    "institution_types": ["public"],
                    "service_commitment": "consider",
                    "adjustment_preference": "consider",
                    "risk_preference": "balanced",
                    "health_constraints": ["体检不合格"],
                },
                "priorities": {
                    "school_vs_major": "major_first",
                    "target_schools": ["合成高校"],
                    "target_majors": ["物理学"],
                    "target_regions": ["武汉"],
                    "future_plan": "postgraduate",
                    "concerns": ["费用"],
                    "desired_outcomes": ["多元路径决策"],
                },
                "target_school_reasons": ["已确认院校目标"],
                "target_major_reasons": ["已确认专业目标"],
                "pathway_preferences": {
                    key: "unknown"
                    for key in payload["pathway_preferences"]
                },
                "eligibility_facts": ["完成高考报名"],
            }
        )
        baseline = PlanningProfile.create(payload)
        decision_consumers = {
            "school_ordering",
            "pathway_decision",
            "pathway_eligibility",
        }
        registered_fields = tuple(
            item.field
            for item in baseline.to_decision_trace()
            if decision_consumers.intersection(item.consumers)
        )

        def school_row(index, **changes):
            value = {
                "year": 2025,
                "province": "湖北",
                "school_name": f"合成院校{index:02d}",
                "school_code": f"P{index:03d}",
                "subject_group": "物理",
                "major_group_name": "第01组",
                "major_group_code": f"G{index:03d}",
                "min_score": 620,
                "min_rank": 7000,
                "majors_in_group": ("其他专业",),
                "school_level": "普通本科",
                "school_province": "湖北",
                "city_location": "武汉",
                "remarks": "",
                "evidence_status": "official",
                "coverage_status": "official",
                "source_ids": (f"public-row-{index:03d}",),
                "coverage_min_rank": 5000,
                "coverage_max_rank": 12000,
                "institution_type": "public",
                "affordable_for": ("limited", "moderate", "flexible"),
                "adjustment_required": False,
            }
            value.update(changes)
            return value

        rows = [school_row(index) for index in range(1, 8)]
        rows[0].update({"school_name": "合成高校", "city_location": "上海"})
        rows[1].update(
            {"school_name": "合成物理大学", "majors_in_group": ("物理学",), "city_location": "上海"}
        )
        rows.extend(
            (
                school_row(20, school_province="北京", city_location="北京"),
                school_row(21, institution_type="private"),
                school_row(22, affordable_for=("limited",)),
                school_row(23, adjustment_required=True),
                school_row(
                    24,
                    required_secondary_subjects=("化学",),
                    secondary_subject_rule="all",
                ),
            )
        )
        ordinary_policy = OrdinaryBatchPolicy(
            schema_version="1.0",
            policy_id="mutation-parity-policy-v1",
            basis_id="mutation-parity-basis-v1",
            search_delta_min=-8000,
            search_delta_max=6000,
            challenge_delta_lt=-2000,
            stable_delta_le=2000,
            tier_caps={"冲": 3, "稳": 4, "保": 5},
        )
        rank_scenario = RankScenario._create(
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
        pathway_types = (
            "strong_foundation",
            "comprehensive_evaluation",
            "national_special",
            "public_funded_teacher",
            "military",
            "hong_kong_macao",
            "arts_sports",
        )
        policies = [
            pathway_policy(
                policy_id=f"preference-{index}",
                pathway_type=pathway_type,
                institution="其他高校",
                professional_options=("其他专业",),
            )
            for index, pathway_type in enumerate(pathway_types, start=1)
        ]
        policies.extend(
            (
                pathway_policy(
                    policy_id="gender-policy",
                    institution="性别条件高校",
                    eligibility_requirements=("完成高考报名", "仅限男生"),
                ),
                pathway_policy(
                    policy_id="health-policy",
                    institution="体检条件高校",
                    disqualifying_facts=("体检不合格",),
                ),
                pathway_policy(
                    policy_id="subject-strength-policy",
                    institution="学科匹配高校",
                    grade_requirements=(),
                    subject_requirements=(),
                    professional_options=("物理学",),
                ),
                pathway_policy(
                    policy_id="award-readiness-policy",
                    institution="奖项准备高校",
                    award_requirements=("需要竞赛奖项",),
                ),
                pathway_policy(
                    policy_id="research-readiness-policy",
                    institution="研究准备高校",
                    activity_requirements=("需要研究经历",),
                ),
                pathway_policy(
                    policy_id="activity-readiness-policy",
                    institution="活动准备高校",
                    activity_requirements=("需要志愿活动",),
                ),
                pathway_policy(
                    policy_id="english-readiness-policy",
                    institution="英语准备高校",
                    application_materials=("提交英语成绩",),
                ),
                pathway_policy(
                    policy_id="interview-readiness-policy",
                    institution="面试准备高校",
                    preparation_actions=("准备面试",),
                ),
                pathway_policy(
                    policy_id="physical-readiness-policy",
                    institution="体能准备高校",
                    preparation_actions=("准备体能测试",),
                ),
                pathway_policy(
                    policy_id="desired-output-policy",
                    pathway_type="national_special",
                    institution="期望交付高校",
                    professional_options=("其他专业",),
                ),
            )
        )

        def fingerprint(profile):
            # The authenticated ordinary-batch dataset is jurisdiction-bound;
            # a province mutation must therefore replay the same synthetic rows
            # under the mutated admission province rather than feed stale Hubei
            # rows into another province's calculation.
            profile_rows = tuple(
                {**row, "province": profile.province} for row in rows
            )
            school = personalize_school_recommendations(
                profile_rows,
                profile,
                ordinary_policy,
                rank_scenario=rank_scenario,
            )
            pathways = evaluate_pathways(
                profile,
                tuple(policies),
                rank_scenario=rank_scenario,
            )
            trace = {item.field: item for item in profile.to_decision_trace()}
            for decision in school.decisions:
                for reason in decision.reasons:
                    self.assertTrue(
                        all(
                            "school_ordering" in trace[field].consumers
                            for field in reason.input_fields
                        ),
                        (
                            f"{decision.school_name} {reason.code} declares "
                            f"non-school inputs {reason.input_fields}"
                        ),
                    )
            for item in pathways.items:
                for reason in item.decision_reasons:
                    self.assertTrue(
                        all(
                            {"pathway_decision", "pathway_eligibility"}
                            .intersection(trace[field].consumers)
                            for field in reason.input_fields
                        )
                    )
            return json.dumps(
                {"school": school.to_dict(), "pathways": pathways.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        scalar_candidates = (
            "男", "女", "不便回答", "高一", "高二", "高三", "物理", "历史",
            "武汉", "上海", "湖南", "unknown", "limited", "moderate", "flexible",
            "accept", "consider", "reject", "conservative", "balanced", "aggressive",
            "school_first", "major_first", "employment", "postgraduate", "overseas",
            "interested", "not_interested", "not_applicable", "not_ready", "developing", "ready",
        )
        list_candidates = (
            (),
            ("历史", "地理"),
            ("政治", "地理"),
            ("public",),
            ("private",),
            ("上海",),
            ("物理学",),
            ("完成高考报名",),
            ("体检不合格",),
            ("费用",),
            ("多元路径决策",),
            ("合成高校",),
            ("单字段变更",),
        )

        baseline_fingerprint = fingerprint(baseline)
        unchanged = []
        rejected_mutations = {}
        for field in registered_fields:
            parts = field.split(".")
            current = payload
            for part in parts:
                current = current[part]
            candidates = list_candidates if isinstance(current, list) else scalar_candidates
            changed = False
            for candidate in candidates:
                replacement = list(candidate) if isinstance(current, list) else candidate
                if replacement == current:
                    continue
                mutated_payload = json.loads(json.dumps(payload, ensure_ascii=False))
                target = mutated_payload
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = replacement
                try:
                    mutated = PlanningProfile.create(mutated_payload)
                    mutated_fingerprint = fingerprint(mutated)
                except (TypeError, ValueError, SchoolRecommendError) as error:
                    rejected_mutations.setdefault(field, []).append(
                        f"{replacement!r}:{type(error).__name__}:{error}"
                    )
                    continue
                if mutated_fingerprint != baseline_fingerprint:
                    changed = True
                    break
            if not changed:
                unchanged.append(field)
        self.assertEqual(
            unchanged,
            [],
            "consumer fields without decision parity: "
            f"{unchanged}; rejected={rejected_mutations}",
        )

    def test_decision_reasons_use_a_finite_code_and_exact_profile_trace_fields(self):
        profile = PlanningProfile.create(reference_payload())

        reason = DecisionReason.create(
            profile,
            code="SCHOOL_TARGET_MAJOR_MATCH",
            explanation="认证招生专业与已确认目标专业相符",
            input_fields=("priorities.target_majors", "target_major_reasons"),
            source_ids=("admission-a",),
        )

        self.assertEqual(reason.dimension, "interest_fit")
        self.assertEqual(reason.effect, "supports")
        self.assertEqual(
            reason.input_fields,
            ("priorities.target_majors", "target_major_reasons"),
        )
        self.assertTrue(
            set(reason.input_fields)
            <= {item.field for item in profile.to_decision_trace()}
        )
        self.assertEqual(reason.source_ids, ("admission-a",))
        with self.assertRaises(ValueError):
            DecisionReason.create(
                profile,
                code="FREE_FORM_REASON",
                explanation="任意理由不得进入决策",
                input_fields=(),
            )
        with self.assertRaises(ValueError):
            DecisionReason.create(
                profile,
                code="SCHOOL_TARGET_MAJOR_MATCH",
                explanation="不得声明规则未读取的字段",
                input_fields=("constraints.risk_preference",),
            )

    def test_risk_preference_changes_only_versioned_tier_caps(self):
        policy = DecisionPolicySnapshot.load_default()

        self.assertEqual(risk_tier_caps(policy, "unknown"), {"冲": 3, "稳": 4, "保": 5})
        self.assertEqual(risk_tier_caps(policy, "balanced"), {"冲": 3, "稳": 4, "保": 5})
        self.assertEqual(risk_tier_caps(policy, "conservative"), {"冲": 1, "稳": 4, "保": 5})
        self.assertEqual(risk_tier_caps(policy, "aggressive"), {"冲": 5, "稳": 4, "保": 3})

    def test_report_loader_accepts_a_schema_valid_v3_profile_without_migration(self):
        payload = reference_payload()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            profile = generate_report._load_public_profile(path)

        self.assertIsInstance(profile, PlanningProfile)
        self.assertEqual(profile.to_dict()["schema_version"], "3.0")
        self.assertEqual(profile.digest, PlanningProfile.create(payload).digest)

    def test_public_rank_locator_rejects_an_unvalidated_snapshot_mapping(self):
        profile = PlanningProfile.create(reference_payload())

        with self.assertRaises(TypeError):
            locate_rank(profile, research_snapshot={"admission_rows": []})


if __name__ == "__main__":
    unittest.main()
