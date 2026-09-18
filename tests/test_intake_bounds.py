"""Bound the actual next-question loop without inventing family answers."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import intake_progress as intake


class IntakeBoundsTest(unittest.TestCase):
    def test_unreadable_cutoff_gets_one_text_fallback_then_moves_on(self):
        before_cutoffs = ("province", "grade", "exam_year", "city", "high_school", "class_level",
                          "gender", "subject_selection", "score_basis", "exam_score", "exam_name",
                          "exam_date", "exam_scope", "exam_max_score")
        state = {"schema_version": "1.1", "responses": {
            key: {"status": "unknown", "quote": "不清楚"} for key in before_cutoffs},
            "question_history": []}
        state = intake.record_answer(state, "cutoffs", "我发了划线图片", {})
        self.assertEqual(intake.assess_intake(state)["question_id"], "cutoffs")
        self.assertEqual(intake.assess_intake(state)["question_kind"], "clarification")
        self.assertNotIn("发图片", intake.assess_intake(state)["next_question"])
        self.assertIn("名称与分数", intake.assess_intake(state)["next_question"])
        state = intake.record_answer(state, "cutoffs", "文字我也看不清", {})
        progress = intake.assess_intake(state)
        self.assertEqual(progress["question_id"], "rank")
        self.assertIn("exam_cutoffs", progress["deferred_fields"])

    def test_record_cli_persists_one_reply_and_returns_the_next_question(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "intake.json"
            reply = Path(temporary) / "reply.json"
            reply.write_text(json.dumps({"question_id": "province", "quote": "湖北", "responses": {
                "province": {"status": "answered", "quote": "湖北"}}}), encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "scripts.intake_progress",
                "--state", str(state), "--record", str(reply)], capture_output=True,
                text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["question_id"], "grade_year")
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["question_history"]), 1)
            self.assertEqual(saved["responses"]["province"]["quote"], "湖北")

    def test_deferred_detail_does_not_block_confirmed_cli_start(self):
        from tests.test_questionnaire_intake import structured_answers
        from scripts.questionnaire_intake import build_profile_from_questionnaire
        profile = build_profile_from_questionnaire(structured_answers())
        state = {"schema_version": "1.1", "responses": {
            key: {"status": "unknown", "quote": "不清楚"}
            for key in intake.REQUIRED_FIELDS if key != "best_rank_exam"},
            "question_history": [{"question_id": "rank_history", "quote": "平时约前150，最好考试记不清"}]}
        progress = intake.assess_intake(state)
        self.assertEqual(progress["deferred_fields"], ["best_rank_exam"])
        state["confirmation"] = {"profile_digest": profile.digest, "progress_digest": progress["progress_digest"],
                                 "quote": "以上资料及待核验项准确"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "profile.json").write_text(json.dumps(profile.to_dict()), encoding="utf-8")
            (root / "intake.json").write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "scripts.host_workflow", "start",
                "--workspace", str(root), "--profile", str(root / "profile.json"),
                "--intake", str(root / "intake.json"), "--confirmed"],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["report_generated"])

    def test_coherent_answers_do_not_require_seventy_two_turns(self):
        state = {"schema_version": "1.0", "responses": {}}
        turns = 0
        while intake.assess_intake(state)["phase"] == "collecting":
            result = intake.assess_intake(state)
            fields = result.get("question_fields", [result["next_field"]])
            for field in fields:
                state["responses"][field] = {"status": "unknown", "quote": "这题涉及的信息我都不清楚"}
            turns += 1
            self.assertLessEqual(turns, 30, "A parent must not be asked 72 independent fields")
        self.assertEqual(set(state["responses"]), set(intake.REQUIRED_FIELDS))
        self.assertFalse(intake.assess_intake(state)["ready_for_planning"])

    def test_known_facts_are_excluded_from_the_current_question(self):
        state = {"schema_version": "1.0", "responses": {
            key: {"status": "unknown", "quote": "不清楚"}
            for key in intake.REQUIRED_FIELDS if key not in {"color_vision", "physical_fitness"}
        }}
        state["responses"]["vision"] = {"status": "answered", "quote": "近视约250度"}
        result = intake.assess_intake(state)
        self.assertEqual(set(result.get("question_fields", [])), {"color_vision", "physical_fitness"})
        self.assertNotIn("近视", result["next_question"])
        self.assertNotIn("视力", result["next_question"])

    def test_unanswered_detail_gets_one_clarifier_then_is_deferred(self):
        state = {"schema_version": "1.1", "responses": {}, "question_history": []}
        state = intake.record_answer(state, "province", "某省，具体报名地还不知道", {})
        self.assertEqual(intake.assess_intake(state)["question_kind"], "clarification")
        state = intake.record_answer(state, "province", "目前没有更多资料", {})
        progress = intake.assess_intake(state)
        self.assertNotEqual(progress["question_id"], "province")
        self.assertIn("province", progress["deferred_fields"])
        self.assertNotIn("province", state["responses"])
        self.assertFalse(progress["ready_for_planning"])
        with self.assertRaises(ValueError):
            intake.record_answer(state, "province", "再问一遍", {})

    def test_unanswered_subfields_cannot_turn_into_endless_clarification(self):
        state = {"schema_version": "1.1", "responses": {}, "question_history": []}
        seen = {}
        for _ in range(31):
            progress = intake.assess_intake(state)
            if progress["phase"] != "collecting":
                break
            qid = progress["question_id"]
            seen[qid] = seen.get(qid, 0) + 1
            state = intake.record_answer(state, qid, "需要之后核实细节", {})
        progress = intake.assess_intake(state)
        self.assertEqual(progress["phase"], "confirming")
        self.assertLessEqual(len(state["question_history"]), 30)
        self.assertTrue(all(count <= 2 for count in seen.values()))
        self.assertLessEqual(sum(count - 1 for count in seen.values()), 6)
        self.assertEqual(state["responses"], {})
        self.assertEqual(set(progress["deferred_fields"]), set(intake.REQUIRED_FIELDS))

    def test_unknown_only_resolves_the_question_actually_asked(self):
        state = {"schema_version": "1.1", "responses": {}, "question_history": []}
        state = intake.record_answer(state, "province", "不知道", {})
        self.assertEqual(state["responses"], {"province": {"status": "unknown", "quote": "不知道"}})
        progress = intake.assess_intake(state)
        self.assertEqual(progress["question_id"], "grade_year")
        self.assertIn("tuition_budget", progress["missing_fields"])
        self.assertFalse(progress["ready_for_planning"])

    def test_volunteered_information_is_preserved_and_skips_reasking(self):
        quote = "湖北，高三，2027年高考，人数大概四五百人"
        responses = {key: {"status": "answered", "quote": quote}
                     for key in ("province", "grade", "exam_year", "rank_cohort")}
        original = {"schema_version": "1.1", "responses": {}, "question_history": []}
        state = intake.record_answer(original, "province", quote, responses)
        self.assertEqual(original["responses"], {})
        self.assertEqual(state["responses"]["rank_cohort"]["quote"], quote)
        self.assertEqual(intake.assess_intake(state)["question_id"], "school")
        with self.assertRaises(ValueError):
            intake.record_answer(state, "school", "某高中", {
                "high_school": {"status": "answered", "quote": "并非用户所说的内容"}})

    def test_confirmation_binds_the_conversation_as_well_as_fields(self):
        state = {"schema_version": "1.1", "responses": {}, "question_history": []}
        while intake.assess_intake(state)["phase"] == "collecting":
            progress = intake.assess_intake(state)
            state = intake.record_answer(state, progress["question_id"], "不知道", {})
        progress = intake.assess_intake(state)
        self.assertEqual(progress["phase"], "confirming")
        state["confirmation"] = {"profile_digest": "profile", "progress_digest": progress["progress_digest"],
                                 "quote": "以上准确"}
        self.assertTrue(intake.assess_intake(state, profile_digest="profile")["ready_for_planning"])
        changed = copy.deepcopy(state)
        changed["question_history"][0]["quote"] = "修改原来的回答"
        self.assertFalse(intake.assess_intake(changed, profile_digest="profile")["ready_for_planning"])


if __name__ == "__main__":
    unittest.main()
