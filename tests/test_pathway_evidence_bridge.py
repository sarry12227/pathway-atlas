from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace

from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceManifest,
    EvidenceStatus,
)
from scripts.path_recommend import (
    PathwayPolicy,
    PathwayProfile,
    evaluate_pathways,
)
from scripts.validate_evidence import FrozenJsonRecord, ValidatedEvidenceSnapshot

try:
    from scripts.adapters.pathway_bridge import bridge_pathway_policies
except ImportError:
    bridge_pathway_policies = None


def policy_value(policy_id: str, *, data_year: int = 2026, **changes) -> dict:
    sources = changes.pop("policy_source_ids", [f"{policy_id}-source"])
    status = changes.pop("evidence_status", "official")
    value = {
        "policy_id": policy_id,
        "pathway_type": "strong_foundation",
        "title": f"{policy_id} 合成招生政策",
        "institution": "虚构高校",
        "province": "湖北",
        "subject_mode": "3+1+2",
        "valid_year": data_year,
        "eligibility_requirements": ["完成高考报名"],
        "disqualifying_facts": [],
        "professional_options": ["虚构专业"],
        "training_arrangements": "校内培养",
        "transition_rules": "按公开规则考核转段",
        "outcomes": "完成培养后按规则毕业",
        "service_employment_obligations": "无额外服务期",
        "penalty_exit_rules": "可按公开规则退出",
        "fees_and_subsidies": "按公开标准执行",
        "policy_source_ids": list(sources),
        "evidence_status": status,
        "calculation_basis": "仅按公开政策判断资格与准备价值",
        "target_year": 2026,
        "data_year": data_year,
        "fallback_distance": 2026 - data_year,
        "year_basis": "current_year" if data_year == 2026 else "historical_fallback",
        "timeline": ["本学期核对报名资格", "报名前复核当年简章"],
        "preparation_actions": ["整理学业材料", "跟踪官方报名通知"],
    }
    value.update(changes)
    digest_payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    value["projection_hash"] = "sha256:" + hashlib.sha256(digest_payload).hexdigest()
    return value


def fact(value: dict, *, status: str | None = None, source_ids=None, field=None) -> dict:
    return {
        "fact_id": f"fact-{value['policy_id']}",
        "field": field or f"pathway_policy:{value['policy_id']}",
        "value": value,
        "unit": None,
        "status": status or value["evidence_status"],
        "source_ids": list(source_ids or value["policy_source_ids"]),
        "method": "exact_agreement",
        "notes": "合成政策投影",
    }


def snapshot(*facts: dict) -> ValidatedEvidenceSnapshot:
    manifest = EvidenceManifest(
        schema_version="2.0",
        session_id="a" * 32,
        capability_tier=CapabilityTier.STANDARD,
        candidates_filename="candidates.jsonl",
        facts_filename="normalized/facts.jsonl",
        rejected_count=0,
        manifest_hash="sha256:" + "b" * 64,
    )
    return ValidatedEvidenceSnapshot._create(
        manifest,
        CapabilityReport(tier=CapabilityTier.STANDARD),
        ("2026-08-27",),
        tuple(FrozenJsonRecord._from_mapping(item) for item in facts),
        (),
    )


def direct_policy(policy_id: str, **changes) -> PathwayPolicy:
    value = policy_value(policy_id, **changes)
    value.pop("projection_hash")
    return PathwayPolicy(**value)


class PathwayEvidenceBridgeTest(unittest.TestCase):
    def test_official_two_source_corroborated_and_three_source_reference_enter(self):
        self.assertIsNotNone(bridge_pathway_policies)
        official = policy_value("official")
        corroborated = policy_value(
            "corroborated",
            evidence_status="corroborated",
            policy_source_ids=["b-one", "b-two"],
        )
        reference = policy_value(
            "reference",
            evidence_status="reference",
            policy_source_ids=["c-one", "c-two", "c-three"],
        )
        policies = bridge_pathway_policies(
            snapshot(fact(official), fact(corroborated), fact(reference)),
            province="湖北",
            subject_mode="3+1+2",
            target_year=2026,
        )
        self.assertEqual(
            [(item.policy_id, item.evidence_status.value) for item in policies],
            [
                ("corroborated", "corroborated"),
                ("official", "official"),
                ("reference", "reference"),
            ],
        )

    def test_wrong_context_threshold_hash_and_duplicate_projection_fail_closed(self):
        weak = policy_value(
            "weak",
            evidence_status="corroborated",
            policy_source_ids=["only-one"],
        )
        wrong_province = policy_value("province", province="湖南")
        wrong_subject = policy_value("subject", subject_mode="3+3")
        wrong_year = policy_value("year", target_year=2027, fallback_distance=1)
        mismatch = policy_value("mismatch")
        mismatch["title"] = "篡改后未重哈希"
        duplicate_a = policy_value("duplicate-a")
        duplicate_b = dict(duplicate_a)
        records = (
            fact(weak),
            fact(wrong_province),
            fact(wrong_subject),
            fact(wrong_year),
            fact(mismatch),
            fact(duplicate_a),
            fact(duplicate_b),
        )
        policies = bridge_pathway_policies(
            snapshot(*records),
            province="湖北",
            subject_mode="3+1+2",
            target_year=2026,
        )
        self.assertEqual(policies, ())

    def test_historical_policy_is_usable_but_never_current_eligibility(self):
        old = policy_value("old", data_year=2025)
        policies = bridge_pathway_policies(
            snapshot(fact(old)),
            province="湖北",
            subject_mode="3+1+2",
            target_year=2026,
        )
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0].fallback_distance, 1)
        item = evaluate_pathways(
            PathwayProfile(
                rank=None,
                province="湖北",
                subject_mode="3+1+2",
                current_year=2026,
                eligibility_facts=("完成高考报名",),
            ),
            policies,
        ).items[0]
        self.assertEqual(item.qualification_status, "待核验")
        self.assertNotEqual(item.investment_decision, "不建议")
        self.assertIn("历史回退", item.calculation_basis)

    def test_replaced_snapshot_identity_is_rejected(self):
        value = policy_value("identity")
        authenticated = snapshot(fact(value))
        forged = object.__new__(ValidatedEvidenceSnapshot)
        for name in (
            "manifest",
            "capability",
            "retrieval_dates",
            "facts",
            "rejections",
            "manifest_hash",
        ):
            object.__setattr__(forged, name, getattr(authenticated, name))
        object.__setattr__(forged, "manifest_hash", "sha256:" + "c" * 64)
        with self.assertRaises((TypeError, ValueError)):
            bridge_pathway_policies(
                forged,
                province="湖北",
                subject_mode="3+1+2",
                target_year=2026,
            )

    def test_five_investment_decisions_are_separate_from_qualification(self):
        policies = (
            direct_policy("a-main"),
            direct_policy(
                "b-focus",
                evidence_status="reference",
                policy_source_ids=["c-one", "c-two", "c-three"],
            ),
            direct_policy(
                "c-backup",
                evidence_status="corroborated",
                policy_source_ids=["b-one", "b-two"],
                eligibility_requirements=["完成高考报名", "取得竞赛奖项"],
            ),
            direct_policy(
                "d-observe",
                evidence_status="conflict",
                policy_source_ids=["conflict-one", "conflict-two"],
            ),
            direct_policy(
                "e-reject",
                disqualifying_facts=["命中明确禁限条件"],
            ),
        )
        result = evaluate_pathways(
            PathwayProfile(
                rank=None,
                province="湖北",
                subject_mode="3+1+2",
                current_year=2026,
                eligibility_facts=("完成高考报名", "命中明确禁限条件"),
            ),
            policies,
        )
        self.assertEqual(
            tuple(item.investment_decision for item in result.items),
            ("主攻", "重点准备", "备选", "观察", "不建议"),
        )
        self.assertEqual(
            tuple(item.qualification_status for item in result.items),
            ("已满足", "已满足", "部分满足", "待核验", "不适用"),
        )
        focus = result.items[1]
        self.assertEqual(focus.evidence_status, EvidenceStatus.REFERENCE)
        self.assertEqual(focus.investment_decision, "重点准备")

    def test_pathway_taxonomy_contains_all_approved_types(self):
        approved = {
            "strong_foundation",
            "comprehensive_evaluation",
            "national_special",
            "local_special",
            "university_special",
            "public_funded_teacher",
            "excellent_teacher",
            "directed_medical",
            "military",
            "police_judicial_fire",
            "maritime_aviation",
            "hong_kong_macao",
            "sino_foreign",
            "arts_sports",
            "other",
        }
        for pathway_type in approved:
            with self.subTest(pathway_type=pathway_type):
                self.assertEqual(
                    direct_policy(pathway_type, pathway_type=pathway_type).pathway_type,
                    pathway_type,
                )

    def test_policy_and_item_replace_revalidate_year_metadata(self):
        policy = direct_policy("replace")
        with self.assertRaises((TypeError, ValueError)):
            replace(policy, fallback_distance=2)
        item = evaluate_pathways(
            PathwayProfile(
                rank=None,
                province="湖北",
                subject_mode="3+1+2",
                current_year=2026,
                eligibility_facts=("完成高考报名",),
            ),
            (policy,),
        ).items[0]
        with self.assertRaises((TypeError, ValueError)):
            replace(item, investment_decision="随便看看")


if __name__ == "__main__":
    unittest.main()
