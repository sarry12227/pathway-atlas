import csv
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from dataclasses import FrozenInstanceError
from pathlib import Path

from scripts.data_loader import DataError, load_admission_rows, load_toudang
from scripts import validate_data as validator_module
from scripts.validate_data import (
    ValidatedAdmissionRow,
    ValidatedDatasetSnapshot,
    ValidatedScoreRow,
    ValidationIssue,
    admission_row_hash,
    validate_dataset,
    validate_dataset_snapshot,
    validate_runtime_admission_row,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "provinces"
VALIDATOR = ROOT / "scripts" / "validate_data.py"


def strict_metadata(province="测试省", mode="3+1+2", score_scale=750):
    if mode == "3+1+2":
        primary = ["物理", "历史"]
        secondary = ["化学", "生物", "思想政治", "地理"]
    else:
        primary = ["物理", "历史", "化学"]
        secondary = ["生物", "思想政治", "地理"]
    return {
        "province": province,
        "mode": mode,
        "primary_subjects": primary,
        "secondary_subjects": secondary,
        "score_scale": score_scale,
        "schema_version": "1.0",
        "ordinary_batch_policy": {
            "schema_version": "1.0",
            "policy_id": "synthetic-ordinary-batch-v1",
            "basis_id": "synthetic-policy-basis-v1",
            "search_delta_min": -8000,
            "search_delta_max": 6000,
            "challenge_delta_lt": -2000,
            "stable_delta_le": 2000,
            "tier_caps": {"冲": 3, "稳": 4, "保": 5},
        },
    }


def write_csv(path, headers, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


class ValidationContractTest(unittest.TestCase):
    def test_runtime_admission_row_authenticates_optional_decision_metadata(self):
        payload = {
            "year": 2026,
            "province": "测试省",
            "subject_group": "物理",
            "school_code": "A01",
            "school_name": "合成武汉大学",
            "program_group": "第01组",
            "min_score": 600,
            "min_rank": 1000,
            "remarks": "",
            "city_location": "武汉",
            "school_province": "湖北",
            "majors_in_group": ("人工智能", "计算机科学与技术"),
            "institution_type": "public",
            "affordable_for": ("limited", "moderate", "flexible"),
            "adjustment_required": False,
        }

        row = validate_runtime_admission_row(
            payload,
            province="测试省",
            subject_group="物理",
            score_scale=750,
            allowed_years=(2026,),
        )

        self.assertIsInstance(row, ValidatedAdmissionRow)
        self.assertEqual(row.to_dict(), payload)
        changed = dict(payload)
        changed["city_location"] = "上海"
        changed_row = validate_runtime_admission_row(
            changed,
            province="测试省",
            subject_group="物理",
            score_scale=750,
            allowed_years=(2026,),
        )
        self.assertNotEqual(admission_row_hash(row), admission_row_hash(changed_row))

    def test_runtime_admission_decision_metadata_is_strict_and_unknown_is_absent(self):
        base = {
            "year": 2026,
            "province": "测试省",
            "subject_group": "物理",
            "school_code": "A01",
            "school_name": "合成大学",
            "program_group": "第01组",
            "min_score": 600,
            "min_rank": 1000,
            "remarks": "",
        }
        unknown = validate_runtime_admission_row(
            base,
            province="测试省",
            subject_group="物理",
            score_scale=750,
            allowed_years=(2026,),
        )
        self.assertEqual(unknown.to_dict(), base)

        invalid_values = {
            "city_location": " 武汉",
            "school_province": "湖北\n",
            "majors_in_group": ["计算机科学与技术"],
            "institution_type": "unknown",
            "affordable_for": ("flexible", "limited"),
            "adjustment_required": 0,
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                payload = dict(base)
                payload[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    validate_runtime_admission_row(
                        payload,
                        province="测试省",
                        subject_group="物理",
                        score_scale=750,
                        allowed_years=(2026,),
                    )
                with self.assertRaises((TypeError, ValueError)):
                    ValidatedAdmissionRow.from_mapping(payload)

    def test_validated_snapshot_is_authenticated_deep_frozen_and_detached(self):
        result = validate_dataset_snapshot((FIXTURES / "demo-312").resolve())

        self.assertEqual(result.issues, ())
        self.assertIsInstance(result.snapshot, ValidatedDatasetSnapshot)
        snapshot = result.snapshot
        assert snapshot is not None
        self.assertEqual(snapshot.config.province, "演示甲省")
        self.assertEqual(snapshot.admission_rows[0].to_dict()["school_name"], "虚构甲大学")
        self.assertTrue(snapshot.score_rows)
        self.assertIsInstance(snapshot.score_rows[0], ValidatedScoreRow)
        self.assertEqual(
            snapshot.score_rows[0].to_dict(),
            {
                "year": 2026,
                "score": 650,
                "rank": 1000,
                "cumulative_count": 1000,
                "subject_group": "物理",
            },
        )
        with self.assertRaises(FrozenInstanceError):
            snapshot.admission_rows = ()
        with self.assertRaises(FrozenInstanceError):
            snapshot.score_rows = ()
        payload = snapshot.admission_rows[0].to_dict()
        payload["school_name"] = "外部篡改"
        self.assertEqual(snapshot.admission_rows[0].to_dict()["school_name"], "虚构甲大学")
        score_payload = snapshot.score_rows[0].to_dict()
        score_payload["rank"] = 1
        self.assertEqual(snapshot.score_rows[0].to_dict()["rank"], 1000)

    def test_invalid_dataset_snapshot_fails_closed_without_data(self):
        result = validate_dataset_snapshot((FIXTURES / "duplicate-program").resolve())

        self.assertIsNone(result.snapshot)
        self.assertIn("duplicate_admission_key", {issue.code for issue in result.issues})

    def test_snapshot_survives_post_validation_same_name_file_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            shutil.copytree(FIXTURES / "demo-312", directory)
            result = validate_dataset_snapshot(directory.resolve())
            self.assertEqual(result.issues, ())
            assert result.snapshot is not None

            replacement = directory / "replacement.csv"
            write_csv(
                replacement,
                ("year", "province", "subject_group", "school_code", "school_name", "program_group", "min_score", "min_rank", "remarks"),
                ((2026, "演示甲省", "物理", "EVIL", "替换大学", "第99组", 1, 999999, ""),),
            )
            authenticated = directory / "authenticated.csv"
            (directory / "tou_dang.csv").rename(authenticated)
            replacement.rename(directory / "tou_dang.csv")

            self.assertEqual(
                result.snapshot.admission_rows[0].to_dict()["school_name"],
                "虚构甲大学",
            )

    def test_snapshot_same_name_dataset_and_csv_races_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "dataset"
            moved = root / "authenticated-dataset"
            shutil.copytree(FIXTURES / "demo-312", directory)

            def replace_dataset():
                directory.rename(moved)
                directory.mkdir()
                shutil.copy2(moved / "tou_dang.csv", directory / "tou_dang.csv")

            dataset_result = validator_module._validate_dataset_snapshot(
                directory.resolve(), operation_hook=replace_dataset
            )
            self.assertIsNone(dataset_result.snapshot)
            self.assertEqual([issue.code for issue in dataset_result.issues], ["dataset_path_changed"])

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            shutil.copytree(FIXTURES / "demo-312", directory)
            original = directory / "authenticated-tou_dang.csv"

            def replace_table(table, path):
                if table == "tou_dang":
                    path.rename(original)
                    shutil.copy2(original, path)

            file_result = validator_module._validate_dataset_snapshot(
                directory.resolve(), table_operation_hook=replace_table
            )
            self.assertIsNone(file_result.snapshot)
            self.assertIn("data_file_changed", {issue.code for issue in file_result.issues})

    def test_issue_is_immutable_serializable_and_has_stable_fields(self):
        issue = ValidationIssue(
            code="invalid_integer",
            message="整数格式无效",
            table="yifenyiduan",
            path="C:/fixture/yifenyiduan.csv",
            row=2,
            field="score",
        )
        self.assertEqual(
            issue.to_dict(),
            {
                "code": "invalid_integer",
                "message": "整数格式无效",
                "table": "yifenyiduan",
                "path": "C:/fixture/yifenyiduan.csv",
                "row": 2,
                "field": "score",
            },
        )
        json.dumps(issue.to_dict(), ensure_ascii=False)
        with self.assertRaises(FrozenInstanceError):
            issue.code = "changed"

    def test_both_reform_modes_validate_from_province_config(self):
        self.assertEqual(validate_dataset(FIXTURES / "demo-312"), [])
        self.assertEqual(validate_dataset(FIXTURES / "demo-33"), [])

    def test_score_scale_and_subject_group_are_configuration_driven(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata("量表省", "3+3", 660), ensure_ascii=False),
                encoding="utf-8",
            )
            write_csv(
                directory / "yifenyiduan.csv",
                ("year", "score", "rank", "cumulative_count", "subject_group"),
                ((2026, 661, 1, 1, "物理+化学+地理"), (2026, 600, 2, 2, "物理+物理+地理")),
            )
            issues = validate_dataset(directory.resolve())
            self.assertEqual(
                [(item.code, item.row, item.field) for item in issues],
                [("score_out_of_range", 2, "score"), ("invalid_subject_group", 3, "subject_group")],
            )

    def test_dataset_subject_group_requires_the_mode_aware_canonical_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )
            write_csv(
                directory / "yifenyiduan.csv",
                ("year", "score", "rank", "cumulative_count", "subject_group"),
                ((2026, 600, 1, 1, "物理+化学+地理"),),
            )
            issues = validate_dataset(directory.resolve())
            self.assertEqual(
                [(item.code, item.field) for item in issues],
                [("invalid_subject_group", "subject_group")],
            )

    def test_data_errors_are_returned_in_deterministic_order_without_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )
            write_csv(
                directory / "yifenyiduan.csv",
                ("year", "score", "rank", "cumulative_count", "subject_group"),
                ((" 2026", "1.0", 0, 0, "技术"),),
            )
            issues = validate_dataset(directory.resolve())
            self.assertEqual(issues, sorted(issues, key=lambda item: item.sort_key()))
            self.assertTrue(
                {
                    ("invalid_integer", "year"),
                    ("invalid_integer", "score"),
                    ("rank_out_of_range", "rank"),
                    ("invalid_subject_group", "subject_group"),
                }.issubset({(item.code, item.field) for item in issues}),
            )
            self.assertNotIn("1.0", json.dumps([item.to_dict() for item in issues], ensure_ascii=False))

    def test_empty_duplicate_headers_and_missing_known_files_are_controlled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = []
            for name in ("empty", "duplicate-header", "no-data"):
                directory = root / name
                directory.mkdir()
                (directory / "province.json").write_text(
                    json.dumps(strict_metadata(name), ensure_ascii=False), encoding="utf-8"
                )
                cases.append(directory)
            (cases[0] / "yifenyiduan.csv").write_text("", encoding="utf-8")
            (cases[1] / "yifenyiduan.csv").write_text(
                "year,score,score,rank,cumulative_count,subject_group\n2026,600,600,1,1,物理\n",
                encoding="utf-8",
            )
            self.assertIn("empty_file", {item.code for item in validate_dataset(cases[0].resolve())})
            self.assertIn("duplicate_header", {item.code for item in validate_dataset(cases[1].resolve())})
            self.assertIn("no_known_data_files", {item.code for item in validate_dataset(cases[2].resolve())})

    def test_relative_directory_and_linked_csv_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            directory = Path(temporary)
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )
            relative = Path(os.path.relpath(directory, Path.cwd()))
            self.assertIn("unsafe_dataset_path", {item.code for item in validate_dataset(relative)})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "dataset"
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )
            external = root / "external.csv"
            write_csv(external, ("year", "score", "rank", "cumulative_count", "subject_group"), ((2026, 600, 1, 1, "物理"),))
            try:
                (directory / "yifenyiduan.csv").symlink_to(external)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink unavailable: {error}")
            self.assertIn("unsafe_data_file", {item.code for item in validate_dataset(directory.resolve())})

    def test_same_name_dataset_replacement_after_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "dataset"
            moved = root / "authenticated-dataset"
            shutil.copytree(FIXTURES / "demo-312", directory)

            def replace_after_config():
                directory.rename(moved)
                directory.mkdir()
                shutil.copy2(moved / "yifenyiduan.csv", directory / "yifenyiduan.csv")
                shutil.copy2(moved / "tou_dang.csv", directory / "tou_dang.csv")

            issues = validator_module._validate_dataset(
                directory.resolve(), operation_hook=replace_after_config
            )

            self.assertEqual([item.code for item in issues], ["dataset_path_changed"])
            self.assertNotIn("演示甲省", json.dumps([item.to_dict() for item in issues], ensure_ascii=False))

    def test_same_name_csv_replacement_between_identity_check_and_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            shutil.copytree(FIXTURES / "demo-312", directory)
            original = directory / "original-yifenyiduan.csv"

            def replace_table(table, path):
                if table == "yifenyiduan":
                    path.rename(original)
                    shutil.copy2(original, path)

            issues = validator_module._validate_dataset(
                directory.resolve(), table_operation_hook=replace_table
            )

            self.assertIn("data_file_changed", {item.code for item in issues})


class AdmissionNormalizationTest(unittest.TestCase):
    def test_csv_under_stable_symlinked_ancestor_uses_canonical_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            real_parent = sandbox / "real-parent"
            data_directory = real_parent / "dataset"
            data_directory.mkdir(parents=True)
            path = data_directory / "tou_dang.csv"
            write_csv(
                path,
                ("year", "subject_group", "min_score", "min_rank"),
                ((2026, "物理", 600, 1000),),
            )
            alias = sandbox / "system-alias"
            try:
                alias.symlink_to(real_parent, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink unavailable: {type(error).__name__}")

            rows = load_admission_rows(alias / "dataset" / "tou_dang.csv")

            self.assertEqual(rows[0]["min_rank"], "1000")

    def test_legacy_aliases_normalize_to_one_canonical_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tou_dang.csv"
            write_csv(
                path,
                ("year", "province", "subject_group", "school_code", "school_name", "major_group_name", "min_score", "min_rank", "remark"),
                ((2026, "测试省", "物理", "S01", "虚构大学", "第01组", 600, 1000, "中外合作"),),
            )
            self.assertEqual(
                load_admission_rows(path)[0],
                {
                    "year": "2026",
                    "province": "测试省",
                    "subject_group": "物理",
                    "school_code": "S01",
                    "school_name": "虚构大学",
                    "program_group": "第01组",
                    "min_score": "600",
                    "min_rank": "1000",
                    "remarks": "中外合作",
                },
            )

    def test_conflicting_aliases_raise_controlled_error(self):
        conflicting_headers = (
            ("remarks", "remark", "正式", "旧值"),
            ("program_group", "major_group_name", "第01组", "第02组"),
        )
        for canonical, alias, canonical_value, alias_value in conflicting_headers:
            with self.subTest(canonical=canonical), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "tou_dang.csv"
                write_csv(path, (canonical, alias), ((canonical_value, alias_value),))
                with self.assertRaisesRegex(DataError, "冲突"):
                    load_admission_rows(path)

    def test_load_toudang_keeps_one_release_major_group_alias(self):
        year, rows = load_toudang(
            None,
            "物理",
            province_dir=(FIXTURES / "demo-312").resolve(),
        )
        self.assertEqual(year, 2026)
        self.assertEqual(rows[0]["program_group"], rows[0]["major_group_name"])
        self.assertNotIn("remark", rows[0])

    def test_duplicate_key_uses_school_code_program_group_and_remarks(self):
        issues = validate_dataset((FIXTURES / "duplicate-program").resolve())
        duplicates = [item for item in issues if item.code == "duplicate_admission_key"]
        self.assertEqual([(item.row, item.field) for item in duplicates], [(5, None)])

    def test_school_code_has_one_stable_name_per_province_and_year(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )
            headers = (
                "year", "province", "subject_group", "school_code", "school_name",
                "program_group", "min_score", "min_rank", "remarks",
            )
            write_csv(
                directory / "tou_dang.csv",
                headers,
                (
                    (2026, "测试省", "物理", "A01", "虚构甲大学", "第01组", 600, 1000, ""),
                    (2026, "测试省", "物理", "A02", "虚构甲大学", "第01组", 600, 1000, ""),
                    (2027, "测试省", "物理", "A01", "虚构乙大学", "第01组", 600, 1000, ""),
                    (2026, "测试省", "物理", "A01", "虚构甲大学", "第02组", 601, 999, ""),
                    (2026, "测试省", "物理", "A01", "虚构甲大学", "第03组", 602, 998, "专项"),
                    (2026, "测试省", "物理", "A01", "冲突名称", "第04组", 603, 997, ""),
                ),
            )

            conflicts = [
                item for item in validate_dataset(directory.resolve())
                if item.code == "conflicting_school_identity"
            ]

            self.assertEqual([(item.row, item.field) for item in conflicts], [(7, "school_name")])
            serialized = json.dumps([item.to_dict() for item in conflicts], ensure_ascii=False)
            self.assertNotIn("A01", serialized)
            self.assertNotIn("冲突名称", serialized)


class ValidatorCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.fspath(VALIDATOR), *map(os.fspath, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def assert_cli_paths_are_logical(self, process, *, private_markers=()):
        self.assertNotIn("Traceback", process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["directory"], ".")
        allowed_paths = {
            ".",
            "province.json",
            *(f"{table}.csv" for table in validator_module._KNOWN_TABLES),
        }
        self.assertLessEqual(
            {issue["path"] for issue in payload["issues"]},
            allowed_paths,
        )
        visible = process.stdout + process.stderr
        for forbidden in (
            *private_markers,
            str(ROOT),
            ".worktrees",
            "C:",
            "\\",
            "/home/",
        ):
            self.assertNotIn(forbidden, visible)

    def test_positional_valid_fixture_exits_zero_with_json_output(self):
        process = self.run_cli(FIXTURES / "demo-33")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["issues"], [])
        self.assert_cli_paths_are_logical(process, private_markers=("demo-33",))

    def test_deprecated_switch_uses_same_validator(self):
        process = self.run_cli("--province-dir", FIXTURES / "demo-312")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(json.loads(process.stdout)["valid"])
        self.assert_cli_paths_are_logical(process, private_markers=("demo-312",))

    def test_invalid_fixture_exits_two_and_reports_real_line(self):
        process = self.run_cli(FIXTURES / "duplicate-program")
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        duplicate = next(item for item in payload["issues"] if item["code"] == "duplicate_admission_key")
        self.assertEqual(duplicate["row"], 5)
        self.assertEqual(duplicate["path"], "tou_dang.csv")
        self.assert_cli_paths_are_logical(
            process, private_markers=("duplicate-program",)
        )

    def test_valid_and_invalid_absolute_pii_directories_are_not_serialized(self):
        marker = "学生张三13800138000"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / f"{marker}-valid"
            invalid = root / f"{marker}-invalid"
            shutil.copytree(FIXTURES / "demo-33", valid)
            shutil.copytree(FIXTURES / "duplicate-program", invalid)

            valid_process = self.run_cli(valid.resolve())
            invalid_process = self.run_cli(invalid.resolve())

        self.assertEqual(valid_process.returncode, 0, valid_process.stdout + valid_process.stderr)
        self.assertEqual(invalid_process.returncode, 2, invalid_process.stdout + invalid_process.stderr)
        self.assert_cli_paths_are_logical(valid_process, private_markers=(marker,))
        self.assert_cli_paths_are_logical(invalid_process, private_markers=(marker,))

    def test_file_level_csv_failures_project_generic_cli_messages(self):
        marker = "学生张三13800138000"
        expected_messages = {
            "duplicate_header": "文件级错误：CSV 表头包含重复字段",
            "unsafe_data_file": "文件级错误：CSV 无法安全读取",
            "data_file_changed": "文件级错误：CSV 在读取期间发生变化",
            "invalid_csv": "文件级错误：CSV 必须是严格 UTF-8 且格式有效",
        }
        for code, expected in expected_messages.items():
            with self.subTest(code=code):
                issue = ValidationIssue(
                    code=code,
                    message=f"文件级错误：泄漏 C:/private/{marker}/tou_dang.csv",
                    table="tou_dang",
                    path=f"C:/private/{marker}/tou_dang.csv",
                )
                projected = validator_module._cli_issue_dict(issue)
                self.assertEqual(projected["message"], expected)
                self.assertEqual(projected["path"], "tou_dang.csv")
                self.assertNotIn(marker, json.dumps(projected, ensure_ascii=False))
                # Programmatic diagnostics remain exact and useful.
                self.assertIn(marker, json.dumps(issue.to_dict(), ensure_ascii=False))

    def test_real_malformed_csvs_under_private_directories_do_not_leak(self):
        marker = "学生张三13800138000"
        cases = (
            (
                "duplicate",
                "duplicate_header",
                "文件级错误：CSV 表头包含重复字段",
            ),
            (
                "invalid-utf8",
                "invalid_csv",
                "文件级错误：CSV 必须是严格 UTF-8 且格式有效",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = []
            for suffix, expected_code, expected_message in cases:
                directory = root / f"{marker}-{suffix}"
                shutil.copytree(FIXTURES / "demo-312", directory)
                csv_path = directory / "tou_dang.csv"
                if suffix == "duplicate":
                    write_csv(
                        csv_path,
                        ("year", "province", "province"),
                        ((2026, "演示甲省", "演示甲省"),),
                    )
                else:
                    csv_path.write_bytes(b"\xff\xfe")
                results.append(
                    (
                        self.run_cli(directory.resolve()),
                        expected_code,
                        expected_message,
                    )
                )

        for process, expected_code, expected_message in results:
            with self.subTest(code=expected_code):
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                payload = json.loads(process.stdout)
                issue = next(
                    item for item in payload["issues"]
                    if item["code"] == expected_code
                )
                self.assertEqual(issue["message"], expected_message)
                self.assert_cli_paths_are_logical(process, private_markers=(marker,))

    def test_mocked_file_race_keeps_cli_json_private(self):
        marker = "学生张三13800138000"
        private_path = f"C:/private/{marker}/tou_dang.csv"
        race = ValidationIssue(
            code="data_file_changed",
            message=f"文件级错误：CSV 在读取期间发生变化：{private_path}",
            table="tou_dang",
            path=private_path,
        )
        output = io.StringIO()
        with mock.patch.object(validator_module, "validate_dataset", return_value=[race]):
            with contextlib.redirect_stdout(output):
                returncode = validator_module.main([private_path])

        self.assertEqual(returncode, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["directory"], ".")
        self.assertEqual(payload["issues"][0]["path"], "tou_dang.csv")
        self.assertEqual(
            payload["issues"][0]["message"],
            "文件级错误：CSV 在读取期间发生变化",
        )
        self.assertNotIn(marker, output.getvalue())
        self.assertNotIn("C:/", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_dataset_and_unknown_issue_paths_use_the_stable_logical_fallback(self):
        marker = "学生张三13800138000"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / marker
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )

            process = self.run_cli(directory.resolve())

        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        dataset_issue = next(
            issue for issue in payload["issues"] if issue["code"] == "no_known_data_files"
        )
        self.assertEqual(dataset_issue["path"], ".")
        self.assert_cli_paths_are_logical(process, private_markers=(marker,))

        unknown = ValidationIssue(
            code="synthetic_unknown",
            message="合成未知表错误",
            table="not-a-public-table",
            path=f"C:/private/{marker}/secret.csv",
        )
        self.assertEqual(validator_module._cli_issue_dict(unknown)["path"], ".")
        # The programmatic API retains the exact diagnostic path.
        self.assertEqual(unknown.to_dict()["path"], f"C:/private/{marker}/secret.csv")

    def test_conflicting_school_identity_exits_two_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "dataset"
            directory.mkdir()
            (directory / "province.json").write_text(
                json.dumps(strict_metadata(), ensure_ascii=False), encoding="utf-8"
            )
            write_csv(
                directory / "tou_dang.csv",
                ("year", "province", "subject_group", "school_code", "school_name", "program_group", "min_score", "min_rank", "remarks"),
                (
                    (2026, "测试省", "物理", "SECRET-CODE", "不应回显甲", "第01组", 600, 1000, ""),
                    (2026, "测试省", "物理", "SECRET-CODE", "不应回显乙", "第02组", 601, 999, ""),
                ),
            )

            process = self.run_cli(directory)

            self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
            payload = json.loads(process.stdout)
            conflict = next(item for item in payload["issues"] if item["code"] == "conflicting_school_identity")
            self.assertEqual(conflict["row"], 3)
            self.assertNotIn("SECRET-CODE", process.stdout)
            self.assertNotIn("不应回显", process.stdout)
            self.assert_cli_paths_are_logical(
                process, private_markers=(directory.name,)
            )


if __name__ == "__main__":
    unittest.main()
