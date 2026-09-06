from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date
import json
from pathlib import Path
import unittest

from scripts.decision_policy import DecisionPolicySnapshot


ROOT = Path(__file__).resolve().parents[1]


class DecisionPolicyTest(unittest.TestCase):
    def test_default_is_versioned_project_rule_with_explicit_basis(self) -> None:
        policy = DecisionPolicySnapshot.load_default()

        self.assertEqual(policy.schema_version, "1.0")
        self.assertEqual(policy.policy_kind, "project_planning_rule")
        self.assertEqual(date.fromisoformat(policy.reviewed_at).isoformat(), policy.reviewed_at)
        self.assertRegex(policy.policy_id, r"^[A-Za-z0-9][A-Za-z0-9._:-]+$")
        self.assertRegex(policy.basis.basis_id, r"^[A-Za-z0-9][A-Za-z0-9._:-]+$")
        self.assertRegex(policy.basis.source_id, r"^[A-Za-z0-9][A-Za-z0-9._:-]+$")
        self.assertRegex(policy.basis.source_version, r"^[0-9]+\.[0-9]+$")
        self.assertEqual(policy.source_policy.policy_id, "pathway-atlas-source-policy")
        self.assertEqual(policy.source_policy.version, "1.0")
        self.assertEqual(set(policy.scenario.tier_caps), {"冲", "稳", "保"})
        self.assertTrue(all(value > 0 for value in policy.scenario.tier_caps.values()))
        self.assertGreater(policy.scenario.min_supporting_years_for_medium_confidence, 0)

    def test_policy_is_deeply_immutable_and_factory_only(self) -> None:
        policy = DecisionPolicySnapshot.load_default()
        with self.assertRaises(TypeError):
            DecisionPolicySnapshot()
        with self.assertRaises(TypeError):
            replace(policy, policy_id="forged")
        with self.assertRaises(TypeError):
            policy.scenario.tier_caps["稳"] = 99
        with self.assertRaises(FrozenInstanceError):
            policy.reviewed_at = "2026-08-30"

    def test_schema_has_exact_public_versioned_contract_and_finite_vocabularies(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "decision-policy.schema.json").read_text("utf-8")
        )
        payload = DecisionPolicySnapshot.load_default().to_dict()

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(payload))
        self.assertEqual(schema["properties"]["policy_kind"]["const"], "project_planning_rule")
        self.assertFalse(schema["$defs"]["basis"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["sourcePolicyReference"]["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]["pathway_reason_order"]["items"]["enum"]),
            set(payload["pathway_reason_order"]),
        )
        self.assertEqual(
            set(schema["properties"]["action_priority_order"]["items"]["enum"]),
            set(payload["action_priority_order"]),
        )


if __name__ == "__main__":
    unittest.main()
