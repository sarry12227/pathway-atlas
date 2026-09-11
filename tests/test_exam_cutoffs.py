"""Synthetic cutoff pairs test score-first planning, not admission accuracy."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.planning_brief import build_planning_brief
from scripts.planning_profile import PlanningProfile
from tests.test_planning_brief import brief_fixture


def cutoff_fixture():
    profile, payload = brief_fixture()
    raw = profile.to_dict()
    raw.pop("digest")
    raw.pop("mode")
    raw["rank_observations"][0]["rank"] = None
    profile = PlanningProfile.create(raw)
    payload["profile_digest"] = profile.digest
    model = {"method": "exam_cutoffs", "basis": "家长确认的合成联考划线",
             "year": 2026, "gaokao_max_score": 750, "margin_points": 10,
             "gaokao_province": "浙江", "gaokao_subject_group": "综合", "gaokao_score_basis": profile.score_basis,
             "exam": {"name": "合成联考", "date": "2026-09-01", "scope": "school",
                      "province": "浙江", "subject_group": "综合", "max_score": 750,
                      "score_basis": profile.score_basis, "source_kind": "parent_text",
                      "confirmed": True,
                      "lines": [{"label": "本科线", "score": 509.5},
                                {"label": "高优线", "score": 606.5}]},
             "anchors": []}
    for label, score in (("本科线", 520), ("高优线", 620)):
        quote = f"合成高考参照：{label}对应{score}分。"
        payload["sources"][0]["text"] += "\n" + quote
        model["anchors"].append({"exam_label": label, "gaokao_label": label,
            "gaokao_score": score, "reference_kind": "published_benchmark",
            "basis": "测试对应关系", "citation": {"source_id": "synthetic", "quote": quote}})
    payload["calibration"] = model
    for candidate in payload["candidates"]:
        candidate["benchmark_tier"] = "冲" if candidate["threshold_rank"] < 50000 else "稳" if candidate["threshold_rank"] < 60000 else "保"
    return profile, payload


class ExamCutoffTest(unittest.TestCase):
    def test_decimal_interpolation_without_school_rank_then_maps_sparse_table(self):
        profile, payload = cutoff_fixture()
        result = build_planning_brief(profile, payload, research_year=2026)
        position = result["positioning"]
        self.assertEqual(position["method"], "exam_cutoffs")
        self.assertEqual(position["score"], 613.3)
        self.assertEqual(position["score_bounds"], [603.3, 623.3])
        self.assertEqual(position["central_rank"], 48350)
        self.assertEqual(position["rank_bounds"], [43350, 53350])
        self.assertIn("插值", position["basis"])
        self.assertIn("省排插值", position["basis"])
        self.assertIn("约613.3分", result["report_text"])

    def test_one_anchor_uses_line_difference_and_wider_margin(self):
        profile, payload = cutoff_fixture()
        payload["calibration"]["anchors"] = payload["calibration"]["anchors"][1:]
        result = build_planning_brief(profile, payload, research_year=2026)["positioning"]
        self.assertEqual(result["score"], 613.5)
        self.assertEqual(result["score_bounds"], [593.5, 633.5])
        self.assertIn("线差法", result["basis"])

    def test_missing_table_preserves_equivalent_score_and_delivers_schools(self):
        profile, payload = cutoff_fixture()
        payload.pop("score_table")
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["score"], 613.3)
        self.assertIsNone(result["positioning"]["central_rank"])
        self.assertIn("约613.3分", result["report_text"])
        self.assertIn("省排待", result["report_text"])
        self.assertEqual(len(result["ordinary"]["保"]), 5)

    def test_context_mismatch_or_unconfirmed_numbers_never_produce_a_score(self):
        changes = [{"subject_group": "历史"}, {"province": "湖北"},
                   {"date": "2026-08-01"}, {"max_score": 660},
                   {"score_basis": "不确定"}, {"confirmed": False}]
        for change in changes:
            profile, payload = cutoff_fixture()
            payload["calibration"]["exam"].update(change)
            with self.subTest(change=change):
                result = build_planning_brief(profile, payload, research_year=2026)
                self.assertIsNone(result["positioning"]["score"])

    def test_missing_correspondence_uses_existing_school_rank_fallback(self):
        profile, payload = brief_fixture()
        _, cutoff = cutoff_fixture()
        payload["fallback_calibration"] = payload["calibration"]
        payload["calibration"] = cutoff["calibration"]
        payload["calibration"]["anchors"] = []
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["method"], "school_rank")
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertIn("划线", result["positioning"]["basis"])

    def test_nonmonotone_mapping_and_tampered_citation_are_rejected(self):
        for change in ("order", "citation"):
            profile, payload = cutoff_fixture()
            if change == "order":
                payload["calibration"]["anchors"][1]["gaokao_score"] = 510
                payload["sources"][0]["text"] = payload["sources"][0]["text"].replace("620", "510")
                payload["calibration"]["anchors"][1]["citation"]["quote"] = "合成高考参照：高优线对应510分。"
            else:
                payload["calibration"]["anchors"][1]["gaokao_score"] = 621
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_planning_brief(profile, payload, research_year=2026)

    def test_parent_teacher_reference_is_disclosed_without_a_fake_public_url(self):
        profile, payload = cutoff_fixture()
        anchor = payload["calibration"]["anchors"][1]
        anchor.pop("citation")
        anchor.update(reference_kind="teacher_reported", parent_statement="老师说高优线对应620分")
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["score"], 613.3)
        self.assertIn("家长转述", result["positioning"]["basis"])

    def test_reference_year_must_match_the_score_table(self):
        profile, payload = cutoff_fixture()
        payload["score_table"]["year"] = 2025
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["score"], 613.3)
        self.assertIsNone(result["positioning"]["central_rank"])

    def test_cutoff_on_endpoint_and_nearby_outside_interval(self):
        from scripts.exam_cutoffs import estimate_cutoff_score
        self.assertEqual(estimate_cutoff_score(606.5, [(509.5, 520), (606.5, 620)], 750)["score"], 620)
        outside = estimate_cutoff_score(616.5, [(509.5, 520), (606.5, 620)], 750)
        self.assertEqual(outside["score"], 630)
        self.assertIn("区间外", outside["calculation"])
        self.assertIsNone(estimate_cutoff_score(200, [(509.5, 520)], 750))

    def test_gaokao_context_cannot_be_copied_across_provinces_or_score_bases(self):
        for field, value in (("gaokao_province", "湖北"), ("gaokao_subject_group", "历史"),
                             ("gaokao_score_basis", "原始分")):
            profile, payload = cutoff_fixture()
            payload["calibration"][field] = value
            with self.subTest(field=field):
                self.assertIsNone(build_planning_brief(profile, payload, research_year=2026)["positioning"]["score"])

    def test_partial_score_table_does_not_invent_boundary_ranks(self):
        profile, payload = cutoff_fixture()
        payload["score_table"]["rows"] = payload["score_table"]["rows"][1:3]
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 48350)
        self.assertIsNone(result["positioning"]["rank_bounds"][1])
        self.assertNotIn("None", result["report_text"])

    def test_same_exam_joint_rank_does_not_hide_the_school_cutoffs(self):
        profile, payload = cutoff_fixture()
        raw = profile.to_dict()
        raw.pop("digest")
        raw.pop("mode")
        observation = dict(raw["rank_observations"][0])
        observation.update(scope="province_joint", rank=2000, cohort_size=10000, source="joint_exam_report")
        raw["rank_observations"].append(observation)
        profile = PlanningProfile.create(raw)
        payload["profile_digest"] = profile.digest
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["score"], 613.3)

    def test_same_exam_joint_rank_does_not_break_school_fallback(self):
        profile, payload = brief_fixture()
        raw = profile.to_dict()
        raw.pop("digest")
        raw.pop("mode")
        observation = dict(raw["rank_observations"][0])
        observation.update(scope="province_joint", rank=2000, cohort_size=10000, source="joint_exam_report")
        raw["rank_observations"].append(observation)
        profile = PlanningProfile.create(raw)
        payload["profile_digest"] = profile.digest
        payload["fallback_calibration"] = payload["calibration"]
        _, cutoff = cutoff_fixture()
        payload["calibration"] = cutoff["calibration"]
        payload["calibration"]["anchors"] = []
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 50000)

    def test_teacher_statement_must_keep_decimal_and_label(self):
        for statement in ("老师说高优线对应620.5分", "老师说另一条线对应620分", "老师说高优线对应1620分"):
            profile, payload = cutoff_fixture()
            anchor = payload["calibration"]["anchors"][1]
            anchor.pop("citation")
            anchor.update(reference_kind="teacher_reported", parent_statement=statement)
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                build_planning_brief(profile, payload, research_year=2026)

    def test_missing_image_cells_are_not_zero_and_do_not_block_other_pairs(self):
        profile, payload = cutoff_fixture()
        payload["calibration"]["exam"]["lines"].append({"label": "专科线", "score": None})
        self.assertEqual(build_planning_brief(profile, payload, research_year=2026)["positioning"]["score"], 613.3)

    def test_cli_persists_parent_cutoffs_and_delivers_fixed_report(self):
        from scripts.host_workflow import PlanningWorkflow
        from tests.test_planning_session_replay_journal import _report
        profile, payload = cutoff_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            flow = PlanningWorkflow.start(root, profile, _report(), confirmed=True)
            submission = root / "cutoffs.json"
            submission.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "brief",
                "--workspace", str(root), "--session", flow.session.session_id,
                "--submission", str(submission)], cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertTrue(output["report_generated"])
            self.assertIn("约613.3分", output["report_text"])
            saved = list(root.rglob("*.brief-*.json"))
            self.assertTrue(saved)
            record = json.loads(saved[0].read_text(encoding="utf-8"))
            self.assertEqual(record["submission"]["calibration"]["exam"]["lines"][0]["score"], 509.5)


if __name__ == "__main__":
    unittest.main()
