from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest

from scripts.contracts import EvidenceStatus
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.path_recommend import (
    PathwayFieldEvidenceOrigin,
    PathwayPolicy,
    RankAdjustmentModel,
    evaluate_pathways as _evaluate_pathways,
)
from scripts.planning_profile import PlanningProfile
from scripts.query_plan import build_query_plan, load_province_catalog
from tests.test_planning_profile import reference_payload
from tests.test_school_recommend_generic import exact_rank
from tests.test_pathway_evidence_bridge import (
    POLICY_FIELDS,
    candidate as pathway_candidate,
    policy_table,
)


def full_profile(
    *,
    gender="不便回答",
    grade="高三",
    preparation=None,
    constraints=None,
    priorities=None,
    preferences=None,
    target_school_reasons=None,
    target_major_reasons=None,
    eligibility_facts=None,
):
    payload = reference_payload()
    payload["province"] = "湖北"
    payload["gender"] = gender
    payload["grade"] = grade
    payload["exam_year"] = 2026
    payload["subject_group"] = "物理"
    payload["secondary_subjects"] = ["化学", "生物"]
    payload["rank_observations"] = []
    payload["best_rank"] = None
    payload["usual_rank"] = None
    payload["eligibility_facts"] = (
        ["完成高考报名"] if eligibility_facts is None else eligibility_facts
    )
    payload["preparation_assets"].update(preparation or {})
    payload["constraints"].update(constraints or {})
    payload["priorities"].update(priorities or {})
    payload["pathway_preferences"].update(preferences or {})
    if target_school_reasons is not None:
        payload["target_school_reasons"] = target_school_reasons
    if target_major_reasons is not None:
        payload["target_major_reasons"] = target_major_reasons
    return PlanningProfile.create(payload)


def pathway_policy(
    policy_id="policy-a",
    pathway_type="public_funded_teacher",
    **changes,
):
    values = {
        "policy_id": policy_id,
        "pathway_type": pathway_type,
        "title": "合成公开招生政策",
        "institution": "合成高校",
        "province": "湖北",
        "subject_mode": "3+1+2",
        "valid_year": 2026,
        "eligibility_requirements": ("完成高考报名",),
        "disqualifying_facts": (),
        "professional_options": ("物理学",),
        "training_arrangements": "按公开培养方案执行",
        "transition_rules": "按公开考核规则执行",
        "outcomes": "按公开培养方案毕业",
        "service_employment_obligations": "毕业后履行公开服务约定",
        "penalty_exit_rules": "按公开规则处理退出",
        "fees_and_subsidies": "费用和补助按公开标准执行",
        "policy_source_ids": ("policy-official",),
        "evidence_status": EvidenceStatus.OFFICIAL,
        "calculation_basis": "项目决策规则，仅比较已认证政策与画像",
        "target_year": 2026,
        "data_year": 2026,
        "fallback_distance": 0,
        "year_basis": "current_year",
        "timeline": ("报名前复核当年简章",),
        "preparation_actions": ("整理申请材料",),
        "grade_requirements": ("高三在读",),
        "subject_requirements": ("物理和化学",),
        "award_requirements": ("公开简章未要求奖项",),
        "activity_requirements": ("公开简章未要求特定活动",),
        "application_materials": ("按公开清单提交材料",),
    }
    values.update(changes)
    return PathwayPolicy(**values)


_TARGET_BY_PATHWAY_TYPE = {
    "strong_foundation": "强基计划",
    "comprehensive_evaluation": "综合评价",
    "national_special": "国家专项",
    "public_funded_teacher": "公费师范",
    "military": "军校",
    "hong_kong_macao": "港澳招生",
    "arts_sports": "艺体类",
}


def _authenticated_policy(profile, query_plan, policy: PathwayPolicy) -> PathwayPolicy:
    from scripts.adapters.pathway_bridge import (
        bridge_pathway_policies,
        bridge_pathway_policy_evidence,
    )
    from scripts.adapters.pathway_extraction import extract_pathway_policy

    target = _TARGET_BY_PATHWAY_TYPE[policy.pathway_type]
    data_year = policy.data_year or query_plan.research_year
    task = next(
        item
        for item in query_plan.tasks
        if item.target_name == target and item.year == data_year
    )
    table = policy_table(
        year=data_year,
        province=profile.province,
        subject_mode=profile.subject_mode,
        institution=policy.institution,
        eligibility_requirements=policy.eligibility_requirements,
        grade_requirements=policy.grade_requirements or (),
        subject_requirements=policy.subject_requirements or (),
        award_requirements=policy.award_requirements or (),
        activity_requirements=policy.activity_requirements or (),
        disqualifying_facts=policy.disqualifying_facts,
        professional_options=policy.professional_options,
        training_arrangements=policy.training_arrangements,
        transition_rules=policy.transition_rules,
        outcomes=policy.outcomes,
        service_employment_obligations=policy.service_employment_obligations,
        penalty_exit_rules=policy.penalty_exit_rules,
        fees_and_subsidies=policy.fees_and_subsidies,
        dates_and_deadlines=policy.timeline,
        application_materials=policy.application_materials or (),
        preparation_actions=policy.preparation_actions,
    )
    source = pathway_candidate("policy-official")
    projection = extract_pathway_policy(
        profile=profile,
        plan=query_plan,
        task=task,
        extraction=table,
        field_map={name: name for name in POLICY_FIELDS},
        candidates=(source,),
    )
    return bridge_pathway_policies(
        (bridge_pathway_policy_evidence(projection),),
        province=profile.province,
        subject_mode=profile.subject_mode,
        target_year=query_plan.research_year,
        expected_profile_digest=profile.digest,
        expected_query_plan_digest=projection.query_plan_digest,
    )[0]


def evaluate_pathways(profile, policies, model=None, **kwargs):
    """Exercise full-profile decisions through the authenticated clock seam."""

    active_plan = build_query_plan(
        profile,
        load_province_catalog(),
        DecisionPolicySnapshot.load_default(),
    )
    planned_targets = {task.target_name for task in active_plan.tasks}
    bound = tuple(
        _authenticated_policy(profile, active_plan, policy)
        for policy in policies
        if _TARGET_BY_PATHWAY_TYPE[policy.pathway_type] in planned_targets
    )
    return _evaluate_pathways(
        profile,
        bound,
        model,
        query_plan=active_plan,
        **kwargs,
    )


class FullProfilePathwayDecisionTest(unittest.TestCase):
    def test_missing_policy_stays_as_observation_and_drives_verification_actions(self):
        from scripts.action_plan import build_action_plan
        from scripts.adapters.pathway_bridge import bridge_pathway_observations

        planning = full_profile(
            preferences={
                "strong_foundation": "interested",
                "comprehensive_evaluation": "not_interested",
                "special_program": "not_interested",
                "service_oriented": "not_interested",
                "uniformed_service": "not_interested",
                "cross_border": "not_interested",
                "arts_sports": "not_interested",
            }
        )
        active_plan = build_query_plan(
            planning,
            load_province_catalog(),
            DecisionPolicySnapshot.load_default(),
        )
        observations = bridge_pathway_observations(
            (), profile=planning, plan=active_plan
        )

        result = _evaluate_pathways(
            planning,
            (),
            rank_scenario=exact_rank(),
            query_plan=active_plan,
            observations=observations,
        )

        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item.title, "强基计划")
        self.assertEqual(item.status, "pending_verification")
        self.assertEqual(item.investment_decision, "观察")
        self.assertEqual(item.qualification_status, "待核验")
        self.assertIs(item.evidence_status, EvidenceStatus.MISSING)
        self.assertEqual(item.policy_source_ids, ())
        self.assertIsNone(item.target_rank)
        self.assertIsNone(item.target_year)
        self.assertIsNone(item.data_year)
        self.assertEqual(item.timeline, ())
        self.assertEqual(item.professional_options, ())
        self.assertIsNone(item.training_arrangements)
        self.assertTrue(item.missing_constraints)
        self.assertTrue(all(not record.source_ids for record in item.field_evidence))

        actions = build_action_plan(
            planning,
            rank_scenario=exact_rank(),
            recommendations=(),
            pathways=result.items,
            evidence_status=EvidenceStatus.OFFICIAL,
        )
        by_id = {action.action_id: action for action in actions}
        evidence_id = f"pathway-evidence-review:{item.policy_id}"
        qualification_id = f"qualification-blocker:{item.policy_id}"
        self.assertIn(evidence_id, by_id)
        self.assertIn(qualification_id, by_id)
        self.assertEqual(by_id[evidence_id].source_ids, ())
        self.assertEqual(by_id[qualification_id].depends_on, (evidence_id,))
        self.assertNotIn(f"long-lead-readiness:{item.policy_id}", by_id)

    def test_authenticated_helper_covers_all_seven_profile_pathway_families(self):
        profile = full_profile()
        active_plan = build_query_plan(
            profile,
            load_province_catalog(),
            DecisionPolicySnapshot.load_default(),
        )
        cases = {
            "strong_foundation": "强基计划",
            "comprehensive_evaluation": "综合评价",
            "national_special": "国家专项",
            "public_funded_teacher": "公费师范",
            "military": "军校",
            "hong_kong_macao": "港澳招生",
            "arts_sports": "艺体类",
        }
        self.assertEqual(_TARGET_BY_PATHWAY_TYPE, cases)
        policies = tuple(
            pathway_policy(
                policy_id=f"coverage-{pathway_type.replace('_', '-')}",
                pathway_type=pathway_type,
            )
            for pathway_type in cases
        )

        result = evaluate_pathways(
            profile,
            policies,
            rank_scenario=exact_rank(),
        )
        planned_targets = {task.target_name for task in active_plan.tasks}
        expected_types = {
            pathway_type
            for pathway_type, target in cases.items()
            if target in planned_targets
        }
        self.assertEqual(
            {item.pathway_type for item in result.items}, expected_types
        )

    def test_display_trails_bind_query_year_and_rank_model_contexts(self):
        planning = full_profile(
            grade="高三",
            preparation={
                "subject_strengths": ["物理"],
                "interview_readiness": "ready",
            },
            preferences={"comprehensive_evaluation": "interested"},
            priorities={"target_majors": ["物理学"]},
        )
        adjustment = RankAdjustmentModel(
            model_id="model-field-trail",
            province="湖北",
            subject_mode="3+1+2",
            cohort_years=(2025, 2026),
            source_ids=("rank-model-a", "rank-model-b"),
            evidence_status=EvidenceStatus.CORROBORATED,
            method="documented_rank_delta",
            pathway_types=("comprehensive_evaluation",),
            applicability_rank_min=1,
            applicability_rank_max=50000,
            score_table_rank_min=1,
            score_table_rank_max=100000,
            rank_delta=-100,
        )
        result = evaluate_pathways(
            planning,
            (
                pathway_policy(
                    pathway_type="comprehensive_evaluation",
                    service_employment_obligations="无额外服务期",
                ),
            ),
            adjustment,
            rank_scenario=exact_rank(),
        )
        self.assertIsNotNone(result.target_rank)
        trails = {item.field: item for item in result.items[0].field_evidence}

        self.assertIs(trails["title"].origin, PathwayFieldEvidenceOrigin.QUERY_CONTEXT)
        self.assertTrue(
            any(locator.endswith("/target_name") for locator in trails["title"].locators)
        )
        self.assertNotEqual(
            trails["title"].locators,
            trails["institution"].locators,
        )
        self.assertEqual(
            set(trails["year_basis"].upstream_fields),
            {"data_year", "query_task.target_year", "query_plan.research_year"},
        )
        self.assertTrue(
            any(locator.endswith("/target_year") for locator in trails["year_basis"].locators)
        )
        self.assertTrue(
            set(adjustment.source_ids) <= set(trails["calculation_basis"].source_ids)
        )
        self.assertTrue(
            {
                "rank_model.source_ids",
                "rank_model.evidence_status",
                "rank_model.method",
            }
            <= set(trails["calculation_basis"].upstream_fields)
        )

    def test_full_profile_rejects_untrailed_bound_policy_and_future_plan_clock(self):
        profile = full_profile()
        active_plan = build_query_plan(
            profile,
            load_province_catalog(),
            DecisionPolicySnapshot.load_default(),
        )
        untrailed = replace(
            pathway_policy(),
            target_year=active_plan.research_year,
            profile_digest=profile.digest,
            query_plan_digest="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "authenticated projection"):
            _evaluate_pathways(
                profile,
                (untrailed,),
                rank_scenario=exact_rank(),
                query_plan=active_plan,
            )

        trailed = _authenticated_policy(
            profile,
            active_plan,
            pathway_policy(pathway_type="comprehensive_evaluation"),
        )
        future = replace(active_plan)
        object.__setattr__(future, "research_year", 2099)
        with self.assertRaises(TypeError):
            _evaluate_pathways(
                profile,
                (trailed,),
                rank_scenario=exact_rank(),
                query_plan=future,
            )

    def test_only_policy_relevant_readiness_can_support_or_create_a_gap(self):
        generic_policy = pathway_policy(
            pathway_type="comprehensive_evaluation",
            service_employment_obligations="无额外服务期",
            award_requirements=("公开简章未要求奖项",),
            activity_requirements=("公开简章未要求特定活动",),
            application_materials=("提交申请材料",),
            preparation_actions=("整理申请材料",),
        )
        common = {
            "preparation": {
                "subject_strengths": [],
                "awards": [],
                "research_experiences": [],
                "activities": [],
                "english_readiness": "unknown",
                "interview_readiness": "unknown",
            },
            "constraints": {"budget_level": "flexible"},
            "preferences": {"comprehensive_evaluation": "interested"},
        }
        unrelated_ready = evaluate_pathways(
            full_profile(
                preparation={**common["preparation"], "physical_readiness": "ready"},
                constraints=common["constraints"],
                preferences=common["preferences"],
            ),
            (generic_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        unrelated_not_ready = evaluate_pathways(
            full_profile(
                preparation={
                    **common["preparation"],
                    "physical_readiness": "not_ready",
                },
                constraints=common["constraints"],
                preferences=common["preferences"],
            ),
            (generic_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        self.assertEqual(
            next(reason.code for reason in unrelated_ready.decision_reasons
                 if reason.dimension == "readiness"),
            "PATH_READINESS_UNVERIFIED",
        )
        self.assertEqual(
            next(reason.input_fields for reason in unrelated_ready.decision_reasons
                 if reason.dimension == "readiness"),
            (),
        )
        self.assertEqual(
            unrelated_ready.investment_decision,
            unrelated_not_ready.investment_decision,
        )

        physical_policy = pathway_policy(
            pathway_type="comprehensive_evaluation",
            service_employment_obligations="无额外服务期",
            award_requirements=("公开简章未要求奖项",),
            activity_requirements=("公开简章未要求特定活动",),
            application_materials=("提交体能测试证明",),
            preparation_actions=("准备体能测试",),
        )
        relevant_ready = evaluate_pathways(
            full_profile(
                preparation={**common["preparation"], "physical_readiness": "ready"},
                constraints=common["constraints"],
                preferences=common["preferences"],
            ),
            (physical_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        relevant_gap = evaluate_pathways(
            full_profile(
                preparation={
                    **common["preparation"],
                    "physical_readiness": "not_ready",
                },
                constraints=common["constraints"],
                preferences=common["preferences"],
            ),
            (physical_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        self.assertEqual(
            next(reason.code for reason in relevant_ready.decision_reasons
                 if reason.dimension == "readiness"),
            "PATH_READINESS_READY",
        )
        self.assertEqual(
            next(reason.code for reason in relevant_gap.decision_reasons
                 if reason.dimension == "readiness"),
            "PATH_READINESS_GAP",
        )
        self.assertEqual(
            next(reason.input_fields for reason in relevant_ready.decision_reasons
                 if reason.dimension == "readiness"),
            ("preparation_assets.physical_readiness",),
        )

    def test_subject_alternatives_use_any_and_ambiguous_grammar_is_uncertain(self):
        alternative = evaluate_pathways(
            full_profile(
                constraints={"service_commitment": "accept"},
                preferences={"service_oriented": "interested"},
            ),
            (pathway_policy(subject_requirements=("物理或历史",)),),
            rank_scenario=exact_rank(),
        ).items[0]
        ambiguous = evaluate_pathways(
            full_profile(
                constraints={"service_commitment": "accept"},
                preferences={"service_oriented": "interested"},
            ),
            (pathway_policy(subject_requirements=("物理/历史",)),),
            rank_scenario=exact_rank(),
        ).items[0]
        mixed = evaluate_pathways(
            full_profile(
                constraints={"service_commitment": "accept"},
                preferences={"service_oriented": "interested"},
            ),
            (pathway_policy(subject_requirements=("物理或化学，生物",)),),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertNotEqual(alternative.investment_decision, "不建议")
        self.assertEqual(
            next(reason.code for reason in alternative.decision_reasons
                 if reason.dimension == "academic_fit"),
            "PATH_ACADEMIC_MATCH",
        )
        self.assertNotEqual(ambiguous.investment_decision, "不建议")
        self.assertEqual(
            next(reason.code for reason in ambiguous.decision_reasons
                 if reason.dimension == "academic_fit"),
            "PATH_ACADEMIC_SUBJECT_UNCERTAIN",
        )
        self.assertEqual(
            next(reason.code for reason in mixed.decision_reasons
                 if reason.dimension == "academic_fit"),
            "PATH_ACADEMIC_SUBJECT_UNCERTAIN",
        )
        self.assertNotEqual(mixed.investment_decision, "主攻")

    def test_gender_and_health_change_eligibility_only_for_explicit_policy_requirements(self):
        gender_policy = pathway_policy(
            eligibility_requirements=("完成高考报名", "仅限男生"),
        )
        male = evaluate_pathways(
            full_profile(
                gender="男",
                constraints={"service_commitment": "accept"},
                preferences={"service_oriented": "interested"},
            ),
            (gender_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        female = evaluate_pathways(
            full_profile(
                gender="女",
                constraints={"service_commitment": "accept"},
                preferences={"service_oriented": "interested"},
            ),
            (gender_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        self.assertNotEqual(male.investment_decision, "不建议")
        self.assertEqual(female.investment_decision, "不建议")
        self.assertIn(
            "PATH_ELIGIBILITY_BLOCKED",
            {reason.code for reason in female.decision_reasons},
        )

        health_policy = pathway_policy(disqualifying_facts=("色盲",))
        exact_health = evaluate_pathways(
            full_profile(
                constraints={
                    "health_constraints": ["色盲"],
                    "service_commitment": "accept",
                },
                preferences={"service_oriented": "interested"},
            ),
            (health_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        unrelated_health = evaluate_pathways(
            full_profile(
                constraints={
                    "health_constraints": ["花粉过敏"],
                    "service_commitment": "accept",
                },
                preferences={"service_oriented": "interested"},
            ),
            (health_policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        self.assertEqual(exact_health.investment_decision, "不建议")
        self.assertNotEqual(unrelated_health.investment_decision, "不建议")

        unrestricted = pathway_policy(disqualifying_facts=())
        unrestricted_male = evaluate_pathways(
            full_profile(
                gender="男",
                constraints={
                    "health_constraints": [],
                    "service_commitment": "accept",
                },
                preferences={"service_oriented": "interested"},
            ),
            (unrestricted,),
            rank_scenario=exact_rank(),
        ).items[0]
        unrestricted_female = evaluate_pathways(
            full_profile(
                gender="女",
                constraints={
                    "health_constraints": ["花粉过敏"],
                    "service_commitment": "accept",
                },
                preferences={"service_oriented": "interested"},
            ),
            (unrestricted,),
            rank_scenario=exact_rank(),
        ).items[0]
        self.assertEqual(
            unrestricted_male.investment_decision,
            unrestricted_female.investment_decision,
        )
        self.assertEqual(
            next(reason.code for reason in unrestricted_male.decision_reasons
                 if reason.dimension == "eligibility"),
            next(reason.code for reason in unrestricted_female.decision_reasons
                 if reason.dimension == "eligibility"),
        )

    def test_target_major_reason_changes_strategic_commitment_without_parsing_text(self):
        policy = pathway_policy(
            pathway_type="comprehensive_evaluation",
            service_employment_obligations="无额外服务期",
        )

        def strategic_code(reasons):
            item = evaluate_pathways(
                full_profile(
                    priorities={
                        "target_majors": ["物理学"],
                    },
                    target_major_reasons=reasons,
                    preferences={"comprehensive_evaluation": "interested"},
                ),
                (policy,),
                rank_scenario=exact_rank(),
            ).items[0]
            reason = next(
                value for value in item.decision_reasons
                if value.dimension == "strategic_value"
            )
            return reason.code, reason.explanation

        without = strategic_code([])
        first = strategic_code(["家庭确认目标专业"])
        second = strategic_code(["不解析这段自由文本"])

        self.assertEqual(without[0], "PATH_STRATEGIC_MATCH")
        self.assertEqual(first[0], "PATH_STRATEGIC_COMMITTED")
        self.assertEqual(first, second)

    def test_exact_grade_requirement_changes_same_rank_decision(self):
        inputs = {
            "preparation": {"interview_readiness": "ready"},
            "constraints": {
                "service_commitment": "accept",
                "budget_level": "flexible",
            },
            "priorities": {
                "target_majors": ["物理学"],
                "future_plan": "public_service",
            },
            "preferences": {"service_oriented": "interested"},
        }
        eligible = evaluate_pathways(
            full_profile(grade="高三", **inputs),
            (pathway_policy(application_materials=("参加面试",)),),
            rank_scenario=exact_rank(),
        ).items[0]
        wrong_grade = evaluate_pathways(
            full_profile(grade="高二", **inputs),
            (pathway_policy(application_materials=("参加面试",)),),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertEqual(eligible.investment_decision, "主攻")
        self.assertEqual(wrong_grade.investment_decision, "不建议")
        self.assertIn(
            "PATH_ACADEMIC_GRADE_BLOCKED",
            {reason.code for reason in wrong_grade.decision_reasons},
        )

    def test_pathway_specific_strength_changes_academic_reason_and_investment(self):
        policy = pathway_policy(
            pathway_type="comprehensive_evaluation",
            grade_requirements=(),
            subject_requirements=(),
            service_employment_obligations="无额外服务期",
            application_materials=("参加面试",),
        )
        common = {
            "constraints": {
                "service_commitment": "accept",
                "budget_level": "flexible",
            },
            "priorities": {"target_majors": ["物理学"]},
            "preferences": {"comprehensive_evaluation": "interested"},
        }
        matched = evaluate_pathways(
            full_profile(
                preparation={
                    "subject_strengths": ["物理"],
                    "interview_readiness": "ready",
                },
                **common,
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        unknown = evaluate_pathways(
            full_profile(
                preparation={
                    "subject_strengths": [],
                    "interview_readiness": "ready",
                },
                **common,
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertEqual(
            next(reason.code for reason in matched.decision_reasons
                 if reason.dimension == "academic_fit"),
            "PATH_ACADEMIC_MATCH",
        )
        self.assertEqual(
            next(reason.code for reason in unknown.decision_reasons
                 if reason.dimension == "academic_fit"),
            "PATH_ACADEMIC_UNVERIFIED",
        )
        self.assertNotEqual(matched.investment_decision, unknown.investment_decision)

    def test_explicit_family_effort_concern_changes_same_rank_burden_decision(self):
        policy = pathway_policy(
            pathway_type="comprehensive_evaluation",
            service_employment_obligations="无额外服务期",
            application_materials=("参加面试",),
        )
        common = {
            "preparation": {"interview_readiness": "ready"},
            "constraints": {
                "service_commitment": "accept",
                "budget_level": "flexible",
            },
            "preferences": {"comprehensive_evaluation": "interested"},
        }
        no_concern = evaluate_pathways(
            full_profile(
                priorities={"target_majors": ["物理学"], "concerns": []},
                **common,
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        effort_concern = evaluate_pathways(
            full_profile(
                priorities={
                    "target_majors": ["物理学"],
                    "concerns": ["申请材料投入"],
                },
                **common,
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertEqual(no_concern.investment_decision, "主攻")
        self.assertEqual(effort_concern.investment_decision, "重点准备")
        self.assertIn(
            "PATH_BURDEN_UNVERIFIED",
            {reason.code for reason in effort_concern.decision_reasons},
        )

    def test_same_rank_service_commitment_changes_decision_and_reason_codes(self):
        policy = pathway_policy(application_materials=("参加面试",))
        accepts = evaluate_pathways(
            full_profile(
                preparation={"interview_readiness": "ready"},
                constraints={"service_commitment": "accept", "budget_level": "flexible"},
                priorities={
                    "target_majors": ["物理学"],
                    "future_plan": "public_service",
                    "desired_outcomes": ["多元路径决策"],
                },
                preferences={"service_oriented": "interested"},
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        reject_profile = full_profile(
            constraints={"service_commitment": "reject"},
            preferences={"service_oriented": "interested"},
        )

        self.assertEqual(accepts.investment_decision, "主攻")
        self.assertIn("PATH_SERVICE_ACCEPTED", {item.code for item in accepts.decision_reasons})
        reject_plan = build_query_plan(
            reject_profile,
            load_province_catalog(),
            DecisionPolicySnapshot.load_default(),
        )
        self.assertFalse(
            any(task.target_name == "公费师范" for task in reject_plan.tasks)
        )
        rejected_trace = next(
            item for item in reject_plan.pathway_trace
            if item.pathway_id == "service_oriented"
        )
        self.assertEqual(rejected_trace.decision, "exclude")
        self.assertEqual(rejected_trace.reason_code, "service_commitment_rejected")

    def test_awards_activities_and_readiness_change_investment_without_changing_rank(self):
        policy = pathway_policy(
            pathway_type="comprehensive_evaluation",
            award_requirements=("需要可核实奖项材料",),
            activity_requirements=("需要可核实研究活动",),
            service_employment_obligations="无额外服务期",
        )
        ready = evaluate_pathways(
            full_profile(
                preparation={
                    "awards": ["合成竞赛奖项"],
                    "research_experiences": ["合成研究项目"],
                    "activities": ["合成研究活动"],
                    "interview_readiness": "ready",
                },
                preferences={"comprehensive_evaluation": "interested"},
                priorities={"target_majors": ["物理学"]},
                constraints={"service_commitment": "accept"},
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]
        unready = evaluate_pathways(
            full_profile(
                preparation={
                    "awards": [],
                    "research_experiences": [],
                    "activities": [],
                    "interview_readiness": "not_ready",
                },
                preferences={"comprehensive_evaluation": "interested"},
            ),
            (policy,),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertIn(ready.investment_decision, {"主攻", "重点准备"})
        self.assertEqual(unready.investment_decision, "备选")
        self.assertNotEqual(
            {item.code for item in ready.decision_reasons},
            {item.code for item in unready.decision_reasons},
        )

    def test_every_pathway_emits_all_eight_dimensions_bound_to_profile_trace(self):
        profile = full_profile(
            constraints={"budget_level": "limited", "service_commitment": "consider"},
            priorities={
                "concerns": ["费用和准备投入"],
                "desired_outcomes": ["多元路径决策"],
                "future_plan": "postgraduate",
            },
            preferences={"service_oriented": "unknown"},
        )
        item = evaluate_pathways(
            profile,
            (pathway_policy(),),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertEqual(
            tuple(reason.dimension for reason in item.decision_reasons),
            (
                "eligibility",
                "academic_fit",
                "interest_fit",
                "readiness",
                "urgency",
                "burden",
                "strategic_value",
                "evidence_quality",
            ),
        )
        trace_fields = {value.field for value in profile.to_decision_trace()}
        self.assertTrue(
            all(set(reason.input_fields) <= trace_fields
                for reason in item.decision_reasons)
        )
        self.assertEqual(
            next(
                reason.input_fields
                for reason in item.decision_reasons
                if reason.dimension == "evidence_quality"
            ),
            (),
        )
        self.assertIn(
            "PATH_AFFORDABILITY_UNVERIFIED",
            {reason.code for reason in item.decision_reasons},
        )

    def test_official_is_not_automatically_main_and_history_is_not_automatically_prepare(self):
        unknown_fit = evaluate_pathways(
            full_profile(
                preparation={
                    "subject_strengths": [],
                    "awards": [],
                    "research_experiences": [],
                    "activities": [],
                    "interview_readiness": "unknown",
                },
                constraints={"service_commitment": "unknown", "budget_level": "unknown"},
                priorities={"target_majors": [], "future_plan": "unknown"},
                preferences={"service_oriented": "unknown"},
            ),
            (pathway_policy(),),
            rank_scenario=exact_rank(),
        ).items[0]
        historical_unknown = evaluate_pathways(
            full_profile(
                preparation={
                    "subject_strengths": [],
                    "awards": [],
                    "research_experiences": [],
                    "activities": [],
                    "interview_readiness": "unknown",
                },
                constraints={"service_commitment": "unknown", "budget_level": "unknown"},
                priorities={"target_majors": [], "future_plan": "unknown"},
                preferences={"service_oriented": "unknown"},
            ),
            (
                pathway_policy(
                    valid_year=2025,
                    target_year=2026,
                    data_year=2025,
                    fallback_distance=1,
                    year_basis="historical_fallback",
                ),
            ),
            rank_scenario=exact_rank(),
        ).items[0]

        self.assertNotEqual(unknown_fit.investment_decision, "主攻")
        self.assertNotEqual(historical_unknown.investment_decision, "重点准备")
        self.assertIn(
            "PATH_EVIDENCE_HISTORICAL",
            {reason.code for reason in historical_unknown.decision_reasons},
        )


if __name__ == "__main__":
    unittest.main()
