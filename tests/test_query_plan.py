from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from contracts import RecommendationProfile  # noqa: E402
from province_registry import discover_provinces  # noqa: E402
from query_plan import (  # noqa: E402
    QueryPlan,
    QueryTask,
    build_query_plan,
    validate_query_plan_payload,
)


class QueryPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.configs = discover_provinces(ROOT / "tests" / "fixtures" / "provinces")

    def profile_312(self, **changes) -> RecommendationProfile:
        values = {
            "rank": 1100,
            "target_province": "演示甲省",
            "subject_group": "物理",
            "secondary_subjects": frozenset({"化学", "地理"}),
        }
        values.update(changes)
        return RecommendationProfile(**values)

    def profile_33(self, **changes) -> RecommendationProfile:
        values = {
            "rank": 2100,
            "target_province": "演示乙市",
            "subject_group": "化学",
            "secondary_subjects": frozenset({"物理", "生物"}),
        }
        values.update(changes)
        return RecommendationProfile(**values)

    def build_312(self, **changes) -> QueryPlan:
        values = {
            "profile": self.profile_312(),
            "province": self.configs["演示甲省"],
            "exam_year": 2026,
            "high_school_name": "演示第一中学",
            "requested_pathways": ["强基计划", "综合评价"],
        }
        values.update(changes)
        return build_query_plan(**values)

    def test_plan_covers_all_required_query_kinds(self):
        kinds = {task.kind for task in self.build_312().tasks}
        self.assertEqual(
            kinds,
            {"score_table", "admission", "joy_report", "pathway_policy"},
        )

    def test_candidate_limit_and_tiers_are_non_negotiable(self):
        for task in self.build_312().tasks:
            self.assertEqual(task.max_candidates, 10)
            self.assertEqual(task.preferred_source_tiers, ("A", "B", "C"))

    def test_admission_years_are_exactly_three_explicit_years(self):
        tasks = [task for task in self.build_312().tasks if task.kind == "admission"]
        self.assertEqual({task.year for task in tasks}, {2024, 2025, 2026})
        self.assertNotIn("latest", json.dumps(self.build_312().to_dict()))

    def test_mode_aware_subject_keys_and_ids_are_deterministic_safe_ascii(self):
        first = self.build_312()
        second = self.build_312()
        self.assertEqual(first.subject_group, "物理")
        self.assertEqual(first.to_dict(), second.to_dict())

        plan_33 = build_query_plan(
            self.profile_33(),
            self.configs["演示乙市"],
            2026,
            high_school_name="演示第二中学",
            requested_pathways=("强基计划",),
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

    def test_current_year_is_not_yet_expected_and_older_years_are_explicit(self):
        plan = self.build_312()
        current = [task for task in plan.tasks if task.year == 2026]
        older = [task for task in plan.tasks if task.year < 2026]
        self.assertTrue(current)
        self.assertTrue(older)
        self.assertTrue(
            all(task.availability_expectation == "current_year_not_yet_expected" for task in current)
        )
        self.assertTrue(
            all(task.availability_expectation == "explicit_older_year" for task in older)
        )

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
            "演示甲省",
            "演示甲省教育考试院",
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

    def test_optional_school_and_pathways_omit_only_dependent_tasks(self):
        plan = build_query_plan(
            self.profile_312(), self.configs["演示甲省"], 2026
        )
        self.assertEqual(
            {task.kind for task in plan.tasks}, {"score_table", "admission"}
        )

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

    def test_exam_year_is_a_finite_mathematical_integer_and_normalized(self):
        self.assertEqual(self.build_312(exam_year=2026.0).exam_year, 2026)
        for value in (True, "2026", 2026.5, float("nan"), float("inf"), 1999, 2101):
            with self.subTest(exam_year=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.build_312(exam_year=value)

    def test_requires_real_profile_and_province_and_matching_context(self):
        with self.assertRaises(TypeError):
            build_query_plan({}, self.configs["演示甲省"], 2026)
        with self.assertRaises(TypeError):
            build_query_plan(self.profile_312(), {}, 2026)
        with self.assertRaises(ValueError):
            build_query_plan(self.profile_312(), self.configs["演示乙市"], 2026)

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
                query_variants="not-a-collection",
                preferred_source_tiers=task.preferred_source_tiers,
                max_candidates=task.max_candidates,
                freshness_rule=task.freshness_rule,
                required_extraction_fields=task.required_extraction_fields,
                availability_expectation=task.availability_expectation,
            )
        with self.assertRaises(ValueError):
            replace(self.build_312(), exam_year=2027)


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
            {"schema_version", "province", "exam_year", "subject_group", "tasks"},
        )
        task = self.schema["$defs"]["queryTask"]
        self.assertFalse(task["additionalProperties"])
        self.assertEqual(task["properties"]["kind"]["enum"], [
            "score_table", "admission", "joy_report", "pathway_policy"
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

    def test_semantic_validator_resolves_for_package_and_flat_imports_without_io(self):
        package_module = importlib.import_module("scripts.query_plan")
        flat_module = importlib.import_module("query_plan")
        configs = discover_provinces(ROOT / "tests" / "fixtures" / "provinces")
        profile = RecommendationProfile(
            rank=1100,
            target_province="演示甲省",
            subject_group="物理",
            secondary_subjects=frozenset({"化学", "地理"}),
        )
        payload = build_query_plan(
            profile,
            configs["演示甲省"],
            2026,
            high_school_name="演示第一中学",
            requested_pathways=("强基计划",),
        ).to_dict()
        self.assertEqual(package_module.validate_query_plan_payload(payload).to_dict(), payload)
        self.assertEqual(flat_module.validate_query_plan_payload(payload).to_dict(), payload)
        self.assertEqual(validate_query_plan_payload(payload).to_dict(), payload)


class QueryPlanCliTest(unittest.TestCase):
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
            "--profile", str(ROOT / "tests" / "fixtures" / "profiles" / "demo.json"),
            "--province", str(ROOT / "tests" / "fixtures" / "provinces" / "demo-312" / "province.json"),
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


if __name__ == "__main__":
    unittest.main()
