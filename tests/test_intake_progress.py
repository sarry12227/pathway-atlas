"""Incomplete parent answers must lead to another question, not a planning run."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_questionnaire_intake import structured_answers
from scripts.questionnaire_intake import build_profile_from_questionnaire


def resolved_intake(profile):
    from scripts.intake_progress import REQUIRED_FIELDS, assess_intake
    state = {"schema_version": "1.0", "responses": {
        key: {"status": "answered", "quote": "合成测试中家长明确回答的该项资料"}
        for key in REQUIRED_FIELDS
    }}
    check = assess_intake(state)
    state["confirmation"] = {"profile_digest": profile.digest,
                             "progress_digest": check["progress_digest"], "quote": "确认以上资料正确"}
    return state


class IntakeProgressTest(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.intake_progress"),
                             "A three-turn intake needs an executable progress gate")
        from scripts import intake_progress
        return intake_progress

    def test_partial_score_answer_keeps_collecting_one_missing_field(self):
        module = self.module()
        state = {"schema_version": "1.0", "responses": {
            "province": {"status": "answered", "quote": "江苏"},
            "grade": {"status": "answered", "quote": "高三"},
            "exam_year": {"status": "answered", "quote": "2027"},
            "exam_score": {"status": "answered", "quote": "9调考550分"},
            "school_rank": {"status": "unknown", "quote": "校排大概学校中等，其他的不太清楚"},
        }}
        result = module.assess_intake(state)
        self.assertEqual(result["phase"], "collecting")
        self.assertFalse(result["ready_for_planning"])
        self.assertEqual(result["next_field"], "city")
        self.assertIn("城市", result["next_question"])
        self.assertNotIn("school_rank", result["missing_fields"])
        self.assertIn("target_majors", result["missing_fields"])
        self.assertIn("tuition_budget", result["missing_fields"])

    def test_explicit_unknown_is_not_a_blanket_skip_of_unasked_conditions(self):
        module = self.module()
        state = {"schema_version": "1.0", "responses": {
            "school_rank": {"status": "unknown", "quote": "不清楚校排"}}}
        result = module.assess_intake(state)
        self.assertIn("living_budget", result["missing_fields"])
        self.assertIn("foreign_language", result["missing_fields"])
        state["responses"]["tuition_budget"] = {"status": "unknown", "quote": ""}
        with self.assertRaises(ValueError):
            module.assess_intake(state)

    def test_all_resolved_fields_still_need_explicit_confirmation(self):
        module = self.module()
        profile = build_profile_from_questionnaire(structured_answers())
        state = resolved_intake(profile)
        state.pop("confirmation")
        result = module.assess_intake(state, profile_digest=profile.digest)
        self.assertEqual(result["phase"], "confirming")
        self.assertFalse(result["ready_for_planning"])
        state["responses"].pop("color_vision")
        self.assertEqual(module.assess_intake(state)["phase"], "collecting")

    def test_confirmation_is_invalidated_by_answer_or_profile_changes(self):
        module = self.module()
        profile = build_profile_from_questionnaire(structured_answers())
        state = resolved_intake(profile)
        self.assertTrue(module.assess_intake(state, profile_digest=profile.digest)["ready_for_planning"])
        self.assertFalse(module.assess_intake(state, profile_digest="changed")["ready_for_planning"])
        state["responses"]["tuition_budget"]["quote"] = "改为另一档学费预算"
        self.assertFalse(module.assess_intake(state, profile_digest=profile.digest)["ready_for_planning"])

    def test_cli_cannot_start_from_auto_filled_profile_and_confirmed_flag_alone(self):
        profile = build_profile_from_questionnaire(structured_answers())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "work").mkdir()
            path = root / "profile.json"
            path.write_text(json.dumps(profile.to_dict(), ensure_ascii=True), encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "scripts.host_workflow", "start",
                "--workspace", str(root / "work"), "--profile", str(path), "--confirmed"],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(list((root / "work").iterdir()), [])
            self.assertFalse(json.loads(result.stderr)["report_generated"])

    def test_cli_rejects_partial_coverage_even_with_a_confirmation_object(self):
        module = self.module()
        profile = build_profile_from_questionnaire(structured_answers())
        state = {"schema_version": "1.0", "responses": {
            "province": {"status": "answered", "quote": "湖北"}}}
        state["confirmation"] = {"profile_digest": profile.digest,
                                 "progress_digest": module.assess_intake(state)["progress_digest"],
                                 "quote": "确认"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_file, intake_file = root / "profile.json", root / "intake.json"
            profile_file.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
            intake_file.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "scripts.host_workflow", "start",
                "--workspace", str(root), "--profile", str(profile_file), "--intake", str(intake_file), "--confirmed"],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 2)
            feedback = json.loads(result.stderr)
            self.assertEqual(feedback["error_code"], "intake_incomplete")
            self.assertEqual(feedback["intake_progress"]["next_field"], "grade")
            self.assertFalse((root / "journal").exists())


if __name__ == "__main__":
    unittest.main()
