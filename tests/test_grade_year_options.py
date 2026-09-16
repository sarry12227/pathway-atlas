"""Calendar labels must not put an incoming final-year student in the old cohort."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import questionnaire_intake as intake
from tests.test_questionnaire_intake import structured_answers


class GradeYearOptionsTest(unittest.TestCase):
    def options(self, **kwargs):
        self.assertTrue(callable(getattr(intake, "grade_year_options", None)),
                        "Intake must supply date-based grade/year options")
        return intake.grade_year_options(**kwargs)

    def test_september_reported_case_targets_next_gaokao(self):
        result = self.options(as_of=date(2026, 9, 16))
        self.assertEqual(result["as_of"], "2026-09-16")
        self.assertEqual([(o["grade"], o["exam_year"]) for o in result["options"]],
                         [("高一", 2029), ("高二", 2028), ("高三", 2027), ("其他", None)])
        self.assertEqual(result["options"][2]["label"], "高三（预计2027年高考）")

    def test_july_first_rolls_over_but_january_first_does_not(self):
        cases = [
            (date(2026, 6, 30), [2028, 2027, 2026]),
            (date(2026, 7, 1), [2029, 2028, 2027]),
            (date(2026, 12, 31), [2029, 2028, 2027]),
            (date(2027, 1, 1), [2029, 2028, 2027]),
            (date(2027, 6, 30), [2029, 2028, 2027]),
            (date(2027, 7, 1), [2030, 2029, 2028]),
            (date(2028, 2, 29), [2030, 2029, 2028]),
        ]
        for as_of, expected in cases:
            with self.subTest(as_of=as_of):
                result = self.options(as_of=as_of)
                self.assertEqual([o["exam_year"] for o in result["options"][:3]], expected)

    def test_default_clock_uses_shanghai_midnight(self):
        class Clock:
            @classmethod
            def now(cls, tz):
                return datetime(2026, 6, 30, 16, 0, tzinfo=timezone.utc).astimezone(tz)

        self.assertTrue(callable(getattr(intake, "grade_year_options", None)))
        with patch.object(intake, "datetime", Clock):
            result = self.options()
        self.assertEqual(result["as_of"], "2026-07-01")
        self.assertEqual(result["options"][2]["exam_year"], 2027)

    def test_actual_cli_emits_one_current_grade_question(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "scripts.questionnaire_intake", "--grade-options", "--as-of", "2026-09-16"],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), "CLI must supply the grade options")
        data = json.loads(result.stdout)
        self.assertIn("年级", data["question"])
        self.assertEqual(data["options"][2]["exam_year"], 2027)
        self.assertEqual(result.stderr, "")

    def test_explicit_cohort_is_not_rewritten_during_profile_replay(self):
        for year in (2026, 2027, 2029):
            with self.subTest(year=year):
                answers = structured_answers()
                answers[4] = {"grade": "高三", "exam_year": year}
                profile = intake.build_profile_from_questionnaire(answers)
                self.assertEqual(profile.exam_year, year)


if __name__ == "__main__":
    unittest.main()
