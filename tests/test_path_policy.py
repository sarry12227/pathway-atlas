# -*- coding: utf-8 -*-
"""Policy-evidence-gated pathway evaluation tests."""

from __future__ import annotations

import os
import json
import math
import re
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, replace


SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

import path_recommend  # noqa: E402
from contracts import EvidenceStatus  # noqa: E402


def structural_schema_errors(schema, value, path="$"):
    """Small independent structural oracle for the policy Schema."""

    errors = []
    allowed_types = schema.get("type")
    if allowed_types is not None:
        allowed_types = [allowed_types] if isinstance(allowed_types, str) else allowed_types

        def matches(kind):
            return {
                "null": value is None,
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                "string": isinstance(value, str),
                "integer": (
                    not isinstance(value, bool)
                    and (
                        isinstance(value, int)
                        or (
                            isinstance(value, float)
                            and math.isfinite(value)
                            and value.is_integer()
                        )
                    )
                ),
            }.get(kind, False)

        if not any(matches(kind) for kind in allowed_types):
            return [f"{path}:type"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}:enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}:minLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}:pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}:minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}:maximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}:minItems")
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                errors.append(f"{path}:uniqueItems")
        for index, item in enumerate(value):
            if "items" in schema:
                errors.extend(structural_schema_errors(
                    schema["items"], item, f"{path}[{index}]"
                ))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}.{required}:required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}:additionalProperties")
        for key, child in properties.items():
            if key in value:
                errors.extend(structural_schema_errors(child, value[key], f"{path}.{key}"))
    return errors


def profile(**changes):
    values = {
        "rank": 1200,
        "province": "演示省",
        "subject_mode": "3+1+2",
        "current_year": 2026,
        "eligibility_facts": ("完成高考报名",),
    }
    values.update(changes)
    return path_recommend.PathwayProfile(**values)


def policy(policy_id="policy-a", pathway_type="strong_foundation", **changes):
    values = {
        "policy_id": policy_id,
        "pathway_type": pathway_type,
        "title": "虚构高校 2026 年招生政策",
        "institution": "虚构高校",
        "province": "演示省",
        "subject_mode": "3+1+2",
        "valid_year": 2026,
        "eligibility_requirements": ("完成高考报名",),
        "disqualifying_facts": (),
        "professional_options": ("虚构专业甲", "虚构专业乙"),
        "training_arrangements": "校内合成培养方案",
        "transition_rules": "按合成政策完成阶段考核后转段",
        "outcomes": "合成毕业与升学出口说明",
        "service_employment_obligations": "明确无服务期或就业义务",
        "penalty_exit_rules": "可按简章规定退出，无额外违约金",
        "fees_and_subsidies": "按普通专业学费标准，无专项补助",
        "policy_source_ids": ("official-policy",),
        "evidence_status": EvidenceStatus.OFFICIAL,
        "calculation_basis": "仅核验报名资格和公开政策约束，不推断录取结果",
    }
    values.update(changes)
    return path_recommend.PathwayPolicy(**values)


def model(**changes):
    values = {
        "model_id": "model-a",
        "province": "演示省",
        "subject_mode": "3+1+2",
        "cohort_years": (2025, 2026),
        "source_ids": ("model-source-a", "model-source-b"),
        "evidence_status": EvidenceStatus.CORROBORATED,
        "method": "documented_rank_delta",
        "pathway_types": ("strong_foundation",),
        "applicability_rank_min": 1,
        "applicability_rank_max": 50000,
        "score_table_rank_min": 1,
        "score_table_rank_max": 100000,
        "rank_delta": -4000,
    }
    values.update(changes)
    return path_recommend.RankAdjustmentModel(**values)


class PathPolicySafetyTest(unittest.TestCase):
    def test_complete_policy_details_are_preserved_and_each_missing_field_is_pending(self):
        complete = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        self.assertEqual(complete.status, "formal")
        self.assertEqual(set(complete.professional_options), {"虚构专业甲", "虚构专业乙"})
        self.assertEqual(complete.training_arrangements, "校内合成培养方案")
        self.assertEqual(complete.transition_rules, "按合成政策完成阶段考核后转段")
        self.assertEqual(complete.outcomes, "合成毕业与升学出口说明")
        self.assertEqual(complete.service_employment_obligations, "明确无服务期或就业义务")
        self.assertEqual(complete.penalty_exit_rules, "可按简章规定退出，无额外违约金")
        self.assertEqual(complete.fees_and_subsidies, "按普通专业学费标准，无专项补助")

        missing_cases = {
            "professional_options": (),
            "training_arrangements": None,
            "transition_rules": None,
            "outcomes": None,
            "service_employment_obligations": None,
            "penalty_exit_rules": None,
            "fees_and_subsidies": None,
        }
        for field, value in missing_cases.items():
            with self.subTest(field=field):
                item = path_recommend.evaluate_pathways(
                    profile(), (policy(**{field: value}),)
                ).items[0]
                self.assertEqual(item.status, "pending_verification")
                self.assertTrue(item.missing_constraints)

    def test_target_result_carries_strict_model_identity_method_and_evidence(self):
        documented = model()
        result = path_recommend.evaluate_pathways(
            profile(), (policy(),), documented
        )

        self.assertEqual(result.model_id, documented.model_id)
        self.assertEqual(result.model_method, documented.method)
        self.assertEqual(result.model_evidence_status, documented.evidence_status)
        with self.assertRaises((TypeError, ValueError)):
            replace(result, model_evidence_status=None)
        with self.assertRaises((TypeError, ValueError)):
            replace(
                result,
                model_evidence_status=EvidenceStatus.REFERENCE,
                model_source_ids=("only-a", "only-b"),
            )
        with self.assertRaises((TypeError, ValueError)):
            path_recommend.PathwayResult(
                items=result.items,
                formal_shortlist=result.formal_shortlist,
                target_rank=result.target_rank,
                transformation=result.transformation,
                model_source_ids=result.model_source_ids,
            )

    def test_no_model_never_creates_an_unexplained_target_rank(self):
        """Catches reintroducing a hidden fixed rank adjustment."""

        profile = path_recommend.PathwayProfile(
            rank=1200,
            province="演示省",
            subject_mode="3+1+2",
            current_year=2026,
            eligibility_facts=(),
        )

        result = path_recommend.evaluate_pathways(profile, (), model=None)

        self.assertIsNone(result.target_rank)
        self.assertIn("未提供有依据的位次模型", result.warnings)

    def test_model_target_is_clamped_to_declared_score_table_domain(self):
        """Catches nonpositive ranks and undeclared rank domains."""

        result = path_recommend.evaluate_pathways(
            profile(), (policy(),), model(rank_delta=-4000)
        )

        self.assertEqual(result.target_rank, 1)
        self.assertIn("1200 + (-4000) = -2800", result.transformation)
        self.assertIn("[1, 100000]", result.transformation)

    def test_pending_policy_does_not_receive_a_model_target(self):
        """Catches presenting a precise target beside an unverified pathway."""

        result = path_recommend.evaluate_pathways(
            profile(), (policy(service_employment_obligations=None),), model()
        )

        self.assertEqual(result.items[0].status, "pending_verification")
        self.assertIsNone(result.items[0].target_rank)

    def test_model_mismatch_never_produces_a_target_rank(self):
        """Catches applying another province or subject-mode model."""

        for invalid_model in (
            model(province="另一省"),
            model(subject_mode="3+3"),
            model(applicability_rank_max=1000),
            model(pathway_types=("comprehensive_evaluation",)),
        ):
            with self.subTest(invalid_model=invalid_model):
                result = path_recommend.evaluate_pathways(
                    profile(), (policy(),), invalid_model
                )
                self.assertIsNone(result.target_rank)
                self.assertTrue(result.warnings)

    def test_unaccepted_or_undersourced_model_fails_closed(self):
        """Catches deriving a number from partial/conflicting or weak evidence."""

        invalid_models = (
            model(evidence_status=EvidenceStatus.PARTIAL),
            model(evidence_status=EvidenceStatus.CONFLICT),
            model(source_ids=("only-one",)),
        )
        for invalid_model in invalid_models:
            with self.subTest(invalid_model=invalid_model):
                result = path_recommend.evaluate_pathways(
                    profile(), (policy(),), invalid_model
                )
                self.assertIsNone(result.target_rank)

    def test_model_cohort_must_include_profile_current_year(self):
        """Catches applying historical or future-only cohorts to the current year."""

        for cohort_years in ((2000, 2001), (2027, 2028)):
            with self.subTest(cohort_years=cohort_years):
                result = path_recommend.evaluate_pathways(
                    profile(), (policy(),), model(cohort_years=cohort_years)
                )
                self.assertIsNone(result.target_rank)
                self.assertIsNone(result.transformation)
                self.assertIn(
                    "用户当前年份不在模型声明的队列年份中", result.warnings
                )

    def test_model_requires_at_least_one_formal_current_exact_policy(self):
        """Catches generating a global target before policy admission finishes."""

        invalid_policies = (
            policy(evidence_status=EvidenceStatus.CONFLICT),
            policy(valid_year=2025),
            policy(service_employment_obligations=None),
            policy(eligibility_requirements=("未回答的资格条件",)),
        )
        for record in invalid_policies:
            with self.subTest(record=record):
                result = path_recommend.evaluate_pathways(
                    profile(), (record,), model()
                )
                self.assertIsNone(result.target_rank)
                self.assertIsNone(result.transformation)
                self.assertEqual(result.formal_shortlist, ())
                self.assertIn(
                    "无满足正式候选条件的政策，位次模型未执行", result.warnings
                )

    def test_model_applies_to_formal_items_but_not_mixed_pending_items(self):
        """Catches one pending policy poisoning or receiving a formal target."""

        formal = policy("formal")
        pending = policy("pending", service_employment_obligations=None)
        result = path_recommend.evaluate_pathways(
            profile(), (pending, formal), model()
        )
        by_id = {item.policy_id: item for item in result.items}

        self.assertEqual(result.target_rank, 1)
        self.assertEqual(result.formal_shortlist, ("formal",))
        self.assertEqual(by_id["formal"].target_rank, 1)
        self.assertIsNone(by_id["pending"].target_rank)
        self.assertEqual(by_id["pending"].status, "pending_verification")


class PathwayPolicyEvaluationTest(unittest.TestCase):
    def test_missing_each_critical_policy_term_is_pending_and_not_formal(self):
        """Catches formal advice when a service, exit, fee, or validity term is unknown."""

        cases = {
            "service_employment_obligations": None,
            "penalty_exit_rules": None,
            "fees_and_subsidies": None,
            "valid_year": None,
        }
        for field, missing_value in cases.items():
            with self.subTest(field=field):
                item = path_recommend.evaluate_pathways(
                    profile(), (policy(**{field: missing_value}),)
                ).items[0]
                self.assertEqual(item.status, "pending_verification")
                self.assertEqual(item.eligibility, "pending_verification")
                self.assertTrue(item.missing_constraints)
                self.assertNotIn(item.policy_id, path_recommend.evaluate_pathways(
                    profile(), (policy(**{field: missing_value}),)
                ).formal_shortlist)

    def test_current_year_policy_and_profile_must_match(self):
        """Catches treating stale or cross-province policies as current advice."""

        stale = path_recommend.evaluate_pathways(
            profile(), (policy(valid_year=2025),)
        ).items[0]
        wrong_province = path_recommend.evaluate_pathways(
            profile(), (policy(province="另一省"),)
        ).items[0]

        self.assertEqual(stale.status, "pending_verification")
        self.assertEqual(wrong_province.status, "excluded")
        self.assertEqual(wrong_province.eligibility, "ineligible")

    def test_policy_evidence_thresholds_are_fail_closed(self):
        """Catches counting one self-media source as corroborated or reference evidence."""

        invalid = (
            policy(evidence_status=EvidenceStatus.CORROBORATED,
                   policy_source_ids=("one-source",)),
            policy(evidence_status=EvidenceStatus.REFERENCE,
                   policy_source_ids=("source-a", "source-b")),
            policy(evidence_status=EvidenceStatus.CONFLICT,
                   policy_source_ids=("source-a", "source-b", "source-c")),
        )
        for record in invalid:
            with self.subTest(record=record):
                item = path_recommend.evaluate_pathways(profile(), (record,)).items[0]
                self.assertEqual(item.status, "pending_verification")

    def test_profile_requirements_and_disqualifiers_control_eligibility(self):
        """Catches recommending before unanswered requirements or despite disqualification."""

        unanswered = policy(eligibility_requirements=("完成高考报名", "通过资格审核"))
        disqualified = policy(disqualifying_facts=("完成高考报名",))

        pending = path_recommend.evaluate_pathways(profile(), (unanswered,)).items[0]
        excluded = path_recommend.evaluate_pathways(profile(), (disqualified,)).items[0]

        self.assertEqual(pending.status, "pending_verification")
        self.assertIn("通过资格审核", pending.missing_constraints)
        self.assertEqual(excluded.status, "excluded")
        self.assertEqual(excluded.eligibility, "ineligible")

    def test_supported_pathway_types_can_be_formally_shortlisted(self):
        """Catches silently omitting a required nationwide pathway family."""

        types = (
            "strong_foundation",
            "comprehensive_evaluation",
            "special_program",
            "public_funded_or_directed",
            "hong_kong_macao",
        )
        policies = tuple(
            policy(f"policy-{index}", kind) for index, kind in enumerate(types)
        )

        result = path_recommend.evaluate_pathways(profile(), policies)

        self.assertEqual(result.formal_shortlist, tuple(
            f"policy-{index}" for index in range(len(types))
        ))
        self.assertEqual({item.pathway_type for item in result.items}, set(types))
        for item in result.items:
            self.assertEqual(item.status, "formal")
            self.assertEqual(item.eligibility, "eligible")
            self.assertEqual(item.policy_source_ids, ("official-policy",))
            self.assertTrue(item.calculation_basis)

    def test_result_never_adds_probability_or_guarantee_claims(self):
        """Catches adding unsupported admission promises to the output contract."""

        payload = path_recommend.evaluate_pathways(profile(), (policy(),)).to_dict()
        rendered = json.dumps(payload, ensure_ascii=False)

        self.assertNotIn("录取概率", rendered)
        self.assertNotIn("保录", rendered)
        self.assertNotIn("收益率", rendered)
        self.assertNotIn("probability", rendered)


class PathwayContractTest(unittest.TestCase):
    def test_contracts_snapshot_mutable_inputs_and_serialize_json_safely(self):
        """Catches shallow freezing and mutable aliases leaking into results."""

        facts = ["完成高考报名"]
        sources = ["official-policy"]
        p = profile(eligibility_facts=facts)
        record = policy(policy_source_ids=sources)
        facts.append("later")
        sources.append("later")

        result = path_recommend.evaluate_pathways(p, [record])
        payload = result.to_dict()
        payload["items"][0]["missing_constraints"].append("mutated")

        self.assertEqual(p.eligibility_facts, ("完成高考报名",))
        self.assertEqual(record.policy_source_ids, ("official-policy",))
        self.assertEqual(result.items[0].missing_constraints, ())
        self.assertEqual(json.loads(json.dumps(result.to_dict())), result.to_dict())
        with self.assertRaises(FrozenInstanceError):
            p.rank = 1

    def test_strict_numeric_string_enum_source_and_extra_field_inputs(self):
        """Catches bool/float/NaN coercion, whitespace IDs, and silent extras."""

        for value in (True, 1200.0, "1200", 0, -1, math.nan):
            with self.subTest(rank=value):
                with self.assertRaises((TypeError, ValueError)):
                    profile(rank=value)
        for value in (True, 2026.0, "2026", 1999):
            with self.subTest(year=value):
                with self.assertRaises((TypeError, ValueError)):
                    profile(current_year=value)
        for source_ids in ((), ("unsafe source",), ("same", "same"), "one-source"):
            with self.subTest(source_ids=source_ids):
                with self.assertRaises((TypeError, ValueError)):
                    policy(policy_source_ids=source_ids)
        with self.assertRaises(ValueError):
            policy(pathway_type="unknown")
        with self.assertRaises(TypeError):
            path_recommend.PathwayProfile(
                rank=1200, province="演示省", subject_mode="3+1+2",
                current_year=2026, eligibility_facts=(), unexpected=True
            )

    def test_blank_text_and_forbidden_promise_basis_are_rejected(self):
        """Catches ambiguous policies and unsupported precise promises."""

        for field in ("title", "institution", "province", "subject_mode",
                      "calculation_basis"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    policy(**{field: "  "})
        for text in ("录取概率 80%", "付费即可保录", "预计收益率 20%"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    policy(calculation_basis=text)

    def test_contract_text_and_ids_reject_surrounding_whitespace(self):
        """Catches runtime normalization that would diverge from the public Schema."""

        for changes in (
            {"policy_id": " policy-a"},
            {"title": " 标题"},
            {"eligibility_requirements": (" 条件",)},
            {"policy_source_ids": (" source-a",)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    policy(**changes)

    def test_model_contract_rejects_incomplete_or_invalid_declarations(self):
        """Catches undocumented cohorts, methods, domains, and unsafe inputs."""

        invalid_changes = (
            {"cohort_years": ()},
            {"source_ids": ()},
            {"method": " "},
            {"pathway_types": ()},
            {"applicability_rank_min": 0},
            {"applicability_rank_max": 100, "applicability_rank_min": 200},
            {"score_table_rank_max": 100, "score_table_rank_min": 200},
            {"rank_delta": True},
            {"rank_delta": -4000.0},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    model(**changes)

    def test_direct_result_construction_is_deeply_frozen_and_strict(self):
        """Catches mutable direct constructors bypassing result invariants."""

        evaluated = path_recommend.evaluate_pathways(profile(), (policy(),))
        mutable_items = list(evaluated.items)
        mutable_shortlist = list(evaluated.formal_shortlist)
        mutable_warnings = list(evaluated.warnings)
        direct = path_recommend.PathwayResult(
            items=mutable_items,
            formal_shortlist=mutable_shortlist,
            warnings=mutable_warnings,
        )
        mutable_items.clear()
        mutable_shortlist.clear()
        mutable_warnings.append("later")

        self.assertEqual(len(direct.items), 1)
        self.assertEqual(direct.formal_shortlist, ("policy-a",))
        self.assertEqual(direct.warnings, ("未提供有依据的位次模型",))
        with self.assertRaises(FrozenInstanceError):
            direct.target_rank = 1

        invalid_results = (
            {"items": "not-items"},
            {"items": (object(),)},
            {"items": evaluated.items, "formal_shortlist": ("unknown",)},
            {"items": evaluated.items, "formal_shortlist": ()},
            {"target_rank": 0, "transformation": "basis"},
            {"target_rank": 1, "transformation": None},
            {"target_rank": None, "transformation": "basis"},
            {"warnings": (1,)},
        )
        for changes in invalid_results:
            values = {
                "items": (), "formal_shortlist": (), "target_rank": None,
                "transformation": None, "warnings": (),
            }
            values.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    path_recommend.PathwayResult(**values)

    def test_direct_item_construction_enforces_status_and_target_invariants(self):
        """Catches inconsistent eligibility or targets on non-formal items."""

        item = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        mutable_missing = []
        mutable_sources = ["official-policy"]
        direct = replace(
            item,
            missing_constraints=mutable_missing,
            policy_source_ids=mutable_sources,
        )
        mutable_missing.append("later")
        mutable_sources.append("later")
        self.assertEqual(direct.missing_constraints, ())
        self.assertEqual(direct.policy_source_ids, ("official-policy",))

        for changes in (
            {"status": "unknown"},
            {"status": "formal", "eligibility": "pending_verification"},
            {"status": "pending_verification", "eligibility": "pending_verification",
             "missing_constraints": (), "target_rank": None},
            {"status": "pending_verification", "eligibility": "pending_verification",
             "missing_constraints": ("待核实",), "target_rank": 1},
            {"target_rank": 0},
            {"policy_source_ids": ()},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    replace(item, **changes)

    def test_direct_formal_item_requires_exact_sufficient_evidence(self):
        """Catches bypassing policy evidence gates through direct result objects."""

        item = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        invalid_evidence = (
            {"evidence_status": EvidenceStatus.CONFLICT},
            {"evidence_status": EvidenceStatus.MASKED},
            {"evidence_status": EvidenceStatus.PARTIAL},
            {"evidence_status": EvidenceStatus.MISSING},
            {"evidence_status": EvidenceStatus.INFERRED},
            {
                "evidence_status": EvidenceStatus.CORROBORATED,
                "policy_source_ids": ("only-one",),
            },
            {
                "evidence_status": EvidenceStatus.REFERENCE,
                "policy_source_ids": ("source-a", "source-b"),
            },
        )
        for changes in invalid_evidence:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(item, **changes)

    def test_promise_gate_covers_normalized_chinese_and_english_variants(self):
        """Catches promise language hidden by case, punctuation, or whitespace."""

        unsafe_texts = (
            "保 录", "保证-录取", "包/录", "确保·录取", "录取 概率",
            "成功-率", "百分比承诺", "预计收 益", "承诺回-报",
            "R.O.I", "Return-On Investment",
            "ADMISSION_GUARANTEE", "success-rate", "proba bility",
        )
        for unsafe in unsafe_texts:
            with self.subTest(unsafe=unsafe):
                with self.assertRaisesRegex(
                    ValueError, "output text contains unsupported promise language"
                ):
                    policy(title=unsafe)
                with self.assertRaisesRegex(
                    ValueError, "output text contains unsupported promise language"
                ):
                    policy(service_employment_obligations=unsafe)

    def test_promise_gate_covers_model_item_and_result_output_text(self):
        """Catches bypassing the policy gate through another output contract."""

        with self.assertRaisesRegex(ValueError, "unsupported model method"):
            model(method="free-form method")

        item = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        for changes in (
            {"title": "保证.录取"},
            {"calculation_basis": "success rate 90%"},
        ):
            with self.subTest(item_changes=changes):
                with self.assertRaisesRegex(
                    ValueError, "output text contains unsupported promise language"
                ):
                    replace(item, **changes)

        with self.assertRaisesRegex(
            ValueError, "output text contains unsupported promise language"
        ):
            path_recommend.PathwayResult(warnings=("admission guarantee",))

        with_target = replace(item, target_rank=1)
        with self.assertRaisesRegex(
            ValueError, "output text contains unsupported promise language"
        ):
            path_recommend.PathwayResult(
                items=(with_target,), formal_shortlist=(with_target.policy_id,),
                target_rank=1, transformation="预计收益 20%", warnings=(),
            )

    def test_context_gate_rejects_exact_admission_and_return_claims(self):
        """Catches word-order, Chinese-rate, and Unicode-percent bypasses."""

        unsafe_claims = (
            "guaranteed admission",
            "admission is guaranteed",
            "admission guarantee",
            "录取几率八成",
            "预计录取80%",
            "录取80\u066a",
            "success rate 80%",
            "investment return 20%",
            "ROI 20%",
            "录取百分之八十",
            "预计录取百分之八十",
            "八成录取把握",
            "录取٨٠٪",
            "历史录取率80%",
            "historical admission rate 20%",
            "录取比例",
            "录取概率",
            "录取几率",
            "录取可能性",
            "录取把握",
            "admission rate",
            "rate admission",
            "admission probability",
            "probability of admission",
            "admission chance",
            "chance of admission",
            "admission likelihood",
            "likelihood of admission",
            "admission 80%",
            "admission is 80%",
            "80% admission chance",
            "ROI20%",
            "R.O.I.20%",
        )
        for claim in unsafe_claims:
            with self.subTest(claim=claim):
                with self.assertRaisesRegex(
                    ValueError, "output text contains unsupported promise language"
                ):
                    policy(calculation_basis=claim)

    def test_context_gate_accepts_documented_policy_percentages(self):
        """Catches reverting to a global percent/return substring ban."""

        legitimate_facts = (
            "高考成绩占85%",
            "综合评价成绩占15%",
            "学费补助20%",
            "tuition subsidy 20%",
            "service penalty is 20% of subsidy",
            "收益补助已明确",
            "回报材料已提交",
            "毕业后回报家乡",
            "录取后学费补助20%",
            "被录取后可享受学费减免百分之二十",
        )
        for fact in legitimate_facts:
            with self.subTest(fact=fact):
                record = policy(calculation_basis=fact)
                self.assertEqual(record.calculation_basis, fact)
                item = path_recommend.evaluate_pathways(
                    profile(), (record,)
                ).items[0]
                self.assertIn(fact, item.calculation_basis)
                result = path_recommend.PathwayResult(warnings=(fact,))
                self.assertEqual(result.warnings, (fact,))
                self.assertEqual(model(province=fact).province, fact)

    def test_context_gate_applies_to_every_output_contract(self):
        """Catches bypassing the shared gate outside policy calculation basis."""

        item = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        for claim in (
            "admission is guaranteed",
            "历史录取率80%",
            "likelihood of admission",
            "ROI20%",
        ):
            constructors = (
                lambda: policy(title=claim),
                lambda: model(province=claim),
                lambda: replace(item, title=claim),
                lambda: path_recommend.PathwayResult(warnings=(claim,)),
            )
            for constructor in constructors:
                with self.subTest(claim=claim, constructor=constructor):
                    with self.assertRaisesRegex(
                        ValueError, "output text contains unsupported promise language"
                    ):
                        constructor()

    def test_private_output_gate_covers_every_visible_text_contract(self):
        """Catches PII, secrets, and paths leaking through non-profile fields."""

        item = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        unsafe_texts = (
            "姓名：张三",
            "Student Name: Alice",
            "wechat: wx-student",
            "微 信：wx-student",
            "手机号 13800138000",
            "手机号 138 0013 8000",
            "联系电话 138-0013-8000",
            "联系电话 138.0013.8000",
            "联系电话 138·0013·8000",
            "联系电话 138\u200b0013\u200b8000",
            "电话 010-12345678",
            "wxid_abc123",
            "就读学校：某中学",
            "current school: Example High",
            "高三（1）班",
            "住址：某路1号",
            "api_key: secret-value",
            "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
            "前缀C:\\Users\\student\\record.txt",
            "\\\\server\\share\\record.txt",
            "/home/student/record.txt",
            "https://example.invalid/private",
            "www.example.com/private",
            "example.com/private",
            "$HOME/private",
            "$USERPROFILE\\private",
            "$APPDATA\\private",
            "$CODEX_HOME/private",
            "%USERPROFILE%\\private\\report.md",
            "..\\private\\report.md",
            ".\\private\\report.md",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "AKIAIOSFODNN7EXAMPLE",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdHVkZW50In0.signature123456",
        )
        for unsafe in unsafe_texts:
            constructors = (
                lambda unsafe=unsafe: policy(training_arrangements=unsafe),
                lambda unsafe=unsafe: policy(professional_options=(unsafe,)),
                lambda unsafe=unsafe: model(province=unsafe),
                lambda unsafe=unsafe: replace(item, calculation_basis=unsafe),
                lambda unsafe=unsafe: replace(item, outcomes=unsafe),
                lambda unsafe=unsafe: path_recommend.PathwayResult(warnings=(unsafe,)),
            )
            for constructor in constructors:
                with self.subTest(unsafe=unsafe, constructor=constructor):
                    with self.assertRaisesRegex(
                        ValueError, "output text contains private or non-public content"
                    ):
                        constructor()

        # Institution fields may carry ordinary university names; only student-school
        # context labels are private.
        self.assertEqual(policy(institution="虚构学校大学").institution, "虚构学校大学")
        for benign in ("Saxophone", "Secretarial", "Token Economy"):
            with self.subTest(benign=benign):
                self.assertEqual(policy(training_arrangements=benign).training_arrangements, benign)
                benign_item = path_recommend.evaluate_pathways(
                    profile(), (policy(outcomes=benign),)
                ).items[0]
                self.assertEqual(benign_item.outcomes, benign)
                self.assertEqual(path_recommend.PathwayResult(warnings=(benign,)).warnings, (benign,))

    def test_marketing_source_ids_are_rejected_and_model_sources_stay_structured(self):
        """Catches marketing slugs and source IDs leaking into prose output."""

        unsafe_source_ids = (
            "admission-is-guaranteed",
            "guarantee-admission",
            "admission-guarantee",
            "guaranteed-admission",
            "return-on-investment",
            "success-rate",
            "probability-80",
            "roi",
            "admissionguarantee",
            "successrate",
            "returnoninvestment",
            "admission--guarantee",
            "return--on--investment",
            "r-o-i",
            "investment-return",
            "investmentreturn",
            "admission-rate",
            "chance-of-admission",
            "likelihood-of-admission",
            "admissionrate",
            "chanceofadmission",
            "likelihoodofadmission",
            "admission-is-guarantee",
            "admissionisguarantee",
            "probabilityadmission",
            "chance-admission",
            "chanceadmission",
            "likelihood-admission",
            "likelihoodadmission",
        )
        base_item = path_recommend.evaluate_pathways(
            profile(), (policy(),), model()
        ).items[0]
        for source_id in unsafe_source_ids:
            with self.subTest(source_id=source_id):
                constructors = (
                    lambda: policy(policy_source_ids=(source_id,)),
                    lambda: model(source_ids=(source_id, "safe-source")),
                    lambda: replace(base_item, policy_source_ids=(source_id,)),
                    lambda: path_recommend.PathwayResult(
                        items=(base_item,),
                        formal_shortlist=(base_item.policy_id,),
                        target_rank=base_item.target_rank,
                        transformation="documented transformation",
                        model_source_ids=(source_id,),
                    ),
                )
                for constructor in constructors:
                    with self.subTest(constructor=constructor):
                        with self.assertRaisesRegex(
                            ValueError,
                            "source ID contains unsupported claim language",
                        ):
                            constructor()

        accepted_source_ids = (
            "android-policy", "heroic-source", "detroit-official", "src-2026"
        )
        for source_id in accepted_source_ids:
            with self.subTest(source_id=source_id):
                self.assertEqual(
                    policy(policy_source_ids=(source_id,)).policy_source_ids,
                    (source_id,),
                )
                self.assertEqual(
                    model(source_ids=(source_id,)).source_ids,
                    (source_id,),
                )
                accepted_item = path_recommend.evaluate_pathways(
                    profile(),
                    (policy(policy_source_ids=(source_id,)),),
                    model(source_ids=(source_id,)),
                ).items[0]
                self.assertEqual(accepted_item.policy_source_ids, (source_id,))
                self.assertEqual(
                    path_recommend.PathwayResult(
                        items=(base_item,),
                        formal_shortlist=(base_item.policy_id,),
                        target_rank=base_item.target_rank,
                        transformation="documented transformation",
                        model_source_ids=(source_id,),
                        model_id="model-a",
                        model_method="documented_rank_delta",
                        model_evidence_status=EvidenceStatus.OFFICIAL,
                    ).model_source_ids,
                    (source_id,),
                )

        documented_model = model()
        result = path_recommend.evaluate_pathways(
            profile(), (policy(),), documented_model
        )
        self.assertEqual(result.model_source_ids, documented_model.source_ids)
        for source_id in documented_model.source_ids:
            self.assertNotIn(source_id, result.transformation)
            self.assertNotIn(source_id, result.items[0].calculation_basis)

    def test_result_target_requires_at_least_one_matching_formal_item(self):
        """Catches a global target with empty or all-pending output."""

        pending = path_recommend.evaluate_pathways(
            profile(), (policy(service_employment_obligations=None),)
        ).items[0]
        for items in ((), (pending,)):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    path_recommend.PathwayResult(
                        items=items,
                        formal_shortlist=(),
                        target_rank=1,
                        transformation="documented transformation",
                        warnings=(),
                    )

        formal = path_recommend.evaluate_pathways(profile(), (policy(),)).items[0]
        with_target = replace(formal, target_rank=1)
        result = path_recommend.PathwayResult(
            items=(with_target,),
            formal_shortlist=(with_target.policy_id,),
            target_rank=1,
            transformation="documented transformation",
            model_source_ids=("documented-model-source",),
            model_id="model-a",
            model_method="documented_rank_delta",
            model_evidence_status=EvidenceStatus.OFFICIAL,
            warnings=(),
        )
        self.assertEqual(result.target_rank, 1)


class PathwayPolicySchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema_path = os.path.join(SKILL_ROOT, "schemas", "pathway-policy.schema.json")
        with open(schema_path, "r", encoding="utf-8") as handle:
            cls.schema = json.load(handle)

    def test_schema_matches_runtime_policy_shape_and_enums(self):
        """Catches Schema/runtime field or enum drift."""

        payload = policy().to_dict()
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), set(payload))
        self.assertEqual(
            set(self.schema["properties"]["pathway_type"]["enum"]),
            {
                "strong_foundation", "comprehensive_evaluation", "national_special",
                "local_special", "university_special", "public_funded_teacher",
                "excellent_teacher", "directed_medical", "military",
                "police_judicial_fire", "maritime_aviation", "hong_kong_macao",
                "sino_foreign", "arts_sports", "other", "special_program",
                "public_funded_or_directed",
            },
        )
        self.assertEqual(
            set(self.schema["properties"]["evidence_status"]["enum"]),
            {status.value for status in EvidenceStatus},
        )
        self.assertEqual(structural_schema_errors(self.schema, payload), [])

    def test_independent_oracle_rejects_structural_negative_cases(self):
        """Catches permissive extras, coercions, unsafe IDs, and enum drift."""

        valid = policy().to_dict()
        invalid = []
        missing = dict(valid)
        missing.pop("policy_id")
        invalid.append(missing)
        invalid.append(dict(valid, unexpected=True))
        invalid.append(dict(valid, valid_year=True))
        invalid.append(dict(valid, valid_year=1999))
        invalid.append(dict(valid, policy_source_ids=[]))
        invalid.append(dict(valid, policy_source_ids=["unsafe source"]))
        invalid.append(dict(valid, policy_source_ids=["same", "same"]))
        invalid.append(dict(valid, pathway_type="unknown"))
        invalid.append(dict(valid, subject_mode="old-mode"))
        invalid.append(dict(valid, title=" "))
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertTrue(structural_schema_errors(self.schema, payload))

        for field in (
            "valid_year", "training_arrangements", "transition_rules", "outcomes",
            "service_employment_obligations",
            "penalty_exit_rules", "fees_and_subsidies",
        ):
            nullable = dict(valid, **{field: None})
            with self.subTest(nullable_field=field):
                self.assertEqual(structural_schema_errors(self.schema, nullable), [])

    def test_schema_declares_cross_field_runtime_validation(self):
        """Catches publishing structure without source-threshold/current-year semantics."""

        semantic = self.schema["x-semantic-validator"]
        self.assertEqual(semantic["policy"], "scripts.path_recommend.PathwayPolicy")
        self.assertEqual(
            semantic["evaluate"], "scripts.path_recommend.evaluate_pathways"
        )
        self.assertEqual(
            set(semantic["checks"]),
            {
                "source_threshold_by_status", "current_year_validity",
                "projection_hash_binding", "province_subject_year_binding",
                "historical_fallback_disclosure",
                "critical_constraint_completeness", "profile_policy_match",
                "promise_language_gate", "source_id_claim_gate",
            },
        )

    def test_schema_semantic_locators_import_in_package_mode(self):
        """Catches publishing dotted semantic locators that cannot be resolved."""

        code = r'''
import importlib
import json
from pathlib import Path
schema = json.loads(Path("schemas/pathway-policy.schema.json").read_text(encoding="utf-8"))
for key in ("policy", "evaluate", "evidence_bridge"):
    module_name, attribute = schema["x-semantic-validator"][key].rsplit(".", 1)
    assert getattr(importlib.import_module(module_name), attribute)
print("package-locators-ok")
'''
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        process = subprocess.run(
            [sys.executable, "-c", code], cwd=SKILL_ROOT,
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "package-locators-ok")

    def test_json_schema_integer_semantics_normalize_finite_integral_float(self):
        """Catches confusing Python int representation with JSON Schema integer."""

        valid = policy().to_dict()
        integral_float = dict(valid, valid_year=2026.0)

        self.assertEqual(
            structural_schema_errors(self.schema, integral_float), []
        )
        record = policy(valid_year=2026.0)
        self.assertEqual(record.valid_year, 2026)
        self.assertIs(type(record.valid_year), int)
        self.assertEqual(record.to_dict()["valid_year"], 2026)
        self.assertIs(type(record.to_dict()["valid_year"]), int)

        for invalid_year in (2026.5, math.nan, math.inf, -math.inf, True, "2026"):
            with self.subTest(invalid_year=invalid_year):
                payload = dict(valid, valid_year=invalid_year)
                self.assertTrue(structural_schema_errors(self.schema, payload))
                with self.assertRaises((TypeError, ValueError)):
                    policy(valid_year=invalid_year)


if __name__ == "__main__":
    unittest.main()
