"""Synthetic planning references; never real admissions or prediction data."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.planning_brief import build_planning_brief
from scripts.planning_profile import PlanningProfile
from tests.test_zhejiang_subjects import zhejiang_profile


def brief_fixture():
    profile = zhejiang_profile(grade="高一", exam_year=2029, best_rank=40, usual_rank=60,
        rank_observations=[{"exam_date": "2026-09-01", "scope": "school", "score": 600,
                            "max_score": 750, "rank": 50, "cohort_size": 400}])
    profile_input = profile.to_dict()
    profile_input.pop("digest")
    profile_input.pop("mode")
    profile_input["priorities"]["target_majors"] = ["计算机"]
    profile = PlanningProfile.create(profile_input)
    text = []
    def evidence(values):
        quote = "合成测试记录：" + "；".join(str(v) for v in values) + "。"
        text.append(quote)
        return {"source_id": "synthetic", "quote": quote}
    anchors = [{"school_rank": school, "province_rank": rank,
                "citation": evidence([school, rank])}
               for school, rank in ((40, 40000), (60, 60000))]
    scores = [{"score": score, "rank": rank, "citation": evidence([score, rank])}
              for score, rank in ((650, 30000), (630, 40000), (610, 50000),
                                  (590, 60000), (570, 70000))]
    candidates = []
    for kind in ("ordinary", "strong_foundation", "comprehensive_evaluation", "hong_kong_macao"):
        thresholds = (41000, 45000, 49000, 51000, 53000, 56000, 58000,
                      61000, 65000, 70000, 75000, 80000) if kind == "ordinary" else (45000, 55000, 70000)
        for index, rank in enumerate(thresholds):
            label = {"ordinary": "普通", "strong_foundation": "强基", "comprehensive_evaluation": "综评", "hong_kong_macao": "港澳"}[kind]
            name = f"合成{label}大学{index + 1}"
            candidates.append({"school": name, "kind": kind, "location_province": "浙江" if kind != "hong_kong_macao" else "香港",
                "major": "计算机科学与技术", "matches_preferences": ["计算机"],
                "province": "浙江", "year": 2026, "threshold_rank": rank,
                "threshold_basis": "admission" if kind == "ordinary" else "shortlist",
                "required_subjects": ["物理"],
                "facts": "物理；计算机科学与技术；按简章培养；综合考核",
                "citation": evidence([name, "计算机科学与技术", rank, "物理", "按简章培养", "综合考核"]),
                "fit": "conditional", "fit_reason": "专业兴趣匹配，申请年度语种与校测待复核",
                "cultivation": "按简章培养", "selection": "综合考核",
                "cautions": "语种、费用、体检和未来年度要求待复核"})
    payload = {"schema_version": "1.0", "profile_digest": profile.digest,
        "province": "浙江", "subject_mode": "3+3", "research_year": 2026,
        "selection": ["物理", "地理", "技术"],
        "calibration": {"method": "school_rank", "school": profile.high_school,
            "year": 2026, "comparability": "same_school", "basis": "合成同校同口径校排出口",
            "margin_fraction": 0.2, "anchors": anchors},
        "score_table": {"year": 2026, "subject_group": "综合", "rows": scores}, "candidates": candidates,
        "pathway_decisions": {k: "可作为准备备选；报考资格按具体项目核实" for k in
            ("strong_foundation", "comprehensive_evaluation", "hong_kong_macao")},
        "other_pathways": [], "actions": ["本月核对意向专业选科，完成一份可报专业清单", "下次同口径大考后用新校排更新规划"],
        "sources": [{"source_id": "synthetic", "url": "https://policy.example/brief",
                     "title": "合成回归材料", "year": 2026, "text": "\n".join(text)}]}
    return profile, payload


class PlanningBriefTest(unittest.TestCase):
    def test_school_exam_produces_score_rank_and_exact_local_345_and_pathway_111(self):
        profile, payload = brief_fixture()
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertEqual(result["positioning"]["score"], 610)
        self.assertEqual(result["positioning"]["rank_bounds"], [40000, 60000])
        self.assertEqual(result["positioning"]["score_bounds"], [590, 630])
        self.assertEqual({k: len(v) for k, v in result["ordinary"].items()}, {"冲": 3, "稳": 4, "保": 5})
        for group in result["pathways"].values():
            self.assertEqual([len(group[t]) for t in ("冲", "稳", "保")], [1, 1, 1])
        body = result["report_text"]
        self.assertIn("本次考试：600/750分", body)
        self.assertIn("约610分", body)
        self.assertIn("约50000位", body)
        headings = ("一、成绩定位", "二、本省普通批", "三、强基计划", "四、综合评价", "五、港澳升学", "六、其他路径", "七、现阶段行动")
        self.assertEqual(sorted(headings, key=body.index), list(headings))
        self.assertTrue(body.endswith("AI生成，仅供升学规划参考；不构成录取承诺，正式报考以当年官方要求为准。"))

    def test_missing_calibration_keeps_sourced_representatives_without_fake_personal_rank(self):
        profile, payload = brief_fixture()
        payload["calibration"] = {"method": "unavailable", "basis": "暂未取得可比学校出口"}
        for c in payload["candidates"]:
            c["benchmark_tier"] = "冲" if c["threshold_rank"] < 50000 else "稳" if c["threshold_rank"] < 60000 else "保"
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertIsNone(result["positioning"]["central_rank"])
        self.assertEqual(len(result["ordinary"]["稳"]), 4)
        self.assertIn("目标梯度", result["report_text"])
        self.assertNotIn("600分对应", result["report_text"])

    def test_source_profile_context_and_quote_tampering_are_rejected(self):
        profile, payload = brief_fixture()
        changes = [lambda p: p.update(profile_digest="wrong"),
                   lambda p: p.update(province="湖北"),
                   lambda p: p.update(selection=["物理", "化学", "生物"]),
                   lambda p: p["score_table"].update(subject_group="历史"),
                   lambda p: p["calibration"]["anchors"][0].update(province_rank=1),
                   lambda p: p["score_table"]["rows"][0].update(score=999),
                   lambda p: p["sources"][0].update(year=2029)]
        for mutate in changes:
            changed = deepcopy(payload)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                build_planning_brief(profile, changed, research_year=2026)

    def test_local_subject_and_duplicate_filters_do_not_fabricate_quota(self):
        profile, payload = brief_fixture()
        payload["candidates"][0]["location_province"] = "湖北"
        payload["candidates"][1]["fit"] = "blocked"
        payload["candidates"].append(deepcopy(payload["candidates"][2]))
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(len(result["ordinary"]["冲"]), 1)
        self.assertIn("缺2所", result["report_text"])
        self.assertNotIn(payload["candidates"][0]["school"],
                         {item["school"] for group in result["ordinary"].values() for item in group})

    def test_hypothetical_school_threshold_is_not_pathway_selection_evidence(self):
        profile, payload = brief_fixture()
        for item in payload["candidates"]:
            if item["kind"] == "strong_foundation":
                item["threshold_basis"] = "planning_benchmark"
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertIn("普通批参照，不能代表本路径入围线", result["report_text"])

    def test_subject_requirement_blocks_a_major_instead_of_treating_physics_as_enough(self):
        profile, payload = brief_fixture()
        payload["candidates"][0]["required_subjects"] = ["物理", "化学"]
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(len(result["ordinary"]["冲"]), 2)
        self.assertNotIn(payload["candidates"][0]["school"],
                         {item["school"] for group in result["ordinary"].values() for item in group})

    def test_missing_school_details_do_not_block_known_schools_and_majors(self):
        profile, payload = brief_fixture()
        item = payload["candidates"][0]
        item.update(cultivation=None, selection=None, required_subjects=None)
        result = build_planning_brief(profile, payload, research_year=2026)
        selected = next(s for s in result["ordinary"]["冲"] if s["school"] == item["school"])
        self.assertEqual(selected["fit"], "conditional")
        self.assertIn("待核验", selected["cultivation"])

    def test_different_fields_can_cite_different_actual_pages(self):
        profile, payload = brief_fixture()
        item = payload["candidates"][0]
        payload["sources"].append({"source_id": "program", "url": "https://policy.example/program",
            "title": "合成培养说明", "year": 2026, "text": "合成培养说明：实验室轮转。"})
        item["cultivation"] = "实验室轮转"
        item["citations"] = {"cultivation": {"source_id": "program", "quote": "实验室轮转"}}
        result = build_planning_brief(profile, payload, research_year=2026)
        selected = next(s for s in result["ordinary"]["冲"] if s["school"] == item["school"])
        self.assertIn("/program", selected["citation"])

    def test_no_silent_extrapolation_beyond_the_school_anchors(self):
        profile, payload = brief_fixture()
        raw = profile.to_dict()
        raw.pop("digest")
        raw.pop("mode")
        raw["rank_observations"][0]["rank"] = 10
        profile = PlanningProfile.create(raw)
        payload["profile_digest"] = profile.digest
        for item in payload["candidates"]:
            item["benchmark_tier"] = "冲"
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertIsNone(result["positioning"]["central_rank"])
        self.assertIn("超出", result["positioning"]["basis"])

    def test_actual_gaokao_score_and_rank_keep_345_strategy_without_inventing_rank_error(self):
        profile, payload = brief_fixture()
        raw = profile.to_dict()
        raw.pop("digest")
        raw.pop("mode")
        raw.update(grade="高三", exam_year=2026, best_rank=None, usual_rank=None)
        raw["rank_observations"] = [{"exam_date": "2026-06-07", "scope": "province_official",
                                    "score": 610, "max_score": 750, "rank": 50000,
                                    "cohort_size": None}]
        for method in ("official_rank", "official_score"):
            with self.subTest(method=method):
                if method == "official_score":
                    raw["rank_observations"][0]["rank"] = None
                student = PlanningProfile.create(raw)
                payload["profile_digest"] = student.digest
                payload["calibration"] = {"method": method, "basis": "正式高考测试"}
                result = build_planning_brief(student, payload, research_year=2026)
                self.assertEqual(result["positioning"]["rank_bounds"], [50000, 50000])
                self.assertEqual(result["positioning"]["score"], 610)
                self.assertEqual([len(result["ordinary"][k]) for k in ("冲", "稳", "保")], [3, 4, 5])

    def test_monthly_score_cannot_claim_to_be_official_score(self):
        profile, payload = brief_fixture()
        payload["calibration"] = {"method": "official_score", "basis": "校考直接套表"}
        with self.assertRaises(ValueError):
            build_planning_brief(profile, payload, research_year=2026)

    def test_brief_cli_delivers_before_deep_tasks_close_without_changing_journal(self):
        from scripts.host_workflow import PlanningWorkflow
        from tests.test_planning_session_replay_journal import _report
        profile, payload = brief_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            workflow = PlanningWorkflow.start(root, profile, _report(), confirmed=True)
            payload["research_year"] = workflow.plan.research_year
            submission = root / "brief-input.json"
            submission.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            before = {p.name: p.read_bytes() for p in (root / "journal").glob("*.json")}
            result = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "brief",
                "--workspace", str(root), "--session", workflow.session.session_id,
                "--submission", str(submission)], cwd=Path(__file__).resolve().parents[1],
                capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            brief = json.loads(result.stdout)
            self.assertIn("本省普通批", brief["report_text"])
            self.assertEqual(brief["delivery"]["mode"], "planning_reference")
            self.assertGreater(brief["research_summary"]["pending"], 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in (root / "journal").glob("*.json")})
            self.assertEqual(Path(brief["report"]).read_text(encoding="utf-8"), brief["report_text"])


if __name__ == "__main__":
    unittest.main()
