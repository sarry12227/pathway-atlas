import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from scripts.data_loader import DataError, load_admission_rows, load_toudang
from scripts.validate_data import ValidationIssue, validate_dataset


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
    }


def write_csv(path, headers, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


class ValidationContractTest(unittest.TestCase):
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

    def test_312_accepts_configured_primary_plus_two_secondary_subjects(self):
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
            self.assertEqual(validate_dataset(directory.resolve()), [])

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


class AdmissionNormalizationTest(unittest.TestCase):
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


class ValidatorCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.fspath(VALIDATOR), *map(os.fspath, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_positional_valid_fixture_exits_zero_with_json_output(self):
        process = self.run_cli(FIXTURES / "demo-33")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["issues"], [])

    def test_deprecated_switch_uses_same_validator(self):
        process = self.run_cli("--province-dir", FIXTURES / "demo-312")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(json.loads(process.stdout)["valid"])

    def test_invalid_fixture_exits_two_and_reports_real_line(self):
        process = self.run_cli(FIXTURES / "duplicate-program")
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        payload = json.loads(process.stdout)
        duplicate = next(item for item in payload["issues"] if item["code"] == "duplicate_admission_key")
        self.assertEqual(duplicate["row"], 5)


if __name__ == "__main__":
    unittest.main()
