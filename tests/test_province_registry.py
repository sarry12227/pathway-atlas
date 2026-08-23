import csv
import json
import math
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from scripts.data_loader import DataError, load_toudang
from scripts.province_registry import (
    DuplicateProvinceError,
    ProvinceConfig,
    ProvinceConfigError,
    ProvincePathError,
    UnknownProvinceError,
    discover_provinces,
    resolve_province_dir,
    validate_subject_selection,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "provinces"


def write_metadata(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "province.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def valid_metadata(province: str = "虚构省") -> dict:
    return {
        "province": province,
        "mode": "3+1+2",
        "primary_subjects": ["物理", "历史"],
        "secondary_subjects": ["化学", "生物", "思想政治", "地理"],
        "score_scale": 750,
        "schema_version": "1.0",
    }


class ProvinceRegistryTest(unittest.TestCase):
    def test_resolves_by_metadata_not_directory_name(self):
        path = resolve_province_dir(FIXTURES, "演示甲省")
        self.assertEqual(path, (FIXTURES / "demo-312").resolve())

    def test_config_is_immutable_and_json_serializable(self):
        config = discover_provinces(FIXTURES)["演示甲省"]
        self.assertIsInstance(config, ProvinceConfig)
        self.assertEqual(config.mode, "3+1+2")
        self.assertEqual(config.directory, (FIXTURES / "demo-312").resolve())
        self.assertEqual(json.loads(json.dumps(config.to_dict(), ensure_ascii=False))["score_scale"], 750)
        with self.assertRaises(FrozenInstanceError):
            config.mode = "3+3"

    def test_unknown_province_lists_sorted_available_names(self):
        with self.assertRaises(UnknownProvinceError) as caught:
            resolve_province_dir(FIXTURES, "不存在省")
        self.assertIn("可用省份：演示乙市、演示甲省", str(caught.exception))

    def test_path_like_province_input_is_not_interpreted_as_a_path(self):
        with self.assertRaises(UnknownProvinceError):
            resolve_province_dir(FIXTURES, "../demo-312")

    def test_duplicate_metadata_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_metadata(root / "first", valid_metadata("重复省"))
            write_metadata(root / "second", valid_metadata("重复省"))
            with self.assertRaises(DuplicateProvinceError):
                discover_provinces(root)

    def test_nested_metadata_is_not_discovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_metadata(root / "outer" / "nested", valid_metadata())
            self.assertEqual(discover_provinces(root), {})

    def test_real_rename_is_not_resolved_from_a_stale_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            moved = Path(temporary) / "moved"
            write_metadata(root / "entry", valid_metadata("改名测试省"))
            self.assertIn("改名测试省", discover_provinces(root))
            (root / "entry").rename(moved)
            with self.assertRaises(UnknownProvinceError):
                resolve_province_dir(root, "改名测试省")

    def test_child_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            external = Path(temporary) / "external"
            root.mkdir()
            write_metadata(external, valid_metadata("外部省"))
            link = root / "linked"
            try:
                link.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink unavailable: {error}")
            with self.assertRaises(ProvincePathError):
                discover_provinces(root)

    def test_metadata_symlink_is_never_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            entry = root / "entry"
            external = Path(temporary) / "external.json"
            entry.mkdir(parents=True)
            external.write_text(json.dumps(valid_metadata(), ensure_ascii=False), encoding="utf-8")
            try:
                (entry / "province.json").symlink_to(external)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink unavailable: {error}")
            with self.assertRaises(ProvincePathError):
                discover_provinces(root)


class ProvinceMetadataValidationTest(unittest.TestCase):
    def assert_invalid_payload(self, payload: dict) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_metadata(root / "entry", payload)
            with self.assertRaises(ProvinceConfigError):
                discover_provinces(root)

    def test_missing_and_extra_fields_are_rejected(self):
        missing = valid_metadata()
        del missing["score_scale"]
        self.assert_invalid_payload(missing)
        extra = valid_metadata()
        extra["directory"] = "outside"
        self.assert_invalid_payload(extra)

    def test_unknown_mode_and_schema_are_rejected(self):
        mode = valid_metadata()
        mode["mode"] = "traditional"
        self.assert_invalid_payload(mode)
        version = valid_metadata()
        version["schema_version"] = "2.0"
        self.assert_invalid_payload(version)

    def test_non_finite_and_unreasonable_score_scales_are_rejected(self):
        for value in (math.nan, math.inf, 0, 99, 1001, True, "750"):
            with self.subTest(value=value):
                payload = valid_metadata()
                payload["score_scale"] = value
                self.assert_invalid_payload(payload)

    def test_subjects_are_trimmed_and_deduplicated_in_stable_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = valid_metadata()
            payload["primary_subjects"] = [" 物理 ", "历史", "物理"]
            payload["secondary_subjects"] = [" 化学", "化学 ", "地理"]
            write_metadata(root / "entry", payload)
            config = discover_provinces(root)["虚构省"]
            self.assertEqual(config.primary_subjects, ("物理", "历史"))
            self.assertEqual(config.secondary_subjects, ("化学", "地理"))

    def test_empty_or_wrong_typed_subjects_are_rejected(self):
        for value in ([], [" "], "物理", [1, "物理"], None):
            with self.subTest(value=value):
                payload = valid_metadata()
                payload["secondary_subjects"] = value
                self.assert_invalid_payload(payload)

    def test_path_syntax_in_metadata_name_is_rejected(self):
        for name in ("../省", "a/b", "a\\b", ".", ".."):
            with self.subTest(name=name):
                self.assert_invalid_payload(valid_metadata(name))

    def test_invalid_json_duplicate_keys_and_nan_are_controlled_errors(self):
        documents = (
            "{invalid",
            '{"province":"甲","province":"乙"}',
            '{"province":NaN}',
        )
        for document in documents:
            with self.subTest(document=document), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entry = root / "entry"
                entry.mkdir()
                (entry / "province.json").write_text(document, encoding="utf-8")
                with self.assertRaises(ProvinceConfigError):
                    discover_provinces(root)


class SubjectSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configs = discover_provinces(FIXTURES)
        cls.config_312 = configs["演示甲省"]
        cls.config_33 = configs["演示乙市"]

    def test_312_accepts_one_primary_and_two_distinct_secondary_subjects(self):
        validate_subject_selection(self.config_312, "物理", ("化学", "地理"))

    def test_312_rejects_unknown_duplicate_and_wrong_counts(self):
        invalid = (
            ("技术", ("化学", "地理")),
            ("物理", ("化学", "化学")),
            ("物理", ("化学",)),
            ("物理", ("化学", "技术")),
        )
        for primary, secondary in invalid:
            with self.subTest(primary=primary, secondary=secondary), self.assertRaises(ValueError):
                validate_subject_selection(self.config_312, primary, secondary)

    def test_33_accepts_any_three_distinct_configured_subjects(self):
        validate_subject_selection(self.config_33, "物理", ("化学", "地理"))
        validate_subject_selection(self.config_33, "生物", ("历史", "思想政治"))

    def test_33_rejects_duplicate_unknown_and_non_three_subject_groups(self):
        invalid = (
            ("物理", ("化学", "化学")),
            ("物理", ("化学",)),
            ("物理", ("化学", "技术")),
        )
        for primary, secondary in invalid:
            with self.subTest(primary=primary, secondary=secondary), self.assertRaises(ValueError):
                validate_subject_selection(self.config_33, primary, secondary)


class ProvinceSchemaContractTest(unittest.TestCase):
    def test_schema_and_synthetic_fixtures_share_the_runtime_contract(self):
        schema = json.loads((ROOT / "schemas" / "province.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["mode"]["enum"], ["3+1+2", "3+3"])
        self.assertEqual(schema["properties"]["schema_version"]["enum"], ["1.0"])
        required = set(schema["required"])
        for path in sorted(FIXTURES.glob("*/province.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), required)
            self.assertIn(payload["mode"], schema["properties"]["mode"]["enum"])


class ExplicitProvinceDirectoryLoaderTest(unittest.TestCase):
    def test_loader_reads_only_the_explicit_resolved_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            province_dir = Path(temporary) / "arbitrary-folder-name"
            province_dir.mkdir()
            with (province_dir / "tou_dang.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=("year", "subject_group", "min_score", "min_rank", "is_inside_hubei"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "year": 2026,
                        "subject_group": "物理",
                        "min_score": 600,
                        "min_rank": 9000,
                        "is_inside_hubei": 0,
                    }
                )

            year, rows = load_toudang(
                province=None,
                subject_group="物理",
                root=Path(temporary) / "must-not-be-read",
                province_dir=province_dir.resolve(),
            )

            self.assertEqual(year, 2026)
            self.assertEqual(rows[0]["min_rank"], 9000)

    def test_loader_rejects_a_directory_that_was_not_pre_resolved(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            path = Path(temporary) / "tou_dang.csv"
            path.write_text(
                "year,subject_group,min_score,min_rank,is_inside_hubei\n"
                "2026,物理,600,9000,0\n",
                encoding="utf-8",
            )
            relative = Path(os.path.relpath(temporary, Path.cwd()))
            with self.assertRaisesRegex(DataError, "province_dir"):
                load_toudang(
                    province=None,
                    subject_group="物理",
                    province_dir=relative,
                )


if __name__ == "__main__":
    unittest.main()
