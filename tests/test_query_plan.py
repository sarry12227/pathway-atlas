from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import query_plan as query_plan_module  # noqa: E402
from contracts import OrdinaryBatchPolicy, RecommendationProfile  # noqa: E402
from decision_policy import DecisionPolicySnapshot  # noqa: E402
from planning_profile import PlanningProfile  # noqa: E402
from province_registry import (  # noqa: E402
    SubjectSelectionError,
    canonical_discovery_subject_key,
    discover_provinces,
)
from query_plan import (  # noqa: E402
    MAX_PROVINCE_ALIASES,
    ProvinceCatalogError,
    ProvinceCatalogSnapshot,
    QueryPlan,
    QueryTask,
    ResearchContext,
    _catalog_from_payload,
    _build_query_plan_legacy,
    _load_profile,
    build_query_plan,
    load_province_catalog,
    validate_query_plan_payload,
)
from tests.test_planning_profile import reference_payload, v2_payload  # noqa: E402


def rehash_task_payload(task: dict) -> None:
    content = {key: value for key, value in task.items() if key != "task_id"}
    normalized = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    prefix = task["kind"].replace("_", "-")
    task["task_id"] = f"{prefix}:{hashlib.sha256(normalized).hexdigest()[:24]}"


def assert_draft_2020_payload(testcase: unittest.TestCase, schema: dict, payload) -> None:
    """Small independent Draft 2020-12 oracle for keywords used by this schema."""

    def same_json(left, right) -> bool:
        if (
            not isinstance(left, bool)
            and not isinstance(right, bool)
            and isinstance(left, (int, float))
            and isinstance(right, (int, float))
        ):
            return math.isfinite(left) and math.isfinite(right) and left == right
        return type(left) is type(right) and left == right

    def matches_type(expected, value) -> bool:
        if expected == "null":
            return value is None
        if expected == "object":
            return isinstance(value, dict)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                and value == int(value)
            )
        if expected == "number":
            return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        raise AssertionError(f"unsupported oracle type: {expected}")

    def visit(node: dict, value) -> None:
        if "$ref" in node:
            target = schema
            for segment in node["$ref"].removeprefix("#/").split("/"):
                target = target[segment]
            visit(target, value)
            return
        if "type" in node:
            expected = node["type"]
            choices = expected if isinstance(expected, list) else [expected]
            testcase.assertTrue(any(matches_type(choice, value) for choice in choices))
        if "const" in node:
            testcase.assertTrue(same_json(node["const"], value))
        if "enum" in node:
            testcase.assertTrue(any(same_json(item, value) for item in node["enum"]))
        if isinstance(value, str):
            testcase.assertGreaterEqual(len(value), node.get("minLength", 0))
            testcase.assertLessEqual(len(value), node.get("maxLength", len(value)))
            if "pattern" in node:
                testcase.assertIsNotNone(re.search(node["pattern"], value))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in node:
                testcase.assertGreaterEqual(value, node["minimum"])
            if "maximum" in node:
                testcase.assertLessEqual(value, node["maximum"])
        if isinstance(value, dict):
            testcase.assertTrue(set(node.get("required", ())) <= set(value))
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False:
                testcase.assertTrue(set(value) <= set(properties))
            for key, item in value.items():
                if key in properties:
                    visit(properties[key], item)
        if isinstance(value, list):
            testcase.assertGreaterEqual(len(value), node.get("minItems", 0))
            testcase.assertLessEqual(len(value), node.get("maxItems", len(value)))
            if node.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
                testcase.assertEqual(len(encoded), len(set(encoded)))
            prefix = node.get("prefixItems", [])
            for index, child in enumerate(prefix):
                if index < len(value):
                    visit(child, value[index])
            items = node.get("items")
            if items is False:
                testcase.assertLessEqual(len(value), len(prefix))
            elif isinstance(items, dict):
                for child_value in value[len(prefix):]:
                    visit(items, child_value)

    visit(schema, payload)


class QueryPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configs = discover_provinces(ROOT / "tests" / "fixtures" / "provinces")
        cls.catalog = load_province_catalog()
        cls.config_312 = replace(cls.configs["演示甲省"], province="黑龙江")
        cls.config_33 = replace(cls.configs["演示乙市"], province="海南")

    def profile_312(self, **changes) -> RecommendationProfile:
        values = {
            "rank": 1100,
            "target_province": "黑龙江",
            "subject_group": "物理",
            "secondary_subjects": frozenset({"化学", "地理"}),
        }
        values.update(changes)
        return RecommendationProfile(**values)

    def profile_33(self, **changes) -> RecommendationProfile:
        values = {
            "rank": 2100,
            "target_province": "海南",
            "subject_group": "化学",
            "secondary_subjects": frozenset({"物理", "生物"}),
        }
        values.update(changes)
        return RecommendationProfile(**values)

    def build_312(self, **changes) -> QueryPlan:
        values = {
            "profile": self.profile_312(),
            "province": self.config_312,
            "exam_year": 2026,
            "high_school_name": "演示第一中学",
            "requested_pathways": ["强基计划", "综合评价"],
            "catalog": self.catalog,
        }
        values.update(changes)
        return _build_query_plan_legacy(**values)

    def planning_profile(self, **changes) -> PlanningProfile:
        payload = reference_payload()
        payload.update(
            {
                "province": "湖北",
                "subject_mode": "3+1+2",
                "subject_group": "物理",
                "secondary_subjects": ["化学", "地理"],
                "exam_year": 2028,
            }
        )
        for key, value in changes.items():
            if key == "service_commitment":
                payload["constraints"][key] = value
            elif key in payload["pathway_preferences"]:
                payload["pathway_preferences"][key] = value
            else:
                payload[key] = value
        return PlanningProfile.create(payload)

    def build_public(self, **profile_changes) -> QueryPlan:
        with mock.patch.object(query_plan_module, "_current_utc_year", return_value=2026):
            return build_query_plan(
                self.planning_profile(**profile_changes),
                self.catalog,
                DecisionPolicySnapshot.load_default(),
            )

    def test_planning_profile_builds_plan_without_province_json(self) -> None:
        plan = self.build_public()

        self.assertEqual(plan.province, "湖北")
        self.assertEqual(plan.mode, "3+1+2")
        self.assertEqual(plan.subject_group, "物理+化学+地理")
        self.assertEqual(plan.exam_year, 2028)
        self.assertEqual(plan.research_year, 2026)
        self.assertEqual(tuple(sorted({task.year for task in plan.tasks})), (2023, 2024, 2025, 2026))

    def test_research_window_is_frozen_independently_from_future_exam_year(self) -> None:
        with mock.patch.object(
            query_plan_module, "_current_utc_year", return_value=2026
        ) as clock:
            plan = build_query_plan(
                self.planning_profile(exam_year=2028),
                self.catalog,
                DecisionPolicySnapshot.load_default(),
            )

        self.assertEqual(plan.exam_year, 2028)
        self.assertEqual(plan.research_year, 2026)
        self.assertEqual(
            tuple(task.year for task in plan.tasks if task.kind == "score_table"),
            (2026, 2025, 2024, 2023),
        )
        self.assertLessEqual(max(task.year for task in plan.tasks), plan.research_year)
        clock.assert_called_once_with()

    def test_validator_rejects_a_future_research_clock_snapshot(self) -> None:
        with mock.patch.object(query_plan_module, "_current_utc_year", return_value=2027):
            future = build_query_plan(
                self.planning_profile(exam_year=2028),
                self.catalog,
                DecisionPolicySnapshot.load_default(),
            ).to_dict()

        with mock.patch.object(query_plan_module, "_current_utc_year", return_value=2026):
            with self.assertRaisesRegex(ValueError, "cannot be in the future"):
                validate_query_plan_payload(future, catalog=self.catalog)

    def test_each_data_type_has_an_independent_strict_y_to_y_minus_three_sequence(self) -> None:
        plan = self.build_public()
        for identity in dict.fromkeys((task.kind, task.target_name) for task in plan.tasks):
            with self.subTest(identity=identity):
                self.assertEqual(
                    tuple(
                        task.year
                        for task in plan.tasks
                        if (task.kind, task.target_name) == identity
                    ),
                    (2026, 2025, 2024, 2023),
                )

    def test_profile_exclusions_omit_queries_and_leave_stable_trace(self) -> None:
        plan = self.build_public(
            service_commitment="reject",
            strong_foundation="not_interested",
            uniformed_service="interested",
        )
        targets = {(task.kind, task.target_name) for task in plan.tasks}

        self.assertNotIn(("strong_foundation", "强基计划"), targets)
        for target in (
            "公费师范",
            "优师计划",
            "定向医学生",
            "军校",
            "公安司法消防",
            "航海航空",
        ):
            self.assertNotIn(("special_pathway", target), targets)
        trace = {item.pathway_id: item for item in plan.pathway_trace}
        self.assertEqual(trace["strong_foundation"].decision, "exclude")
        self.assertEqual(trace["strong_foundation"].reason_code, "profile_not_interested")
        self.assertEqual(trace["service_oriented"].reason_code, "service_commitment_rejected")
        self.assertEqual(trace["uniformed_service"].reason_code, "service_commitment_rejected")

    def test_unknown_preference_creates_bounded_discovery_not_a_recommendation(self) -> None:
        plan = self.build_public(comprehensive_evaluation="unknown")
        tasks = [task for task in plan.tasks if task.kind == "comprehensive_evaluation"]
        trace = {item.pathway_id: item for item in plan.pathway_trace}

        self.assertEqual(tuple(task.year for task in tasks), (2026, 2025, 2024, 2023))
        self.assertTrue(all(task.max_candidates == 10 for task in tasks))
        self.assertTrue(all(task.max_network_retries == 1 for task in tasks))
        self.assertEqual(trace["comprehensive_evaluation"].decision, "discover")
        self.assertEqual(trace["comprehensive_evaluation"].reason_code, "preference_unknown_requires_discovery")

    def test_mutation_canary_rejects_tasks_for_an_excluded_pathway(self) -> None:
        safe = self.build_public().to_dict()
        self.assertEqual(
            validate_query_plan_payload(copy.deepcopy(safe), catalog=self.catalog).to_dict(),
            safe,
        )
        mutated = copy.deepcopy(safe)
        trace = next(
            item for item in mutated["pathway_trace"]
            if item["pathway_id"] == "strong_foundation"
        )
        trace.update(
            preference="not_interested",
            decision="exclude",
            reason_code="profile_not_interested",
        )

        with self.assertRaises(ValueError):
            validate_query_plan_payload(mutated, catalog=self.catalog)

    def test_mutation_canary_rejects_missing_active_pathway_family(self) -> None:
        safe = self.build_public().to_dict()
        self.assertEqual(
            validate_query_plan_payload(copy.deepcopy(safe), catalog=self.catalog).to_dict(),
            safe,
        )
        mutated = copy.deepcopy(safe)
        mutated["tasks"] = [
            item for item in mutated["tasks"]
            if item["kind"] != "comprehensive_evaluation"
        ]

        with self.assertRaises(ValueError):
            validate_query_plan_payload(mutated, catalog=self.catalog)

    def test_reviewer_rename_and_rehash_poc_cannot_escape_pathway_binding(self) -> None:
        safe = self.build_public(
            service_commitment="accept",
            service_oriented="interested",
            uniformed_service="interested",
            arts_sports="interested",
        ).to_dict()
        legitimate_pathways = {
            ("strong_foundation", "强基计划"),
            ("comprehensive_evaluation", "综合评价"),
            ("special_pathway", "国家专项"),
            ("special_pathway", "地方专项"),
            ("special_pathway", "高校专项"),
            ("special_pathway", "公费师范"),
            ("special_pathway", "优师计划"),
            ("special_pathway", "定向医学生"),
            ("special_pathway", "军校"),
            ("special_pathway", "公安司法消防"),
            ("special_pathway", "航海航空"),
            ("hk_macao_admission", "港澳招生"),
            ("special_pathway", "中外合作办学"),
            ("special_pathway", "艺体类"),
        }
        actual_pathways = {
            (task["kind"], task["target_name"])
            for task in safe["tasks"]
            if task["kind"] in {
                "strong_foundation",
                "comprehensive_evaluation",
                "hk_macao_admission",
                "special_pathway",
            }
        }
        self.assertEqual(actual_pathways, legitimate_pathways)
        self.assertEqual(
            validate_query_plan_payload(copy.deepcopy(safe), catalog=self.catalog).to_dict(),
            safe,
        )
        alias_control = self.build_312(
            requested_pathways=("强基", "综评", "港澳", "国家专项")
        )
        self.assertEqual(
            validate_query_plan_payload(
                alias_control.to_dict(), catalog=self.catalog
            ).to_dict(),
            alias_control.to_dict(),
        )

        mutated = copy.deepcopy(safe)
        for task in mutated["tasks"]:
            if task["kind"] == "strong_foundation":
                task["target_name"] = "强基计划改名"
                task["query_variants"] = [
                    query.replace("强基计划", "强基计划改名")
                    for query in task["query_variants"]
                ]
                rehash_task_payload(task)
        trace = next(
            item for item in mutated["pathway_trace"]
            if item["pathway_id"] == "strong_foundation"
        )
        trace.update(
            preference="not_interested",
            decision="exclude",
            reason_code="profile_not_interested",
        )

        with self.assertRaises(ValueError):
            validate_query_plan_payload(mutated, catalog=self.catalog)

    def test_312_canonical_subject_and_task_ids_are_permutation_invariant(self) -> None:
        forward = self.build_public(secondary_subjects=["化学", "地理"])
        reverse = self.build_public(secondary_subjects=["地理", "化学"])

        self.assertEqual(forward.subject_group, "物理+化学+地理")
        self.assertEqual(reverse.subject_group, forward.subject_group)
        self.assertEqual(
            tuple(task.task_id for task in reverse.tasks),
            tuple(task.task_id for task in forward.tasks),
        )
        for invalid in (["化学", "化学"], ["化学", "天文"]):
            with self.subTest(invalid=invalid), self.assertRaises(SubjectSelectionError):
                canonical_discovery_subject_key("3+1+2", "物理", invalid)

    def test_research_context_and_plan_bind_catalog_identity(self) -> None:
        profile = self.planning_profile()
        context = ResearchContext.create(profile, self.catalog)
        plan = build_query_plan(profile, self.catalog, DecisionPolicySnapshot.load_default())

        self.assertEqual(context.catalog_digest, self.catalog.digest)
        self.assertEqual(plan.catalog_digest, self.catalog.digest)
        forged = plan.to_dict()
        forged["catalog_digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            validate_query_plan_payload(forged, catalog=self.catalog)

    def test_catalog_snapshot_is_factory_only_after_identity_validation(self) -> None:
        with self.assertRaises(TypeError):
            ProvinceCatalogSnapshot(
                schema_version=self.catalog.schema_version,
                verified_at=self.catalog.verified_at,
                coverage_note=self.catalog.coverage_note,
                mode_authority_urls=self.catalog.mode_authority_urls,
                provinces=self.catalog.provinces,
                digest=self.catalog.digest,
            )
        with self.assertRaises(TypeError):
            replace(self.catalog, verified_at="2026-08-28")

    def test_no_payload_or_module_token_seam_can_forge_a_tracked_catalog(self) -> None:
        payload = json.loads(
            (ROOT / "references" / "provinces" / "index.json").read_text("utf-8")
        )
        self.assertFalse(
            any("trust_token" in name.casefold() for name in vars(query_plan_module))
        )
        self.assertNotIn("_trusted_for_planning", vars(self.catalog))
        with self.assertRaises(TypeError):
            _catalog_from_payload(payload, _trust_token=object())
        with self.assertRaises(TypeError):
            _catalog_from_payload(payload, trusted_for_planning=True)

        generic_canonical = _catalog_from_payload(copy.deepcopy(payload))
        direct_factory_catalog = ProvinceCatalogSnapshot._create(
            schema_version=generic_canonical.schema_version,
            verified_at=generic_canonical.verified_at,
            coverage_note=generic_canonical.coverage_note,
            mode_authority_urls=generic_canonical.mode_authority_urls,
            provinces=generic_canonical.provinces,
            digest=generic_canonical.digest,
        )

        hubei = next(item for item in payload["provinces"] if item["province"] == "湖北")
        hubei["authority_name"] = "伪造考试机构"
        hubei["official_roots"] = ["https://www.baidu.com/"]
        forged_payload_catalog = _catalog_from_payload(copy.deepcopy(payload))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "caller-catalog.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            forged_file_catalog = load_province_catalog(path)

        for forged in (
            generic_canonical,
            direct_factory_catalog,
            forged_payload_catalog,
            forged_file_catalog,
        ):
            with self.subTest(factory=type(forged).__name__), self.assertRaises(
                ProvinceCatalogError
            ):
                build_query_plan(
                    self.planning_profile(),
                    forged,
                    DecisionPolicySnapshot.load_default(),
                )

        tracked = load_province_catalog()
        self.assertEqual(
            build_query_plan(
                self.planning_profile(),
                tracked,
                DecisionPolicySnapshot.load_default(),
            ).catalog_digest,
            tracked.digest,
        )

    def test_schema_valid_v3_profile_file_loads_without_private_migration(self) -> None:
        payload = reference_payload()
        payload.update(
            {
                "province": "湖北",
                "subject_mode": "3+1+2",
                "subject_group": "物理",
                "secondary_subjects": ["化学", "地理"],
                "exam_year": 2028,
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile-v3.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            loaded, mode, year = _load_profile(path)

        self.assertIsInstance(loaded, PlanningProfile)
        self.assertEqual(loaded.schema_version, "3.0")
        self.assertEqual(mode, "3+1+2")
        self.assertEqual(year, 2028)

    def test_plan_covers_all_required_query_kinds(self):
        kinds = {task.kind for task in self.build_312().tasks}
        self.assertEqual(
            kinds,
            {
                "province_policy",
                "score_table",
                "batch_admission",
                "joy_report",
                "enrollment_plan",
                "admission_charter",
                "tuition_fee",
                "subject_requirement",
                "strong_foundation",
                "comprehensive_evaluation",
                "hk_macao_admission",
                "special_pathway",
            },
        )

    def test_default_plan_researches_every_approved_pathway_family_for_four_years(self):
        plan = self.build_312()
        expected = {
            "国家专项",
            "地方专项",
            "高校专项",
            "公费师范",
            "优师计划",
            "定向医学生",
            "军校",
            "公安司法消防",
            "航海航空",
            "中外合作办学",
            "艺体类",
        }
        tasks = [task for task in plan.tasks if task.kind == "special_pathway"]
        self.assertEqual({task.target_name for task in tasks}, expected)
        for target in expected:
            selected = [task for task in tasks if task.target_name == target]
            self.assertEqual({task.year for task in selected}, {2023, 2024, 2025, 2026})
            self.assertTrue(
                all(task.preferred_source_tiers == ("A", "B", "C") for task in selected)
            )

    def test_catalog_backed_plan_has_independent_research_families(self):
        from query_plan import load_province_catalog

        catalog = load_province_catalog()
        base = self.configs["演示甲省"]
        province = replace(base, province="黑龙江")
        profile = self.profile_312(target_province="黑龙江")

        plan = _build_query_plan_legacy(
            profile,
            province,
            2026,
            high_school_name="演示第一中学",
            requested_pathways=("定向医学生",),
            catalog=catalog,
        )

        self.assertEqual(plan.authority_name, "黑龙江省招生考试院")
        self.assertEqual(plan.official_roots, ("https://www.hljea.org.cn/",))
        with mock.patch(
            "query_plan.load_province_catalog",
            side_effect=AssertionError("unexpected catalog I/O"),
        ):
            self.assertEqual(
                validate_query_plan_payload(plan.to_dict(), catalog=catalog).to_dict(),
                plan.to_dict(),
            )
        kinds = {task.kind for task in plan.tasks}
        self.assertTrue(
            {
                "province_policy",
                "score_table",
                "batch_admission",
                "joy_report",
                "enrollment_plan",
                "admission_charter",
                "tuition_fee",
                "subject_requirement",
                "strong_foundation",
                "comprehensive_evaluation",
                "hk_macao_admission",
                "special_pathway",
            }
            <= kinds
        )
        self.assertNotIn("pathway_policy", kinds)

        required = {task.kind: task.required_extraction_fields for task in plan.tasks}
        self.assertTrue({"training_model", "transition_routes", "outcomes"} <= set(required["strong_foundation"]))
        self.assertTrue({"score_ratio", "school_assessment", "outcomes"} <= set(required["comprehensive_evaluation"]))
        self.assertTrue({"english_requirement", "fees", "outcomes"} <= set(required["hk_macao_admission"]))
        self.assertTrue(
            {
                "employment_restrictions",
                "geographic_restrictions",
                "service_term",
                "breach_consequences",
                "fees_and_subsidies",
            }
            <= set(required["special_pathway"])
        )

    def test_all_29_catalog_records_drive_exact_discovery_metadata(self):
        from query_plan import load_province_catalog

        expected = {
            "北京": ("北京教育考试院", ("https://www.bjeea.cn/",)),
            "天津": ("天津市教育招生考试院", ("https://jy.tj.gov.cn/",)),
            "上海": ("上海市教育考试院", ("https://www.shmeea.edu.cn/",)),
            "浙江": ("浙江省教育考试院", ("https://www.zjzs.net/",)),
            "山东": ("山东省教育招生考试院", ("https://www.sdzk.cn/",)),
            "海南": ("海南省考试局", ("https://ea.hainan.gov.cn/",)),
            "河北": ("河北省教育考试院", ("https://www.hebeea.edu.cn/",)),
            "山西": ("山西省招生考试管理中心", ("https://jyt.shanxi.gov.cn/",)),
            "内蒙古": ("内蒙古自治区教育考试院", ("https://www.nm.zsks.cn/",)),
            "辽宁": (
                "辽宁省高中等教育招生考试委员会办公室",
                ("https://www.lnzsks.com/",),
            ),
            "吉林": ("吉林省教育考试院", ("https://www.jleea.com.cn/",)),
            "黑龙江": ("黑龙江省招生考试院", ("https://www.hljea.org.cn/",)),
            "江苏": ("江苏省教育考试院", ("https://www.jseea.cn/",)),
            "安徽": ("安徽省教育招生考试院", ("https://www.ahzsks.cn/",)),
            "福建": ("福建省教育考试院", ("https://www.eeafj.cn/",)),
            "江西": ("江西省教育考试院", ("https://jyt.jiangxi.gov.cn/",)),
            "河南": ("河南省教育考试院", ("https://www.haeea.cn/",)),
            "湖北": ("湖北省教育考试院", ("https://www.hbea.edu.cn/",)),
            "湖南": (
                "湖南省教育考试院",
                ("https://jyt.hunan.gov.cn/jyt/sjyt/hnsjyksy/",),
            ),
            "广东": ("广东省教育考试院", ("https://eea.gd.gov.cn/",)),
            "广西": ("广西壮族自治区招生考试院", ("https://www.gxeea.cn/",)),
            "重庆": ("重庆市教育考试院", ("https://www.cqksy.cn/",)),
            "四川": ("四川省教育考试院", ("https://www.sceea.cn/",)),
            "贵州": ("贵州省招生考试院", ("https://zsksy.guizhou.gov.cn/",)),
            "云南": ("云南省招生考试院", ("https://www.ynzs.cn/",)),
            "陕西": ("陕西省教育考试院", ("https://www.sneea.cn/",)),
            "甘肃": ("甘肃省教育考试院", ("https://www.ganseea.cn/",)),
            "青海": ("青海省教育招生考试院", ("https://www.qhjyks.com/",)),
            "宁夏": ("宁夏教育考试院", ("https://www.nxjyks.cn/",)),
        }
        catalog = load_province_catalog()
        self.assertEqual(len(catalog.provinces), 29)
        self.assertEqual(
            {
                item.province: (item.authority_name, item.official_roots)
                for item in catalog.provinces
            },
            expected,
        )
        base_312 = self.configs["演示甲省"]
        base_33 = self.configs["演示乙市"]
        for discovery in catalog.provinces:
            with self.subTest(province=discovery.province):
                if discovery.mode == "3+3":
                    config = replace(base_33, province=discovery.province)
                    profile = self.profile_33(target_province=discovery.province)
                else:
                    config = replace(base_312, province=discovery.province)
                    profile = self.profile_312(target_province=discovery.province)
                plan = _build_query_plan_legacy(profile, config, 2026, catalog=catalog)
                self.assertEqual(
                    validate_query_plan_payload(
                        plan.to_dict(), catalog=catalog
                    ).to_dict(),
                    plan.to_dict(),
                )
                self.assertEqual(plan.authority_name, discovery.authority_name)
                self.assertEqual(plan.official_roots, discovery.official_roots)
                self.assertTrue(
                    all(task.authority_name == discovery.authority_name for task in plan.tasks)
                )
                self.assertTrue(
                    all(task.official_roots == discovery.official_roots for task in plan.tasks)
                )
                self.assertIn(
                    discovery.authority_name,
                    "\n".join(query for task in plan.tasks for query in task.query_variants),
                )
                if discovery.province == "海南":
                    serialized = json.dumps(plan.to_dict(), ensure_ascii=False)
                    self.assertIn("海南省考试局", serialized)
                    self.assertNotIn("海南教育考试院", serialized)

    def test_semantic_validator_rejects_fully_forged_rehashed_catalog_authority(self):
        payload = self.build_312().to_dict()
        trusted_authority = payload["authority_name"]
        payload["authority_name"] = "Forged Authority"
        payload["official_roots"] = ["https://www.baidu.com/"]
        for task in payload["tasks"]:
            task["authority_name"] = "Forged Authority"
            task["official_roots"] = ["https://www.baidu.com/"]
            task["query_variants"] = [
                query.replace(trusted_authority, "Forged Authority")
                for query in task["query_variants"]
            ]
            rehash_task_payload(task)

        with self.assertRaises(ValueError):
            validate_query_plan_payload(payload)

    def test_semantic_validator_compares_each_trusted_catalog_identity_field(self):
        authority = self.build_312().to_dict()
        trusted_authority = authority["authority_name"]
        authority["authority_name"] = "Forged Authority"
        for task in authority["tasks"]:
            task["authority_name"] = "Forged Authority"
            task["query_variants"] = [
                query.replace(trusted_authority, "Forged Authority")
                for query in task["query_variants"]
            ]
            rehash_task_payload(task)

        roots = self.build_312().to_dict()
        roots["official_roots"] = ["https://www.baidu.com/"]
        for task in roots["tasks"]:
            task["official_roots"] = ["https://www.baidu.com/"]
            rehash_task_payload(task)

        verification_date = self.build_312().to_dict()
        verification_date["catalog_verified_at"] = "2026-08-23"

        for name, forged in (
            ("authority_name", authority),
            ("official_roots", roots),
            ("catalog_verified_at", verification_date),
        ):
            with self.subTest(field=name), self.assertRaises(ValueError):
                validate_query_plan_payload(forged, catalog=self.catalog)

    def test_catalog_loader_rejects_nonarray_collections_and_nonweb_roots(self):
        tracked = json.loads(
            (ROOT / "references" / "provinces" / "index.json").read_text("utf-8")
        )

        def aliases_as_object(payload):
            record = payload["provinces"][0]
            record["aliases"] = {record["province"]: "forged"}

        def roots_as_object(payload):
            root = payload["provinces"][0]["official_roots"][0]
            payload["provinces"][0]["official_roots"] = {root: "forged"}

        def internal_root(payload):
            payload["provinces"][0]["official_roots"] = [
                "https://metadata.internal/"
            ]

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            for index, mutate in enumerate(
                (aliases_as_object, roots_as_object, internal_root)
            ):
                payload = copy.deepcopy(tracked)
                mutate(payload)
                path = directory / f"invalid-catalog-{index}.json"
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(
                    (TypeError, ValueError)
                ):
                    load_province_catalog(path)

    def test_catalog_loader_enforces_the_same_finite_alias_bound_as_schema(self):
        self.assertEqual(MAX_PROVINCE_ALIASES, 3)
        tracked = json.loads(
            (ROOT / "references" / "provinces" / "index.json").read_text("utf-8")
        )
        within_limit = copy.deepcopy(tracked)
        within_limit["provinces"][0]["aliases"].append("京城")
        over_limit = copy.deepcopy(within_limit)
        over_limit["provinces"][0]["aliases"].append("首都地区")

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            accepted_path = directory / "accepted-catalog.json"
            accepted_path.write_text(
                json.dumps(within_limit, ensure_ascii=False),
                encoding="utf-8",
            )
            rejected_path = directory / "rejected-catalog.json"
            rejected_path.write_text(
                json.dumps(over_limit, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(load_province_catalog(accepted_path).provinces[0].aliases[-1], "京城")
            with self.assertRaises(ProvinceCatalogError):
                load_province_catalog(rejected_path)

    def test_structured_targets_are_bound_to_queries_and_ids(self):
        plan = self.build_312()
        joy = [task for task in plan.tasks if task.kind == "joy_report"]
        pathways = [
            task
            for task in plan.tasks
            if task.kind in {"strong_foundation", "comprehensive_evaluation"}
        ]
        self.assertTrue(all(task.target_name == "演示第一中学" for task in joy))
        self.assertEqual(
            {task.target_name for task in pathways}, {"强基计划", "综合评价"}
        )
        self.assertTrue(
            all(
                task.target_name is None
                for task in plan.tasks
                if task.kind == "score_table"
            )
        )

        payload = plan.to_dict()
        target_task = next(task for task in payload["tasks"] if task["kind"] == "joy_report")
        target_task["target_name"] = "伪造中学"
        rehash_task_payload(target_task)
        with self.assertRaises(ValueError):
            validate_query_plan_payload(payload)

    def test_candidate_limit_and_tiers_are_non_negotiable(self):
        for task in self.build_312().tasks:
            self.assertEqual(task.max_candidates, 10)
            self.assertEqual(task.preferred_source_tiers, ("A", "B", "C"))

    def test_every_annual_family_uses_target_year_through_target_minus_three(self):
        tasks = [
            task
            for task in self.build_312().tasks
            if task.kind == "batch_admission"
        ]
        window = {2023, 2024, 2025, 2026}
        self.assertEqual({task.year for task in tasks}, window)
        self.assertEqual(
            {task.target_name for task in tasks},
            {"普通批", "提前批", "综合评价批"},
        )
        for kind in (
            "province_policy",
            "score_table",
            "joy_report",
            "enrollment_plan",
            "admission_charter",
            "tuition_fee",
            "subject_requirement",
            "strong_foundation",
            "comprehensive_evaluation",
            "hk_macao_admission",
        ):
            with self.subTest(kind=kind):
                self.assertEqual(
                    {task.year for task in self.build_312().tasks if task.kind == kind},
                    window,
                )
        special_targets = {
            task.target_name
            for task in self.build_312().tasks
            if task.kind == "special_pathway"
        }
        for target in special_targets:
            self.assertEqual(
                {
                    task.year
                    for task in self.build_312().tasks
                    if task.kind == "special_pathway" and task.target_name == target
                },
                window,
            )
        self.assertNotIn("latest", json.dumps(self.build_312().to_dict()))

    def test_charter_and_tuition_are_distinct_targetless_typed_families(self):
        for label, plan in (
            ("public", self.build_public()),
            ("legacy", self.build_312()),
        ):
            with self.subTest(builder=label):
                charter = tuple(
                    task for task in plan.tasks
                    if task.kind == "admission_charter"
                )
                tuition = tuple(
                    task for task in plan.tasks if task.kind == "tuition_fee"
                )
                self.assertEqual(
                    tuple(task.year for task in charter),
                    (2026, 2025, 2024, 2023),
                )
                self.assertEqual(
                    tuple(task.year for task in tuition),
                    (2026, 2025, 2024, 2023),
                )
                self.assertTrue(
                    all(task.target_name is None for task in (*charter, *tuition))
                )
                charter_fields = set(charter[0].required_extraction_fields)
                tuition_fields = set(tuition[0].required_extraction_fields)
                self.assertGreaterEqual(
                    charter_fields,
                    {
                        "institution",
                        "institution_code",
                        "admission_rules",
                        "adjustment_rules",
                        "adjustment_required",
                        "health_restrictions",
                        "language_restrictions",
                        "single_subject_restrictions",
                        "special_conditions",
                    },
                )
                self.assertGreaterEqual(
                    tuition_fields,
                    {
                        "institution",
                        "institution_code",
                        "program_group",
                        "majors",
                        "annual_fee_amount",
                        "fee_currency",
                        "fee_period",
                        "accommodation_fee",
                        "other_required_fees",
                        "financial_aid",
                    },
                )
                self.assertNotEqual(charter_fields, tuition_fields)
                enrollment_fields = set(
                    next(
                        task for task in plan.tasks
                        if task.kind == "enrollment_plan"
                    ).required_extraction_fields
                )
                self.assertTrue(
                    {
                        "adjustment_required",
                        "annual_fee_amount",
                        "fee_currency",
                        "fee_period",
                    }.isdisjoint(enrollment_fields)
                )
                self.assertTrue(
                    all(
                        "招生章程" in " ".join(task.query_variants)
                        for task in charter
                    )
                )
                self.assertTrue(
                    all("学费" in " ".join(task.query_variants) for task in tuition)
                )

        forged = self.build_public().to_dict()
        targetless = next(
            task for task in forged["tasks"]
            if task["kind"] == "admission_charter"
        )
        targetless["target_name"] = "伪造院校"
        rehash_task_payload(targetless)
        with self.assertRaises(ValueError):
            validate_query_plan_payload(forged, catalog=self.catalog)

    def test_mode_aware_subject_keys_and_ids_are_deterministic_safe_ascii(self):
        first = self.build_312()
        second = self.build_312()
        self.assertEqual(first.subject_group, "物理")
        self.assertEqual(first.to_dict(), second.to_dict())

        plan_33 = _build_query_plan_legacy(
            self.profile_33(),
            self.config_33,
            2026,
            high_school_name="演示第二中学",
            requested_pathways=("强基计划",),
            catalog=self.catalog,
        )
        self.assertEqual(plan_33.subject_group, "物理+化学+生物")
        for task in (*first.tasks, *plan_33.tasks):
            self.assertEqual(task.subject_group, first.subject_group if task in first.tasks else plan_33.subject_group)
            self.assertRegex(task.task_id, r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
            self.assertTrue(task.task_id.startswith(task.kind.replace("_", "-") + ":"))
            self.assertNotRegex(task.task_id, r"[^\x00-\x7f]")

    def test_serialization_is_byte_identical_and_inputs_are_snapshotted(self):
        pathways = ["综合评价", "强基计划"]
        plan = self.build_312(requested_pathways=pathways)
        before = json.dumps(
            plan.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        pathways.append("后来路径")
        after = json.dumps(
            plan.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(before, after)
        self.assertNotIn("后来路径", after.decode("utf-8"))
        self.assertIsInstance(plan.tasks, tuple)
        self.assertTrue(all(isinstance(task.query_variants, tuple) for task in plan.tasks))
        with self.assertRaises(FrozenInstanceError):
            plan.exam_year = 2027

    def test_availability_requires_checks_without_guessing_publication_facts(self):
        plan = self.build_312()
        current = [task for task in plan.tasks if task.year == 2026]
        older = [task for task in plan.tasks if task.year < 2026]
        self.assertTrue(current)
        self.assertTrue(older)
        self.assertTrue(
            all(
                task.availability_expectation == "current_year_availability_must_be_checked"
                and task.freshness_rule == "verify_exact_current_year_availability"
                for task in current
            )
        )
        self.assertTrue(
            all(
                task.availability_expectation == "expected_available"
                and task.freshness_rule == "query_exact_expected_available_year"
                for task in older
            )
        )
        serialized = json.dumps(plan.to_dict(), ensure_ascii=False).casefold()
        for forbidden in ("not_yet_expected", "substitute", "替代", "尚未发布"):
            self.assertNotIn(forbidden, serialized)

    def test_queries_include_public_context_and_never_profile_preferences(self):
        private_preferences = self.profile_312(
            target_schools=("不应进入计划大学",),
            target_cities=("不应进入计划城市",),
        )
        plan = self.build_312(profile=private_preferences)
        queries = "\n".join(
            query for task in plan.tasks for query in task.query_variants
        )
        for value in (
            "黑龙江",
            "黑龙江省招生考试院",
            "2026",
            "物理",
            "演示第一中学",
            "喜报",
            "光荣榜",
            "升学成果",
            "强基计划",
            "综合评价",
        ):
            self.assertIn(value, queries)
        self.assertNotIn("不应进入计划大学", queries)
        self.assertNotIn("不应进入计划城市", queries)

    def test_each_task_query_set_carries_region_year_and_canonical_subject(self):
        for task in self.build_312().tasks:
            query_text = " ".join(task.query_variants)
            self.assertIn(task.province, query_text)
            self.assertIn(str(task.year), query_text)
            self.assertIn(task.subject_group, query_text)

    def test_default_api_emits_independent_pathway_families_without_generic_scope(self):
        plan = _build_query_plan_legacy(
            self.profile_312(), self.config_312, 2026, catalog=self.catalog
        )
        kinds = {task.kind for task in plan.tasks}
        self.assertTrue(
            {"strong_foundation", "comprehensive_evaluation", "hk_macao_admission"}
            <= kinds
        )
        self.assertNotIn("pathway_policy", kinds)
        generic_joy = [task for task in plan.tasks if task.kind == "joy_report"]
        self.assertEqual(len(generic_joy), 4)
        self.assertTrue(all(task.target_name is None for task in generic_joy))
        for task in generic_joy:
            query_text = " ".join(task.query_variants)
            for synonym in ("高中喜报", "高考光荣榜", "高中升学成果"):
                self.assertIn(synonym, query_text)

    def test_public_text_rejects_pii_paths_urls_secrets_and_untrimmed_values(self):
        invalid_values = (
            " 张三中学",
            "姓名:张三",
            "13800138000",
            "https://example.com/policy",
            "C:\\private\\school.txt",
            "../secret.txt",
            "api_key=abcdefghijklmnop",
        )
        for value in invalid_values:
            with self.subTest(high_school=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.build_312(high_school_name=value)
            with self.subTest(pathway=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.build_312(requested_pathways=(value,))
        with self.assertRaises(TypeError):
            self.build_312(requested_pathways="强基计划")
        with self.assertRaises(ValueError):
            self.build_312(requested_pathways=("强基计划", "强基计划"))

    def test_public_text_is_nfkc_normalized_before_order_dedup_and_digest(self):
        ascii_plan = self.build_312(high_school_name="Café中学")
        compatibility_plan = self.build_312(
            high_school_name=unicodedata.normalize("NFD", "Café中学"),
        )
        self.assertEqual(ascii_plan.to_dict(), compatibility_plan.to_dict())

        ordered = self.build_312(requested_pathways=("艺体类", "国家专项"))
        ordered_targets = list(
            dict.fromkeys(
                task.target_name
                for task in ordered.tasks
                if task.kind == "special_pathway"
            )
        )
        self.assertEqual(ordered_targets[:2], ["国家专项", "艺体类"])
        with self.assertRaises(ValueError):
            self.build_312(requested_pathways=("国家专项", "国家专项"))

        invalid = (
            "A\u200b计划",
            "A/B计划",
            "A\\B计划",
            "%USERPROFILE%",
            "$HOME",
            "C:secret",
            ".\\secret",
            "../secret",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.build_312(requested_pathways=(value,))

    def test_exam_year_is_a_finite_mathematical_integer_and_normalized(self):
        self.assertEqual(self.build_312(exam_year=2026.0).exam_year, 2026)
        for value in (True, "2026", 2026.5, float("nan"), float("inf"), 1999, 2101):
            with self.subTest(exam_year=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.build_312(exam_year=value)

    def test_runtime_and_draft_oracle_share_mathematical_integer_semantics(self):
        plan = self.build_312()
        self.assertIsInstance(replace(plan, exam_year=2026.0).exam_year, int)
        task = plan.tasks[0]
        self.assertIsInstance(replace(task, year=float(task.year)).year, int)
        self.assertIsInstance(replace(task, max_candidates=10.0).max_candidates, int)

        payload = plan.to_dict()
        payload["exam_year"] = 2026.0
        for item in payload["tasks"]:
            item["year"] = float(item["year"])
            item["max_candidates"] = 10.0
        normalized = validate_query_plan_payload(payload).to_dict()
        self.assertIsInstance(normalized["exam_year"], int)
        self.assertTrue(all(isinstance(item["year"], int) for item in normalized["tasks"]))
        self.assertTrue(all(isinstance(item["max_candidates"], int) for item in normalized["tasks"]))

        schema = json.loads(
            (ROOT / "schemas" / "query-plan.schema.json").read_text("utf-8")
        )
        assert_draft_2020_payload(self, schema, payload)

        for invalid in (True, 2026.5, float("nan"), float("inf")):
            with self.subTest(exam_year=invalid):
                malformed = copy.deepcopy(payload)
                malformed["exam_year"] = invalid
                with self.assertRaises((AssertionError, TypeError, ValueError)):
                    assert_draft_2020_payload(self, schema, malformed)
                with self.assertRaises((TypeError, ValueError)):
                    validate_query_plan_payload(malformed)
            with self.subTest(task_year=invalid):
                malformed = copy.deepcopy(payload)
                malformed["tasks"][0]["year"] = invalid
                with self.assertRaises((AssertionError, TypeError, ValueError)):
                    assert_draft_2020_payload(self, schema, malformed)
                with self.assertRaises((TypeError, ValueError)):
                    validate_query_plan_payload(malformed)
            with self.subTest(max_candidates=invalid):
                malformed = copy.deepcopy(payload)
                malformed["tasks"][0]["max_candidates"] = invalid
                with self.assertRaises((AssertionError, TypeError, ValueError)):
                    assert_draft_2020_payload(self, schema, malformed)
                with self.assertRaises((TypeError, ValueError)):
                    validate_query_plan_payload(malformed)

    def test_requires_real_profile_and_province_and_matching_context(self):
        with self.assertRaises(TypeError):
            _build_query_plan_legacy({}, self.config_312, 2026, catalog=self.catalog)
        with self.assertRaises(TypeError):
            _build_query_plan_legacy(self.profile_312(), {}, 2026, catalog=self.catalog)
        with self.assertRaises(ValueError):
            _build_query_plan_legacy(self.profile_312(), self.config_33, 2026, catalog=self.catalog)

    def test_build_revalidates_the_complete_province_config_without_io(self):
        config = self.config_312
        invalid_configs = (
            replace(config, schema_version="2.0"),
            replace(config, mode="traditional"),
            replace(config, primary_subjects=["物理", "历史"]),
            replace(config, secondary_subjects=("化学", "化学")),
            replace(config, score_scale=True),
            replace(config, score_scale=float("nan")),
            replace(config, score_scale=1001),
            replace(config, directory="not-a-path"),
            replace(config, directory=Path("relative")),
            replace(config, ordinary_batch_policy=None),
            replace(config, ordinary_batch_policy={"policy_id": "fake"}),
        )
        for invalid in invalid_configs:
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    _build_query_plan_legacy(self.profile_312(), invalid, 2026, catalog=self.catalog)

        policy = OrdinaryBatchPolicy(**config.ordinary_batch_policy.to_dict())
        object.__setattr__(policy, "stable_delta_le", policy.challenge_delta_lt - 1)
        with self.assertRaises(ValueError):
            _build_query_plan_legacy(
                self.profile_312(),
                replace(config, ordinary_batch_policy=policy),
                2026,
                catalog=self.catalog,
            )

        with mock.patch.object(Path, "exists", side_effect=AssertionError("I/O")), mock.patch.object(
            Path, "resolve", side_effect=AssertionError("I/O")
        ), mock.patch("os.lstat", side_effect=AssertionError("I/O")):
            self.build_312()

    def test_direct_construction_and_replace_cannot_bypass_semantics(self):
        task = self.build_312().tasks[0]
        with self.assertRaises(ValueError):
            replace(task, task_id="score-table:wrong")
        with self.assertRaises((TypeError, ValueError)):
            QueryTask(
                task_id=task.task_id,
                kind=task.kind,
                province=task.province,
                year=task.year,
                subject_group=task.subject_group,
                authority_name=task.authority_name,
                official_roots=task.official_roots,
                target_name=task.target_name,
                query_variants="not-a-collection",
                preferred_source_tiers=task.preferred_source_tiers,
                max_candidates=task.max_candidates,
                freshness_rule=task.freshness_rule,
                required_extraction_fields=task.required_extraction_fields,
                availability_expectation=task.availability_expectation,
            )
        with self.assertRaises(ValueError):
            replace(self.build_312(), research_year=2025)

    def test_semantic_validator_rechecks_every_task_contract_after_rehash(self):
        explicit = self.build_312().to_dict()
        generic = _build_query_plan_legacy(
            self.profile_312(), self.config_312, 2026, catalog=self.catalog
        ).to_dict()
        malformed_payloads = []

        context = copy.deepcopy(explicit)
        task = context["tasks"][0]
        task["query_variants"] = [f"{task['province']} {task['year']} 一分一段"]
        rehash_task_payload(task)
        malformed_payloads.append(context)

        tiers = copy.deepcopy(explicit)
        task = tiers["tasks"][0]
        task["preferred_source_tiers"] = ["A", "C", "B"]
        rehash_task_payload(task)
        malformed_payloads.append(tiers)

        candidates = copy.deepcopy(explicit)
        task = candidates["tasks"][0]
        task["max_candidates"] = 9
        rehash_task_payload(task)
        malformed_payloads.append(candidates)

        extraction = copy.deepcopy(explicit)
        task = extraction["tasks"][0]
        task["required_extraction_fields"] = ["province"]
        rehash_task_payload(task)
        malformed_payloads.append(extraction)

        availability = copy.deepcopy(explicit)
        task = next(item for item in availability["tasks"] if item["year"] == 2024)
        task["availability_expectation"] = "current_year_availability_must_be_checked"
        task["freshness_rule"] = "verify_exact_current_year_availability"
        rehash_task_payload(task)
        malformed_payloads.append(availability)

        generic_joy = copy.deepcopy(generic)
        task = next(item for item in generic_joy["tasks"] if item["kind"] == "joy_report")
        task["query_variants"] = task["query_variants"][:2]
        rehash_task_payload(task)
        malformed_payloads.append(generic_joy)

        forged_authority = copy.deepcopy(generic)
        task = forged_authority["tasks"][0]
        task["authority_name"] = "伪造考试机构"
        rehash_task_payload(task)
        malformed_payloads.append(forged_authority)

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises((TypeError, ValueError)):
                    validate_query_plan_payload(payload)


class QueryPlanSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(
            (ROOT / "schemas" / "query-plan.schema.json").read_text("utf-8")
        )

    def test_schema_is_independently_strict_and_declares_semantic_locator(self):
        self.assertEqual(
            self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(
            set(self.schema["required"]),
            {
                "schema_version",
                "province",
                "mode",
                "exam_year",
                "research_year",
                "subject_group",
                "authority_name",
                "official_roots",
                "catalog_verified_at",
                "catalog_digest",
                "decision_policy_id",
                "decision_policy_digest",
                "decision_basis_id",
                "decision_source_id",
                "decision_source_version",
                "source_policy_id",
                "source_policy_version",
                "pathway_trace",
                "tasks",
            },
        )
        task = self.schema["$defs"]["queryTask"]
        self.assertFalse(task["additionalProperties"])
        self.assertIn("target_name", task["required"])
        self.assertEqual(task["properties"]["kind"]["enum"], [
            "province_policy",
            "score_table",
            "batch_admission",
            "joy_report",
            "enrollment_plan",
            "admission_charter",
            "tuition_fee",
            "subject_requirement",
            "strong_foundation",
            "comprehensive_evaluation",
            "hk_macao_admission",
            "special_pathway",
        ])
        self.assertEqual(self.schema["properties"]["tasks"]["minItems"], 32)
        self.assertEqual(
            task["properties"]["max_candidates"],
            {"type": "integer", "const": 10},
        )
        self.assertEqual(
            task["properties"]["preferred_source_tiers"],
            {
                "type": "array",
                "prefixItems": [{"const": "A"}, {"const": "B"}, {"const": "C"}],
                "items": False,
                "minItems": 3,
                "maxItems": 3,
            },
        )
        self.assertRegex(
            self.schema["x-semantic-validator"]["locator"],
            r"^scripts\.query_plan\.validate_query_plan_payload$",
        )
        self.assertEqual(
            set(self.schema["x-semantic-validator"]["checks"]),
            {
                "task_id_digest",
                "task_context",
                "catalog_discovery_context",
                "trusted_catalog_identity",
                "catalog_digest_binding",
                "decision_policy_binding",
                "research_year_binding",
                "structured_target",
                "explicit_four_year_window",
                "strict_year_order",
                "availability_expectation",
                "candidate_limit",
                "retry_stop_bounds",
                "source_tiers",
                "source_policy_reference",
                "pathway_trace",
                "pathway_task_binding",
                "extraction_fields",
            },
        )

    def test_semantic_validator_resolves_for_package_and_flat_imports_without_io(self):
        package_module = importlib.import_module("scripts.query_plan")
        flat_module = importlib.import_module("query_plan")
        configs = discover_provinces(ROOT / "tests" / "fixtures" / "provinces")
        catalog = load_province_catalog()
        config = replace(configs["演示甲省"], province="黑龙江")
        profile = RecommendationProfile(
            rank=1100,
            target_province="黑龙江",
            subject_group="物理",
            secondary_subjects=frozenset({"化学", "地理"}),
        )
        payload = _build_query_plan_legacy(
            profile,
            config,
            2026,
            high_school_name="演示第一中学",
            requested_pathways=("强基计划",),
            catalog=catalog,
        ).to_dict()
        self.assertEqual(package_module.validate_query_plan_payload(payload).to_dict(), payload)
        self.assertEqual(flat_module.validate_query_plan_payload(payload).to_dict(), payload)
        self.assertEqual(validate_query_plan_payload(payload).to_dict(), payload)


class QueryPlanCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile_payload = reference_payload()
        cls.profile_payload.update(
            {
                "province": "湖北",
                "subject_mode": "3+1+2",
                "subject_group": "物理",
                "secondary_subjects": ["地理", "化学"],
                "exam_year": 2028,
            }
        )
        cls.profile_bytes = json.dumps(
            cls.profile_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def run_cli(
        self, *arguments: str, stdin: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "query_plan.py"), *arguments],
            cwd=ROOT,
            input=self.profile_bytes if stdin is None else stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_real_cli_success_is_twice_byte_identical_and_semantically_valid(self):
        first = self.run_cli()
        second = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8", "replace"))
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", "replace"))
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout.decode("utf-8"))
        self.assertEqual(validate_query_plan_payload(payload).to_dict(), payload)
        self.assertEqual(payload["province"], "湖北")
        self.assertEqual(payload["exam_year"], 2028)
        self.assertEqual(payload["research_year"], query_plan_module._current_utc_year())
        self.assertLessEqual(max(task["year"] for task in payload["tasks"]), payload["research_year"])
        self.assertEqual(payload["subject_group"], "物理+化学+地理")
        self.assertNotIn("张三", first.stdout.decode("utf-8"))

    def test_old_province_file_year_and_profile_path_arguments_are_rejected(self):
        cases = (
            ("--province", "C:\\private\\province.json"),
            ("--exam-year", "2029"),
            ("--profile", "C:\\private\\profile.json"),
            ("C:\\private\\profile.json",),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                error = result.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(error, "query-plan: invalid input\n")
                self.assertNotIn("private", error)

    def test_cli_invalid_inputs_exit_two_without_path_or_private_input_leakage(self):
        cases = (
            b'{"duplicate": 1, "duplicate": 2}',
            b'{"rank": NaN}',
            b'\xff\xfe',
        )
        for content in cases:
            with self.subTest(content=content):
                result = self.run_cli(stdin=content)
                error = result.stderr.decode("utf-8", "replace")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(error.replace("\r\n", "\n"), "query-plan: invalid input\n")
                self.assertNotIn("NaN", error)

        v2 = v2_payload()
        result = self.run_cli(
            stdin=json.dumps(v2, ensure_ascii=False).encode("utf-8")
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr.decode("utf-8", "replace").replace("\r\n", "\n"),
            "query-plan: invalid input\n",
        )

    def test_all_argument_parse_errors_are_fixed_and_path_neutral(self):
        cases = (
            ["--unknown-secret", "姓名:张三"],
            ["C:\\private\\extra.txt"],
            ["--high-school"],
            ["--pathway", "强基计划"],
        )
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli(*extra)
                error = result.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(error, "query-plan: invalid input\n")

    def test_missing_local_dependencies_and_old_python_are_real_exit_three(self):
        source = ROOT / "scripts" / "query_plan.py"
        dependency_sets = ((), ("contracts.py", "path_recommend.py"))
        for dependencies in dependency_sets:
            with self.subTest(dependencies=dependencies), tempfile.TemporaryDirectory() as temp:
                isolated = Path(temp)
                shutil.copyfile(source, isolated / "query_plan.py")
                for dependency in dependencies:
                    shutil.copyfile(ROOT / "scripts" / dependency, isolated / dependency)
                environment = dict(os.environ)
                environment.pop("PYTHONPATH", None)
                result = subprocess.run(
                    [sys.executable, str(isolated / "query_plan.py")],
                    cwd=isolated,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                error = result.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
                self.assertEqual(result.returncode, 3)
                self.assertEqual(error, "query-plan: missing capability\n")
                self.assertNotIn(str(isolated), error)
                self.assertNotIn("Traceback", error)

        old_python = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import runpy,sys;"
                    "sys.version_info=(3,9,18);"
                    "sys.path.insert(0,'scripts');"
                    "sys.argv=['query_plan.py'];"
                    "runpy.run_path('scripts/query_plan.py',run_name='__main__')"
                ),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        error = old_python.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
        self.assertEqual(old_python.returncode, 3)
        self.assertEqual(error, "query-plan: missing capability\n")

    def test_internal_module_import_errors_are_not_misreported_as_capabilities(self):
        with tempfile.TemporaryDirectory() as temp:
            isolated = Path(temp)
            shutil.copyfile(ROOT / "scripts" / "query_plan.py", isolated / "query_plan.py")
            (isolated / "contracts.py").write_text(
                "import query_plan_internal_missing_sentinel\n", encoding="utf-8"
            )
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(isolated / "query_plan.py")],
                cwd=isolated,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            error = result.stderr.decode("utf-8", "replace")
            self.assertNotEqual(result.returncode, 3)
            self.assertIn("query_plan_internal_missing_sentinel", error)


if __name__ == "__main__":
    unittest.main()
