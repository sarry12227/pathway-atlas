# -*- coding: utf-8 -*-
"""Evidence-gated joy-report rank estimation contract tests."""

from __future__ import annotations

import json
import math
import os
import sys
import unittest
from dataclasses import FrozenInstanceError, replace


SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from contracts import EvidenceStatus  # noqa: E402
from rank_calc import (  # noqa: E402
    RankAnchor,
    RankScope,
    estimate_rank_from_anchors,
)


def anchor(
    anchor_id: str,
    year: int,
    *,
    school_rank: int = 100,
    province_rank: int = 5000,
    school_name: str = "演示中学",
    scope_type: RankScope = RankScope.WHOLE_SCHOOL,
    scope_value: str = "全校",
    source_ids=("source-a",),
    evidence_status: EvidenceStatus = EvidenceStatus.OFFICIAL,
    coverage_status: EvidenceStatus = EvidenceStatus.OFFICIAL,
    school_score=None,
) -> RankAnchor:
    return RankAnchor(
        anchor_id=anchor_id,
        year=year,
        school_name=school_name,
        scope_type=scope_type,
        scope_value=scope_value,
        school_rank=school_rank,
        province_rank=province_rank,
        school_score=school_score,
        source_ids=source_ids,
        evidence_status=evidence_status,
        coverage_status=coverage_status,
        coverage_min_school_rank=1,
        coverage_max_school_rank=1000,
    )


class RankAnchorContractTest(unittest.TestCase):
    def test_anchor_snapshots_mutable_source_ids_and_serializes_json_safely(self):
        source_ids = ["source-b", "source-a"]
        item = anchor("anchor-a", 2025, source_ids=source_ids, school_score=610.5)
        source_ids.append("source-c")

        payload = item.to_dict()
        payload["source_ids"].append("source-c")

        self.assertEqual(item.source_ids, ("source-a", "source-b"))
        self.assertEqual(item.to_dict()["source_ids"], ["source-a", "source-b"])
        self.assertEqual(json.loads(json.dumps(item.to_dict())), item.to_dict())
        with self.assertRaises(FrozenInstanceError):
            item.school_rank = 1

    def test_invalid_rank_types_and_values_are_rejected(self):
        invalid = (0, -1, "100", 100.0, True, math.nan)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    anchor("bad-rank", 2025, school_rank=value)

    def test_masked_or_ocr_rank_text_is_rejected(self):
        for value in ("6**", "OCR:100", "前100名"):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    anchor("masked-rank", 2025, province_rank=value)

    def test_invalid_score_year_and_source_identity_are_rejected(self):
        for value in (0, -1, True, "610", math.nan, math.inf):
            with self.subTest(score=value):
                with self.assertRaises((TypeError, ValueError)):
                    anchor("bad-score", 2025, school_score=value)
        with self.assertRaises(ValueError):
            anchor("bad-year", 1999)
        with self.assertRaises(ValueError):
            anchor("bad-source", 2025, source_ids=("same", "same"))
        with self.assertRaises(ValueError):
            anchor("bad-source", 2025, source_ids=("contains space",))

    def test_declared_coverage_must_be_complete_and_contain_anchor(self):
        data = anchor("covered", 2025).to_dict()
        data["coverage_min_school_rank"] = None
        with self.assertRaises(ValueError):
            RankAnchor(**data)
        data = anchor("outside", 2025).to_dict()
        data["coverage_max_school_rank"] = 50
        with self.assertRaises(ValueError):
            RankAnchor(**data)

    def test_schema_matches_runtime_shape_and_enums(self):
        schema_path = os.path.join(SKILL_ROOT, "schemas", "rank-anchor.schema.json")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
        payload = anchor("schema-anchor", 2025).to_dict()

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(payload))
        self.assertEqual(
            schema["properties"]["scope_type"]["enum"],
            ["whole_school", "class", "subject_group", "named_program"],
        )
        self.assertEqual(
            set(schema["properties"]["evidence_status"]["enum"]),
            {status.value for status in EvidenceStatus},
        )


class RankEvidenceTest(unittest.TestCase):
    def test_estimate_snapshots_direct_constructor_collections(self):
        base = estimate_rank_from_anchors(
            (anchor("a", 2024), anchor("b", 2025)), None, 120
        )
        reasons = ["caller-reason"]
        copied = replace(base, reasons=reasons)
        reasons.append("mutated")

        self.assertEqual(copied.reasons, ("caller-reason",))
        with self.assertRaises(FrozenInstanceError):
            copied.lower_rank = 1

    def test_two_distinct_years_produce_observed_interval(self):
        anchors = (
            anchor("y2024", 2024, school_rank=100, province_rank=5000),
            anchor("y2025", 2025, school_rank=110, province_rank=5200),
        )

        estimate = estimate_rank_from_anchors(anchors, student_score=610, student_rank=120)

        self.assertEqual(estimate.status, EvidenceStatus.INFERRED)
        self.assertEqual((estimate.lower_rank, estimate.upper_rank), (5020, 5210))
        self.assertEqual(estimate.median_rank, 5115)
        self.assertEqual(estimate.method, "school_rank_offset_median_observed_spread")
        self.assertEqual(estimate.confidence, "moderate")
        self.assertEqual(estimate.contributing_anchor_ids, ("y2024", "y2025"))
        self.assertEqual(estimate.contributing_years, (2024, 2025))
        self.assertEqual(estimate.usable_anchor_count, 2)
        self.assertIn("student_score_cross_check_only", estimate.reasons)

    def test_implied_rank_is_clamped_to_one(self):
        anchors = (
            anchor("y2024", 2024, school_rank=1000, province_rank=1),
            anchor("y2025", 2025, school_rank=900, province_rank=5),
        )
        estimate = estimate_rank_from_anchors(anchors, None, 1)
        self.assertEqual((estimate.lower_rank, estimate.upper_rank), (1, 1))

    def test_one_year_one_anchor_is_missing_without_proxy_number(self):
        estimate = estimate_rank_from_anchors((anchor("only", 2025),), 610, 120)

        self.assertEqual(estimate.status, EvidenceStatus.MISSING)
        self.assertIsNone(estimate.lower_rank)
        self.assertIsNone(estimate.upper_rank)
        self.assertIsNone(estimate.median_rank)
        self.assertEqual(estimate.reason_code, "insufficient_comparable_anchors")

    def test_three_independent_same_year_exact_anchors_are_accepted(self):
        anchors = tuple(
            anchor(f"same-{index}", 2025, source_ids=(f"source-{index}",))
            for index in range(3)
        )
        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.INFERRED)
        self.assertEqual((estimate.lower_rank, estimate.upper_rank), (5020, 5020))
        self.assertEqual(estimate.confidence, "corroborated")
        self.assertEqual(estimate.usable_anchor_count, 3)

    def test_same_year_exact_disagreement_is_conflict_without_bounds(self):
        anchors = (
            anchor("a", 2025, province_rank=5000, source_ids=("source-a",)),
            anchor("b", 2025, province_rank=5100, source_ids=("source-b",)),
            anchor("c", 2025, province_rank=5000, source_ids=("source-c",)),
        )
        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.CONFLICT)
        self.assertIsNone(estimate.lower_rank)
        self.assertIsNone(estimate.upper_rank)
        self.assertEqual(estimate.reason_code, "same_year_exact_disagreement")

    def test_mixed_scope_or_school_is_conflict_not_blended(self):
        cases = (
            (
                anchor("whole", 2024),
                anchor("class", 2025, scope_type=RankScope.CLASS, scope_value="高三一班"),
            ),
            (
                anchor("one", 2024),
                anchor("two", 2025, school_name="另一中学"),
            ),
        )
        for anchors in cases:
            with self.subTest(anchors=anchors):
                estimate = estimate_rank_from_anchors(anchors, None, 120)
                self.assertEqual(estimate.status, EvidenceStatus.CONFLICT)
                self.assertIsNone(estimate.lower_rank)
                self.assertEqual(estimate.reason_code, "mixed_comparability_groups")

    def test_duplicate_or_overlapping_sources_do_not_meet_same_year_threshold(self):
        anchors = (
            anchor("a", 2025, source_ids=("shared",)),
            anchor("b", 2025, source_ids=("shared",)),
            anchor("c", 2025, source_ids=("independent",)),
        )
        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.MISSING)
        self.assertEqual(estimate.reason_code, "insufficient_comparable_anchors")

    def test_unaccepted_statuses_and_incomplete_coverage_are_rejected_and_counted(self):
        anchors = (
            anchor("accepted", 2025),
            anchor("inferred", 2024, evidence_status=EvidenceStatus.INFERRED),
            anchor("masked", 2023, coverage_status=EvidenceStatus.MASKED),
            anchor("partial", 2022, coverage_status=EvidenceStatus.PARTIAL),
        )
        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.input_anchor_count, 4)
        self.assertEqual(estimate.usable_anchor_count, 1)
        self.assertEqual(estimate.rejected_anchor_count, 3)
        self.assertIn("unaccepted_evidence_status", estimate.rejection_reasons)
        self.assertIn("incomplete_coverage", estimate.rejection_reasons)
        self.assertEqual(estimate.status, EvidenceStatus.MISSING)

    def test_student_inputs_are_strict_and_score_never_creates_a_mapping(self):
        anchors = (anchor("a", 2024), anchor("b", 2025))
        for value in (0, -1, 120.0, True, "120"):
            with self.subTest(rank=value):
                with self.assertRaises((TypeError, ValueError)):
                    estimate_rank_from_anchors(anchors, 610, value)
        for value in (0, -1, True, "610", math.nan, math.inf):
            with self.subTest(score=value):
                with self.assertRaises((TypeError, ValueError)):
                    estimate_rank_from_anchors(anchors, value, 120)
        with_score = estimate_rank_from_anchors(anchors, 750, 120)
        without_score = estimate_rank_from_anchors(anchors, None, 120)
        self.assertEqual(
            (with_score.lower_rank, with_score.upper_rank),
            (without_score.lower_rank, without_score.upper_rank),
        )

    def test_function_is_deterministic_does_not_mutate_input_and_serializes(self):
        source_ids = ["source-a"]
        inputs = [
            anchor("later", 2025, source_ids=source_ids),
            anchor("earlier", 2024, province_rank=4800, source_ids=("source-b",)),
        ]
        original = list(inputs)

        first = estimate_rank_from_anchors(inputs, 610, 120)
        source_ids.append("changed-after-construction")
        second = estimate_rank_from_anchors(tuple(reversed(inputs)), 610, 120)

        self.assertEqual(inputs, original)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(json.loads(json.dumps(first.to_dict())), first.to_dict())


if __name__ == "__main__":
    unittest.main()
