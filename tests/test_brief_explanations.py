"""Explanations describe the calculation actually taken, including fallback."""
import unittest

from scripts.planning_brief import build_planning_brief
from tests.test_exam_cutoffs import cutoff_fixture
from tests.test_planning_brief import brief_fixture


class BriefExplanationTest(unittest.TestCase):
    def test_school_position_and_strategy_have_plain_explanations_without_changing_numbers(self):
        profile, payload = brief_fixture()
        result = build_planning_brief(profile, payload, research_year=2026)
        body = result["report_text"]
        self.assertIn("怎样折算", body)
        self.assertIn("学校", body)
        self.assertIn("同一省份科类", body)
        self.assertIn("怎样分档", body)
        self.assertIn("40000", body)
        self.assertIn("50000", body)
        self.assertIn("60000", body)
        self.assertEqual(result["positioning"]["score"], 610)
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertEqual([len(result["ordinary"][key]) for key in ("冲", "稳", "保")], [3, 4, 5])
        self.assertEqual(body.count("## "), 7)

    def test_two_cutoffs_explain_position_between_lines(self):
        profile, payload = cutoff_fixture()
        body = build_planning_brief(profile, payload, research_year=2026)["report_text"]
        self.assertIn("怎样折算", body)
        self.assertIn("509.5", body)
        self.assertIn("606.5", body)
        self.assertIn("520", body)
        self.assertIn("620", body)
        self.assertIn("相对位置", body)
        self.assertIn("约613.3分", body)

    def test_single_cutoff_explains_the_difference_instead_of_two_line_interpolation(self):
        profile, payload = cutoff_fixture()
        payload["calibration"]["anchors"] = payload["calibration"]["anchors"][1:]
        body = build_planning_brief(profile, payload, research_year=2026)["report_text"]
        explanation = next(line for line in body.splitlines() if "怎样折算" in line)
        self.assertIn("相差", explanation)
        self.assertNotIn("相对位置", explanation)
        self.assertIn("约613.5分", body)

    def test_missing_provincial_table_does_not_claim_the_rank_was_looked_up(self):
        profile, payload = cutoff_fixture()
        payload.pop("score_table")
        body = build_planning_brief(profile, payload, research_year=2026)["report_text"]
        explanation = next(line for line in body.splitlines() if "怎样折算" in line)
        self.assertIn("省排还需", explanation)
        self.assertNotIn("已查得", explanation)
        self.assertNotIn("怎样分档", body)

    def test_without_calibration_only_explains_target_order_not_personal_odds(self):
        profile, payload = brief_fixture()
        payload["calibration"] = {"method": "unavailable", "basis": "缺少可比资料"}
        for candidate in payload["candidates"]:
            candidate["benchmark_tier"] = "冲"
        body = build_planning_brief(profile, payload, research_year=2026)["report_text"]
        self.assertNotIn("怎样折算", body)
        self.assertNotIn("怎样分档", body)
        self.assertIn("目标梯度", body)

    def test_cutoff_fallback_explains_the_school_route_actually_used(self):
        profile, payload = brief_fixture()
        _, cutoff = cutoff_fixture()
        payload["fallback_calibration"] = payload["calibration"]
        payload["calibration"] = cutoff["calibration"]
        payload["calibration"]["anchors"] = []
        result = build_planning_brief(profile, payload, research_year=2026)
        explanation = next(line for line in result["report_text"].splitlines() if "怎样折算" in line)
        self.assertEqual(result["positioning"]["method"], "school_rank")
        self.assertIn("学校往年", explanation)
        self.assertNotIn("相对位置", explanation)

    def test_calibrated_student_with_only_qualitative_school_targets_does_not_claim_numeric_tiering(self):
        profile, payload = brief_fixture()
        for candidate in payload["candidates"]:
            if candidate["kind"] == "ordinary":
                candidate.update(threshold_rank=None, threshold_basis="unavailable", benchmark_tier="冲")
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertNotIn("怎样分档", result["report_text"])
        self.assertIn("目标梯度，未判断个人录取把握", result["report_text"])


if __name__ == "__main__":
    unittest.main()
