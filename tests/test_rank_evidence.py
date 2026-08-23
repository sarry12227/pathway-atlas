# -*- coding: utf-8 -*-
"""Evidence-gated joy-report rank estimation contract tests."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
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


def structural_schema_errors(schema, value, path="$"):
    """Independent small Draft 2020-12 oracle for this dependency-free suite."""

    errors = []
    allowed_types = schema.get("type")
    if allowed_types is not None:
        if isinstance(allowed_types, str):
            allowed_types = [allowed_types]

        def matches_type(kind):
            return {
                "null": value is None,
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ),
                "boolean": isinstance(value, bool),
            }.get(kind, False)

        if not any(matches_type(kind) for kind in allowed_types):
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
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}:exclusiveMinimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}:minItems")
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                errors.append(f"{path}:uniqueItems")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(
                    structural_schema_errors(schema["items"], item, f"{path}[{index}]")
                )
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}.{required}:required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}:additionalProperties")
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(
                    structural_schema_errors(child_schema, value[key], f"{path}.{key}")
                )
    for child in schema.get("allOf", []):
        errors.extend(structural_schema_errors(child, value, path))
    if "if" in schema:
        condition_matches = not structural_schema_errors(schema["if"], value, path)
        branch = schema.get("then" if condition_matches else "else")
        if branch is not None:
            errors.extend(structural_schema_errors(branch, value, path))
    return errors


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
    def test_schema_dotted_locators_import_and_estimator_performs_no_io(self):
        code = r'''
import importlib
import json
from pathlib import Path
from unittest import mock

root = Path.cwd()
schema = json.loads((root / "schemas" / "rank-anchor.schema.json").read_text(encoding="utf-8"))
resolved = {}
for key in ("anchor", "estimate"):
    module_name, attribute = schema["x-semantic-validator"][key].rsplit(".", 1)
    resolved[key] = getattr(importlib.import_module(module_name), attribute)

from scripts.contracts import EvidenceStatus
from scripts.rank_calc import RankScope

def make(anchor_id, year):
    return resolved["anchor"](
        anchor_id=anchor_id,
        year=year,
        school_name="Synthetic School",
        scope_type=RankScope.WHOLE_SCHOOL,
        scope_value="whole school",
        school_rank=100,
        province_rank=5000,
        school_score=None,
        source_ids=(f"source-{year}",),
        evidence_status=EvidenceStatus.OFFICIAL,
        coverage_status=EvidenceStatus.OFFICIAL,
        coverage_min_school_rank=1,
        coverage_max_school_rank=1000,
    )

with mock.patch("builtins.open", side_effect=AssertionError("estimator attempted I/O")):
    result = resolved["estimate"]((make("a", 2024), make("b", 2025)), None, 120)
assert result.status == EvidenceStatus.INFERRED
print("package-locators-ok")
'''
        process = subprocess.run(
            [sys.executable, "-c", code],
            cwd=SKILL_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "package-locators-ok")

    def test_flat_scripts_path_import_remains_supported_in_isolated_process(self):
        code = r'''
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "scripts"))
from rank_calc import RankAnchor, estimate_rank_from_anchors
assert RankAnchor.__module__ == "rank_calc"
assert estimate_rank_from_anchors.__module__ == "rank_calc"
print("flat-import-ok")
'''
        process = subprocess.run(
            [sys.executable, "-c", code],
            cwd=SKILL_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "flat-import-ok")

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

    def test_accepted_exact_coverage_cannot_omit_both_bounds(self):
        data = anchor("unbounded-exact", 2025).to_dict()
        data["coverage_min_school_rank"] = None
        data["coverage_max_school_rank"] = None

        with self.assertRaises(ValueError):
            RankAnchor(**data)

        data["coverage_status"] = EvidenceStatus.PARTIAL.value
        partial = RankAnchor(**data)
        self.assertIsNone(partial.coverage_min_school_rank)
        self.assertIsNone(partial.coverage_max_school_rank)
        data = anchor("outside", 2025).to_dict()
        data["coverage_max_school_rank"] = 50
        with self.assertRaises(ValueError):
            RankAnchor(**data)
        data = anchor("reversed", 2025).to_dict()
        data["coverage_min_school_rank"] = 200
        data["coverage_max_school_rank"] = 100
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

    def test_independent_schema_oracle_rejects_structural_negative_cases(self):
        schema_path = os.path.join(SKILL_ROOT, "schemas", "rank-anchor.schema.json")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
        valid = anchor("schema-validated", 2025).to_dict()
        self.assertEqual(structural_schema_errors(schema, valid), [])

        invalid_cases = []
        missing = dict(valid)
        missing.pop("year")
        invalid_cases.append(missing)
        extra = dict(valid, unexpected=True)
        invalid_cases.append(extra)
        invalid_cases.append(dict(valid, school_rank=True))
        invalid_cases.append(dict(valid, school_rank="100"))
        invalid_cases.append(dict(valid, school_rank=0))
        invalid_cases.append(dict(valid, school_score=math.nan))
        invalid_cases.append(dict(valid, source_ids=["unsafe source"]))
        invalid_cases.append(
            dict(
                valid,
                coverage_status=EvidenceStatus.OFFICIAL.value,
                coverage_min_school_rank=None,
                coverage_max_school_rank=None,
            )
        )
        invalid_cases.append(dict(valid, coverage_min_school_rank=None))
        for payload in invalid_cases:
            with self.subTest(payload=payload):
                self.assertTrue(structural_schema_errors(schema, payload))

        partial_unbounded = dict(
            valid,
            coverage_status=EvidenceStatus.PARTIAL.value,
            coverage_min_school_rank=None,
            coverage_max_school_rank=None,
        )
        self.assertEqual(structural_schema_errors(schema, partial_unbounded), [])

    def test_schema_declares_required_semantic_validation_layer(self):
        schema_path = os.path.join(SKILL_ROOT, "schemas", "rank-anchor.schema.json")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
        semantic = schema["x-semantic-validator"]

        self.assertEqual(semantic["anchor"], "scripts.rank_calc.RankAnchor")
        self.assertEqual(
            semantic["estimate"], "scripts.rank_calc.estimate_rank_from_anchors"
        )
        self.assertEqual(
            set(semantic["checks"]),
            {"coverage_order", "anchor_containment", "student_rank_coverage"},
        )


class RankEvidenceTest(unittest.TestCase):
    def test_all_anchors_outside_verified_coverage_are_missing_without_numbers(self):
        anchors = (
            replace(anchor("a", 2024), coverage_max_school_rank=100),
            replace(anchor("b", 2025), coverage_max_school_rank=100),
        )

        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.MISSING)
        self.assertEqual(estimate.reason_code, "input_outside_verified_coverage")
        self.assertIsNone(estimate.lower_rank)
        self.assertIsNone(estimate.upper_rank)
        self.assertEqual(estimate.usable_anchor_count, 0)
        self.assertEqual(estimate.rejected_anchor_count, 2)
        self.assertEqual(
            estimate.rejection_reasons,
            ("input_outside_verified_coverage", "input_outside_verified_coverage"),
        )

    def test_outside_anchor_is_filtered_when_remaining_years_meet_threshold(self):
        anchors = (
            replace(anchor("outside", 2023), coverage_max_school_rank=100),
            anchor("inside-a", 2024),
            anchor("inside-b", 2025),
        )

        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.INFERRED)
        self.assertEqual(estimate.usable_anchor_count, 2)
        self.assertEqual(estimate.rejected_anchor_count, 1)
        self.assertEqual(
            estimate.rejection_reasons, ("input_outside_verified_coverage",)
        )
        self.assertEqual(estimate.contributing_years, (2024, 2025))

    def test_outside_reason_requires_coverage_to_be_the_actual_threshold_blocker(self):
        cases = (
            (
                "overlapping_same_year_sources",
                (
                    anchor("shared-a", 2025, source_ids=("shared",)),
                    anchor("shared-b", 2025, source_ids=("shared",)),
                    replace(
                        anchor("outside-independent", 2025, source_ids=("independent",)),
                        coverage_max_school_rank=100,
                    ),
                ),
            ),
            (
                "mixed_school",
                (
                    anchor("inside", 2024),
                    replace(
                        anchor("outside-school", 2025, school_name="另一中学"),
                        coverage_max_school_rank=100,
                    ),
                ),
            ),
            (
                "mixed_scope",
                (
                    anchor("inside", 2024),
                    replace(
                        anchor(
                            "outside-scope",
                            2025,
                            scope_type=RankScope.CLASS,
                            scope_value="高三一班",
                        ),
                        coverage_max_school_rank=100,
                    ),
                ),
            ),
            (
                "same_year_value_conflict",
                (
                    anchor("inside", 2025, province_rank=5000),
                    replace(
                        anchor("outside-conflict", 2025, province_rank=5100),
                        coverage_max_school_rank=100,
                    ),
                ),
            ),
        )
        for name, anchors in cases:
            with self.subTest(name=name):
                estimate = estimate_rank_from_anchors(anchors, None, 120)
                self.assertEqual(estimate.status, EvidenceStatus.MISSING)
                self.assertEqual(
                    estimate.reason_code, "insufficient_comparable_anchors"
                )
                self.assertIn(
                    "input_outside_verified_coverage", estimate.rejection_reasons
                )
                self.assertIsNone(estimate.lower_rank)

    def test_outside_reason_is_causal_when_restored_second_year_would_qualify(self):
        anchors = (
            anchor("inside", 2024),
            replace(anchor("outside", 2025), coverage_max_school_rank=100),
        )

        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.MISSING)
        self.assertEqual(estimate.reason_code, "input_outside_verified_coverage")
        self.assertIsNone(estimate.lower_rank)

    def test_verified_coverage_boundaries_are_inclusive(self):
        anchors = (
            replace(
                anchor("lower", 2024, school_rank=120),
                coverage_min_school_rank=120,
                coverage_max_school_rank=120,
            ),
            replace(
                anchor("upper", 2025, school_rank=120),
                coverage_min_school_rank=120,
                coverage_max_school_rank=120,
            ),
        )

        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.INFERRED)
        self.assertEqual(estimate.usable_anchor_count, 2)

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

    def test_estimate_rejects_non_atomic_or_unsafe_collection_elements(self):
        base = estimate_rank_from_anchors(
            (anchor("a", 2024), anchor("b", 2025)), None, 120
        )
        invalid_replacements = (
            {"reasons": [["nested"]]},
            {"rejection_reasons": [object()]},
            {"contributing_anchor_ids": ["unsafe id"]},
            {"contributing_anchor_ids": ["a", "a"]},
            {"contributing_years": [True]},
            {"contributing_years": [2024, 2024]},
            {"contributing_source_ids": [["nested"]]},
            {"contributing_source_ids": ["unsafe source"]},
        )
        for changes in invalid_replacements:
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    replace(base, **changes)

    def test_estimate_rejects_invalid_scalars_counts_and_success_bounds(self):
        base = estimate_rank_from_anchors(
            (anchor("a", 2024), anchor("b", 2025)), None, 120
        )
        invalid_replacements = (
            {"status": EvidenceStatus.OFFICIAL},
            {"method": ""},
            {"confidence": " "},
            {"input_anchor_count": True},
            {"input_anchor_count": -1},
            {"usable_anchor_count": base.input_anchor_count + 1},
            {"rejected_anchor_count": 1},
            {"lower_rank": None},
            {"lower_rank": 0},
            {"lower_rank": 1.5},
            {"upper_rank": base.lower_rank - 1},
            {"median_rank": base.upper_rank + 1},
            {"tolerance_rank": -1},
            {"tolerance_rank": 1.5},
            {"tolerance_rank": base.tolerance_rank + 1},
            {"reason_code": "unexpected_success_reason"},
        )
        for changes in invalid_replacements:
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    replace(base, **changes)

    def test_estimate_rejects_failure_status_with_numbers_or_missing_reason(self):
        missing = estimate_rank_from_anchors((anchor("only", 2025),), None, 120)
        invalid_replacements = (
            {"lower_rank": 1},
            {"upper_rank": 1},
            {"median_rank": 1},
            {"tolerance_rank": 0},
            {"reason_code": None},
            {"reason_code": "unsafe reason"},
            {"contributing_anchor_ids": ["unexpected"]},
            {"contributing_years": [2025]},
            {"contributing_source_ids": ["unexpected"]},
        )
        for changes in invalid_replacements:
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    replace(missing, **changes)

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
        self.assertIn("student_score_not_used_no_versioned_model", estimate.reasons)
        self.assertFalse(any("cross_check" in reason for reason in estimate.reasons))

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

    def test_same_year_school_score_missing_or_numeric_disagreement_is_conflict(self):
        anchors = (
            anchor("a", 2025, source_ids=("source-a",), school_score=None),
            anchor("b", 2025, source_ids=("source-b",), school_score=610),
            anchor("c", 2025, source_ids=("source-c",), school_score=610),
        )

        estimate = estimate_rank_from_anchors(anchors, None, 120)

        self.assertEqual(estimate.status, EvidenceStatus.CONFLICT)
        self.assertEqual(estimate.reason_code, "same_year_exact_disagreement")
        self.assertIsNone(estimate.lower_rank)

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

    def test_duplicate_anchor_id_with_any_conflicting_content_is_conflict_first(self):
        original = anchor("duplicate", 2024)
        conflicting_variants = (
            replace(original, school_name="另一中学"),
            replace(original, scope_type=RankScope.CLASS, scope_value="高三一班"),
            replace(original, year=2025),
            replace(original, school_rank=101),
            replace(original, province_rank=5001),
            replace(original, school_score=610),
            replace(original, evidence_status=EvidenceStatus.INFERRED),
            replace(original, source_ids=("source-b",)),
            replace(original, coverage_min_school_rank=2),
            replace(original, coverage_status=EvidenceStatus.PARTIAL),
        )
        for conflicting in conflicting_variants:
            with self.subTest(conflicting=conflicting):
                estimate = estimate_rank_from_anchors(
                    (original, conflicting, anchor("other-year", 2025)), None, 120
                )
                self.assertEqual(estimate.status, EvidenceStatus.CONFLICT)
                self.assertEqual(estimate.reason_code, "duplicate_anchor_id_conflict")
                self.assertIsNone(estimate.lower_rank)
                self.assertIsNone(estimate.upper_rank)

    def test_identical_duplicate_anchor_is_counted_but_does_not_weight_threshold(self):
        duplicate = anchor("duplicate", 2024)
        estimate = estimate_rank_from_anchors(
            (duplicate, duplicate, anchor("other-year", 2025)), None, 120
        )

        self.assertEqual(estimate.status, EvidenceStatus.INFERRED)
        self.assertEqual(estimate.input_anchor_count, 3)
        self.assertEqual(estimate.usable_anchor_count, 2)
        self.assertEqual(estimate.rejected_anchor_count, 1)
        self.assertEqual(estimate.rejection_reasons, ("duplicate_anchor",))

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
