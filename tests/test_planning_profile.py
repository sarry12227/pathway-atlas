from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unicodedata

from scripts import generate_report, query_plan
from scripts.planning_profile import (
    PlanningMode,
    PlanningProfile,
    RankObservation,
    load_planning_profile,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "planning-profile.schema.json"


def reference_payload() -> dict:
    return {
        "schema_version": "2.0",
        "gender": "不便回答",
        "province": "湖北",
        "city": "武汉",
        "high_school": "武汉市示例中学",
        "grade": "高二",
        "exam_year": 2028,
        "class_level": "重点班",
        "subject_mode": "3+1+2",
        "subject_group": "历史",
        "secondary_subjects": ["地理", "政治"],
        "score_basis": "原始分",
        "rank_observations": [
            {
                "exam_date": "2026-06-01",
                "scope": "school",
                "score": 610,
                "max_score": 750,
                "rank": 120,
                "cohort_size": 1000,
            }
        ],
        "best_rank": 80,
        "usual_rank": 140,
        "awards": [],
        "activities": [],
        "target_schools": ["示例甲大学"],
        "target_school_reasons": ["专业实力强"],
        "target_majors": ["历史学"],
        "target_major_reasons": ["长期兴趣"],
        "target_regions": ["武汉"],
        "excluded_regions": [],
        "future_plan": "继续深造",
        "concerns": ["院校定位"],
        "desired_outcomes": ["院校范围", "多元路径"],
        "eligibility_facts": ["接受异地就读"],
    }


class PlanningProfileTest(unittest.TestCase):
    def test_factory_creates_deep_frozen_reference_profile(self):
        payload = reference_payload()
        profile = PlanningProfile.create(payload)

        self.assertEqual(profile.mode, PlanningMode.REFERENCE)
        self.assertIsInstance(profile.rank_observations, tuple)
        self.assertIsInstance(profile.rank_observations[0], RankObservation)
        self.assertEqual(profile.rank_observations[0].rank, 120)
        self.assertEqual(profile.rank_observations[0].high_school, "武汉市示例中学")
        self.assertEqual(profile.rank_observations[0].class_level, "重点班")
        self.assertEqual(profile.to_dict()["mode"], "reference")
        json.dumps(profile.to_dict(), ensure_ascii=False, allow_nan=False)

        payload["secondary_subjects"].append("化学")
        payload["rank_observations"][0]["rank"] = 1
        self.assertEqual(profile.secondary_subjects, ("地理", "政治"))
        self.assertEqual(profile.rank_observations[0].rank, 120)
        with self.assertRaises(FrozenInstanceError):
            profile.city = "襄阳"

    def test_direct_construction_and_replace_cannot_forge_profile_or_observation(self):
        with self.assertRaises(TypeError):
            PlanningProfile()
        with self.assertRaises(TypeError):
            RankObservation()
        profile = PlanningProfile.create(reference_payload())
        with self.assertRaises(TypeError):
            replace(profile, province="湖南")
        with self.assertRaises(TypeError):
            replace(profile.rank_observations[0], rank=1)

    def test_mode_is_derived_from_evidence_not_caller_input(self):
        payload = reference_payload()
        payload["rank_observations"][0]["scope"] = "province_official"
        self.assertEqual(PlanningProfile.create(payload).mode, PlanningMode.OFFICIAL)

        payload = reference_payload()
        payload["rank_observations"] = []
        payload["best_rank"] = None
        payload["usual_rank"] = None
        self.assertEqual(
            PlanningProfile.create(payload).mode,
            PlanningMode.LOW_INFORMATION,
        )
        payload["mode"] = "official"
        with self.assertRaises(ValueError):
            PlanningProfile.create(payload)

    def test_unknown_numeric_values_remain_none_and_mathematical_integers_normalize(self):
        payload = reference_payload()
        observation = payload["rank_observations"][0]
        observation.update(score=None, max_score=None, rank=None, cohort_size=None)
        payload["best_rank"] = None
        payload["usual_rank"] = None
        payload["exam_year"] = 2028.0
        profile = PlanningProfile.create(payload)
        self.assertEqual(profile.exam_year, 2028)
        self.assertIsNone(profile.rank_observations[0].rank)
        self.assertEqual(profile.mode, PlanningMode.LOW_INFORMATION)
        for bad in (True, 2028.5, float("nan")):
            payload["exam_year"] = bad
            with self.subTest(bad=repr(bad)), self.assertRaises((TypeError, ValueError)):
                PlanningProfile.create(payload)

    def test_nfkc_normalization_controls_digest_and_duplicate_detection(self):
        payload = reference_payload()
        payload["target_schools"] = ["Ａ计划"]
        normalized = PlanningProfile.create(payload)
        payload["target_schools"] = ["A计划"]
        plain = PlanningProfile.create(payload)
        self.assertEqual(normalized.to_dict(), plain.to_dict())
        self.assertEqual(normalized.digest, plain.digest)

        payload["target_schools"] = ["Ａ计划", "A计划"]
        with self.assertRaises(ValueError):
            PlanningProfile.create(payload)

    def test_unknown_keys_and_identity_or_path_material_fail_closed(self):
        payload = reference_payload()
        payload["student_name"] = "张三"
        with self.assertRaises(ValueError):
            PlanningProfile.create(payload)

        attacks = (
            ("city", "学生 138 0013 8000"),
            ("high_school", "C:\\private\\student.json"),
            ("future_plan", "%USERPROFILE%\\private"),
            ("eligibility_facts", ["sk-live-AbCdEfGhIjKlMnOpQrSt"]),
            ("concerns", ["wxid_private123"]),
        )
        for field, value in attacks:
            with self.subTest(field=field):
                candidate = reference_payload()
                candidate[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    PlanningProfile.create(candidate)

    def test_rank_observation_context_is_bound_to_profile(self):
        profile = PlanningProfile.create(reference_payload())
        observation = profile.rank_observations[0]
        self.assertEqual(observation.subject_group, "历史")
        self.assertEqual(observation.high_school, profile.high_school)
        self.assertEqual(observation.class_level, profile.class_level)
        self.assertEqual(observation.source, "user_reported")

    def test_v1_payload_migrates_to_official_v2_snapshot(self):
        legacy = {
            "schema_version": "1.0",
            "province": "湖北",
            "subject_mode": "3+1+2",
            "subject_group": "历史",
            "secondary_subjects": ["地理", "政治"],
            "rank": 1200,
            "grade": "高三",
            "current_year": 2026,
            "target_major_categories": ["历史学"],
            "target_cities": ["武汉"],
            "target_schools": ["示例甲大学"],
            "eligibility_facts": [],
        }
        profile = load_planning_profile(legacy)
        self.assertEqual(profile.schema_version, "2.0")
        self.assertEqual(profile.mode, PlanningMode.OFFICIAL)
        self.assertEqual(profile.rank_observations[0].rank, 1200)
        self.assertEqual(profile.rank_observations[0].scope, "province_official")

    def test_existing_profile_loaders_accept_v2_without_fabricating_a_rank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(
                json.dumps(reference_payload(), ensure_ascii=False),
                encoding="utf-8",
            )
            report_profile = generate_report._load_public_profile(path)
            query_profile, subject_mode, exam_year = query_plan._load_profile(path)
        self.assertIsInstance(report_profile, PlanningProfile)
        self.assertIsInstance(query_profile, PlanningProfile)
        self.assertIsNone(report_profile.official_rank)
        self.assertEqual(subject_mode, "3+1+2")
        self.assertEqual(exam_year, 2028)

    def test_questionnaire_field_groups_cover_all_twenty_questions_once(self):
        groups = PlanningProfile.questionnaire_field_groups()
        self.assertEqual(tuple(groups), tuple(range(1, 21)))
        flattened = tuple(field for fields in groups.values() for field in fields)
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), set(reference_payload()) - {"schema_version"})

    def test_schema_is_strict_and_matches_public_fields(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema_version"]["const"], "2.0")
        self.assertEqual(
            set(schema["required"]),
            set(reference_payload()),
        )
        self.assertEqual(
            set(schema["properties"]),
            set(reference_payload()),
        )
        self.assertFalse(schema["$defs"]["rankObservation"]["additionalProperties"])

    def test_cli_accepts_stdin_and_emits_deterministic_canonical_json(self):
        source = json.dumps(reference_payload(), ensure_ascii=False)
        command = [sys.executable, str(ROOT / "scripts" / "planning_profile.py"), "-"]
        first = subprocess.run(
            command, input=source, encoding="utf-8", capture_output=True, cwd=ROOT
        )
        second = subprocess.run(
            command, input=source, encoding="utf-8", capture_output=True, cwd=ROOT
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, "")
        emitted = json.loads(first.stdout)
        self.assertEqual(emitted["mode"], "reference")
        self.assertEqual(emitted["rank_observations"][0]["rank"], 120)

    def test_cli_errors_are_fixed_and_do_not_echo_input(self):
        secret = "C:\\private\\student-13800138000.json"
        command = [sys.executable, str(ROOT / "scripts" / "planning_profile.py"), "-"]
        result = subprocess.run(
            command,
            input="{\"future_plan\":\"" + secret.replace("\\", "\\\\") + "\"}",
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "planning-profile: invalid input\n")
        self.assertNotIn("private", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
