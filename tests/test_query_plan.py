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

from contracts import OrdinaryBatchPolicy, RecommendationProfile  # noqa: E402
from province_registry import discover_provinces  # noqa: E402
from query_plan import (  # noqa: E402
    ProvinceCatalogSnapshot,
    QueryPlan,
    QueryTask,
    build_query_plan,
    load_province_catalog,
    validate_query_plan_payload,
)


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
        return build_query_plan(**values)

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
                "subject_requirement",
                "strong_foundation",
                "comprehensive_evaluation",
                "hk_macao_admission",
            },
        )

    def test_catalog_backed_plan_has_independent_research_families(self):
        from query_plan import load_province_catalog

        catalog = load_province_catalog()
        base = self.configs["演示甲省"]
        province = replace(base, province="黑龙江")
        profile = self.profile_312(target_province="黑龙江")

        plan = build_query_plan(
            profile,
            province,
            2026,
            high_school_name="演示第一中学",
            requested_pathways=("定向培养",),
            catalog=catalog,
        )

        self.assertEqual(plan.authority_name, "黑龙江省招生考试院")
        self.assertEqual(plan.official_roots, ("https://www.hljea.org.cn/",))
        kinds = {task.kind for task in plan.tasks}
        self.assertTrue(
            {
                "province_policy",
                "score_table",
                "batch_admission",
                "joy_report",
                "enrollment_plan",
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
                plan = build_query_plan(profile, config, 2026, catalog=catalog)
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
        self.assertTrue(all(task.target_name is None for task in plan.tasks if task.kind == "score_table"))

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

    def test_admission_years_are_exactly_three_explicit_years(self):
        tasks = [task for task in self.build_312().tasks if task.kind == "batch_admission"]
        self.assertEqual({task.year for task in tasks}, {2024, 2025, 2026})
        self.assertEqual({task.target_name for task in tasks}, {"普通批", "提前批", "综合评价批"})
        self.assertNotIn("latest", json.dumps(self.build_312().to_dict()))

    def test_mode_aware_subject_keys_and_ids_are_deterministic_safe_ascii(self):
        first = self.build_312()
        second = self.build_312()
        self.assertEqual(first.subject_group, "物理")
        self.assertEqual(first.to_dict(), second.to_dict())

        plan_33 = build_query_plan(
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
        plan = build_query_plan(
            self.profile_312(), self.config_312, 2026, catalog=self.catalog
        )
        kinds = {task.kind for task in plan.tasks}
        self.assertTrue(
            {"strong_foundation", "comprehensive_evaluation", "hk_macao_admission"}
            <= kinds
        )
        self.assertNotIn("pathway_policy", kinds)
        generic_joy = [task for task in plan.tasks if task.kind == "joy_report"]
        self.assertEqual(len(generic_joy), 3)
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
        ascii_plan = self.build_312(
            high_school_name="Café中学", requested_pathways=("A计划",)
        )
        compatibility_plan = self.build_312(
            high_school_name=unicodedata.normalize("NFD", "Café中学"),
            requested_pathways=("Ａ计划",),
        )
        self.assertEqual(ascii_plan.to_dict(), compatibility_plan.to_dict())

        ordered = self.build_312(requested_pathways=("Ｂ计划", "A计划"))
        self.assertEqual(
            [task.target_name for task in ordered.tasks if task.kind == "special_pathway"],
            ["A计划", "B计划"],
        )
        with self.assertRaises(ValueError):
            self.build_312(requested_pathways=("A计划", "Ａ计划"))

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
            build_query_plan({}, self.config_312, 2026, catalog=self.catalog)
        with self.assertRaises(TypeError):
            build_query_plan(self.profile_312(), {}, 2026, catalog=self.catalog)
        with self.assertRaises(ValueError):
            build_query_plan(self.profile_312(), self.config_33, 2026, catalog=self.catalog)

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
                    build_query_plan(self.profile_312(), invalid, 2026, catalog=self.catalog)

        policy = OrdinaryBatchPolicy(**config.ordinary_batch_policy.to_dict())
        object.__setattr__(policy, "stable_delta_le", policy.challenge_delta_lt - 1)
        with self.assertRaises(ValueError):
            build_query_plan(
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
            replace(self.build_312(), exam_year=2027)

    def test_semantic_validator_rechecks_every_task_contract_after_rehash(self):
        explicit = self.build_312().to_dict()
        generic = build_query_plan(
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
                "exam_year",
                "subject_group",
                "authority_name",
                "official_roots",
                "catalog_verified_at",
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
            "subject_requirement",
            "strong_foundation",
            "comprehensive_evaluation",
            "hk_macao_admission",
            "special_pathway",
        ])
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
                "structured_target",
                "explicit_three_year_window",
                "availability_expectation",
                "candidate_limit",
                "source_tiers",
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
        payload = build_query_plan(
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
        cls.temporary = tempfile.TemporaryDirectory()
        directory = Path(cls.temporary.name).resolve()
        profile_payload = json.loads(
            (ROOT / "tests" / "fixtures" / "profiles" / "demo.json").read_text("utf-8")
        )
        province_payload = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "provinces"
                / "demo-312"
                / "province.json"
            ).read_text("utf-8")
        )
        profile_payload["province"] = "黑龙江"
        province_payload["province"] = "黑龙江"
        cls.profile_path = directory / "profile.json"
        cls.province_path = directory / "province.json"
        cls.profile_path.write_text(
            json.dumps(profile_payload, ensure_ascii=False), encoding="utf-8"
        )
        cls.province_path.write_text(
            json.dumps(province_payload, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "query_plan.py"), *arguments],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def base_arguments(self) -> list[str]:
        return [
            "--profile", str(self.profile_path),
            "--province", str(self.province_path),
            "--exam-year", "2026",
            "--high-school", "演示第一中学",
            "--pathway", "强基计划",
            "--pathway", "综合评价",
        ]

    def test_real_cli_success_is_twice_byte_identical_and_semantically_valid(self):
        first = self.run_cli(*self.base_arguments())
        second = self.run_cli(*self.base_arguments())
        self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8", "replace"))
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", "replace"))
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout.decode("utf-8"))
        self.assertEqual(validate_query_plan_payload(payload).to_dict(), payload)
        self.assertNotIn("张三", first.stdout.decode("utf-8"))

    def test_brief_relative_cli_paths_are_twice_byte_identical_and_valid(self):
        arguments = (
            "--profile",
            os.path.relpath(self.profile_path, ROOT),
            "--province",
            os.path.relpath(self.province_path, ROOT),
            "--exam-year",
            "2026",
        )
        first = self.run_cli(*arguments)
        second = self.run_cli(*arguments)
        self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8", "replace"))
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", "replace"))
        self.assertEqual(first.stderr, b"")
        self.assertEqual(second.stderr, b"")
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout.decode("utf-8"))
        self.assertEqual(validate_query_plan_payload(payload).to_dict(), payload)

    def test_cli_invalid_inputs_exit_two_without_path_or_private_input_leakage(self):
        cases = (
            b'{"duplicate": 1, "duplicate": 2}',
            b'{"rank": NaN}',
            b'\xff\xfe',
        )
        for content in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "private-profile-location.json"
                    path.write_bytes(content)
                    args = self.base_arguments()
                    args[1] = str(path)
                    result = self.run_cli(*args)
                    error = result.stderr.decode("utf-8", "replace")
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn(str(path), error)
                    self.assertNotIn("NaN", error)

        for flag, value in (
            ("--high-school", "姓名:张三"),
            ("--high-school", "C:\\private\\school.txt"),
            ("--pathway", "https://example.com/private"),
            ("--pathway", "13800138000"),
        ):
            with self.subTest(flag=flag, value=value):
                result = self.run_cli(*self.base_arguments(), flag, value)
                error = result.stderr.decode("utf-8", "replace")
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(value, error)
                self.assertNotIn("张三", error)

    def test_all_argument_parse_errors_are_fixed_and_path_neutral(self):
        profile = str(self.profile_path)
        province = str(self.province_path)
        cases = (
            ["--unknown-secret", "姓名:张三"],
            ["C:\\private\\extra.txt"],
            ["--high-school"],
            ["--profile", profile],
            ["--province", province],
            ["--exam-year", "2026"],
            ["--high-school", "演示第一中学"],
        )
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.run_cli(*self.base_arguments(), *extra)
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
