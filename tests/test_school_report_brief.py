"""Program regressions using synthetic school-report text, not admissions facts."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.planning_brief import build_planning_brief
from scripts.planning_profile import PlanningProfile
from tests.test_planning_brief import brief_fixture


def report_fixture():
    profile, payload = brief_fixture()
    quote = f"合成喜报：{profile.high_school}，2026年选考综合组400人；630分及以上40人；590分及以上60人。"
    citation = {"source_id": "report-video", "quote": quote}
    payload["sources"].append({"source_id": "report-video", "year": 2026,
        "url": "https://www.douyin.com/video/synthetic-report", "title": "合成喜报转述",
        "publisher": "合成分享者", "text": quote})
    payload["calibration"] = {"method": "school_report", "basis": "喜报累计人数对应学校位置",
        "reports": [{"school": profile.high_school, "year": 2026, "subject_group": "综合",
            "comparability": "same_school", "cohort_size": 400, "source_kind": "social_video",
            "citation": citation, "metrics": [
                {"kind": "score_count", "score": 630, "count": 40, "citation": citation},
                {"kind": "score_count", "score": 590, "count": 60, "citation": citation}]}]}
    return profile, payload


class SchoolReportBriefTest(unittest.TestCase):
    def test_report_keeps_calibration_caveat_next_to_the_estimate(self):
        profile, payload = report_fixture()
        payload["calibration"]["basis"] = "仅取得一个年度的单份转述，尚未交叉核实"
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertIn(payload["calibration"]["basis"], result["report_text"].split("## 二、")[0])

    def test_qualitative_targets_keep_host_priority_instead_of_unicode_school_order(self):
        profile, payload = report_fixture()
        candidates = payload["candidates"]
        payload["candidates"] = [candidates[2], candidates[0], candidates[1], *candidates[3:]]
        for item in payload["candidates"][:3]:
            item.update(threshold_rank=None, threshold_basis="unavailable", benchmark_tier="冲")
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual([item["school"] for item in result["ordinary"]["冲"]],
                         [candidates[i]["school"] for i in (2, 0, 1)])

    def test_score_table_with_empty_score_bands_keeps_cumulative_plateaus(self):
        profile, payload = report_fixture()
        added, text = [], []
        for score, rank in ((670, 0), (669, 0), (649, 30000), (609, 50000)):
            quote = f"合成完整表：{score}分，累计{rank}位。"
            text.append(quote)
            added.append({"score": score, "rank": rank,
                          "citation": {"source_id": "empty-bands", "quote": quote}})
        payload["sources"].append({"source_id": "empty-bands", "year": 2026,
            "url": "https://example.org/full-table", "title": "合成完整表", "text": "\n".join(text)})
        payload["score_table"]["rows"].extend(added)
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["score"], 610)
        self.assertEqual(result["positioning"]["central_rank"], 50000)

    def test_nearby_extrapolation_is_labeled_but_distant_extrapolation_stays_unavailable(self):
        for school_rank, expected in ((75, 75000), (140, None)):
            profile, payload = report_fixture()
            value = profile.to_dict()
            value.pop("digest")
            value.pop("mode")
            value["rank_observations"][0]["rank"] = school_rank
            profile = PlanningProfile.create(value)
            payload["profile_digest"] = profile.digest
            for item in payload["candidates"]:
                item["benchmark_tier"] = "冲" if item["threshold_rank"] < 50000 else "稳" if item["threshold_rank"] < 60000 else "保"
            result = build_planning_brief(profile, payload, research_year=2026)
            with self.subTest(rank=school_rank):
                self.assertEqual(result["positioning"]["central_rank"], expected)
                if expected:
                    self.assertIn("有限外推", result["positioning"]["basis"])
                    self.assertGreaterEqual(result["positioning"]["rank_bounds"][1], 105000)
                else:
                    self.assertTrue(result["ordinary"]["保"])

    def test_earlier_school_report_uses_its_own_year_score_table(self):
        profile, payload = report_fixture()
        payload["calibration"]["reports"][0]["year"] = 2025
        payload["sources"][-1]["year"] = 2025
        quote = payload["sources"][-1]["text"].replace("2026年", "2025年")
        payload["sources"][-1]["text"] = quote
        payload["calibration"]["reports"][0]["citation"]["quote"] = quote
        old_table = deepcopy(payload["score_table"])
        old_table["year"] = 2025
        text = []
        for row in old_table["rows"]:
            row["rank"] += 5000
            quote = f"合成2025表：{row['score']}分，累计{row['rank']}位。"
            text.append(quote)
            row["citation"] = {"source_id": "old-table", "quote": quote}
        payload["historical_score_tables"] = [old_table]
        payload["sources"].append({"source_id": "old-table", "year": 2025,
            "url": "https://example.org/2025-table", "title": "合成历史表", "text": "\n".join(text)})
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 55000)
        self.assertEqual(result["positioning"]["reference_year"], 2026)

    def test_rates_use_historical_cohort_size_and_student_percentile(self):
        profile, payload = report_fixture()
        report = payload["calibration"]["reports"][0]
        text = f"{profile.high_school}综合组800人，630分以上10%，590分以上15%。"
        payload["sources"][-1]["text"] = text
        report["citation"]["quote"] = text
        report["cohort_size"] = 800
        report["metrics"] = [{"kind": "score_rate", "score": score, "rate_percent": rate,
            "citation": report["citation"]} for score, rate in ((630, 10), (590, 15))]
        result = build_planning_brief(profile, payload, research_year=2026)
        # Current 50/400 -> previous 100/800, between counts 80 and 120.
        self.assertEqual(result["positioning"]["central_rank"], 50000)

    def test_single_count_is_a_wide_assumption_and_not_a_top_score_curve(self):
        profile, payload = report_fixture()
        payload["calibration"]["reports"][0]["metrics"].pop()
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertEqual(result["positioning"]["rank_bounds"], [25000, 75000])
        self.assertIn("粗估", result["positioning"]["basis"])

    def test_tier_count_uses_read_benchmark_without_inventing_universal_tier_line(self):
        profile, payload = report_fixture()
        report = payload["calibration"]["reports"][0]
        text = f"{profile.high_school}综合组400人，211层次40人；本科层次60人。"
        payload["sources"][-1]["text"] = text
        report["citation"]["quote"] = text
        report["metrics"] = []
        for label, count, row in (("211", 40, payload["score_table"]["rows"][1]),
                                  ("本科", 60, payload["score_table"]["rows"][3])):
            report["metrics"].append({"kind": "tier_count", "label": label, "count": count,
                "citation": report["citation"], "benchmark": {"year": 2026,
                    "basis": "合成代表校门槛参照，不是统一层次线",
                    "ranks": [{"province_rank": row["rank"], "citation": row["citation"]}]}})
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertIn("代表校参照", result["positioning"]["basis"])

    def test_conflicting_posts_stay_separate_and_widen_reference_bounds(self):
        profile, payload = report_fixture()
        other = deepcopy(payload["calibration"]["reports"][0])
        text = f"{profile.high_school}综合组400人，630分以上30人，590分以上50人。"
        citation = {"source_id": "different-report", "quote": text}
        other["citation"] = citation
        for metric, count in zip(other["metrics"], (30, 50)):
            metric.update(count=count, citation=citation)
        payload["sources"].append({"source_id": "different-report", "year": 2026,
            "url": "https://example.org/different-report", "title": "不同转述", "text": text})
        payload["calibration"]["reports"].append(other)
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(len(result["positioning"]["scenarios"]), 2)
        self.assertEqual(result["positioning"]["central_rank"], 55000)
        self.assertEqual(result["positioning"]["rank_bounds"], [40000, 72000])

    def test_wrong_cohort_or_altered_count_is_rejected(self):
        profile, payload = report_fixture()
        for mutate in (lambda r: r.update(subject_group="物理"),
                       lambda r: r.update(school="另一个学校"),
                       lambda r: r["metrics"][0].update(count=399)):
            modified = deepcopy(payload)
            mutate(modified["calibration"]["reports"][0])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                build_planning_brief(profile, modified, research_year=2026)

    def test_reposts_do_not_duplicate_calibration_scenarios(self):
        profile, payload = report_fixture()
        payload["calibration"]["reports"].append(deepcopy(payload["calibration"]["reports"][0]))
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(len(result["positioning"]["scenarios"]), 1)

    def test_report_school_alias_is_bound_to_original_text_with_identity_reason(self):
        profile, payload = report_fixture()
        report = payload["calibration"]["reports"][0]
        original = payload["sources"][-1]["text"]
        changed = original.replace(profile.high_school, "合成校简称")
        payload["sources"][-1]["text"] = changed
        report["citation"]["quote"] = changed
        report.update(reported_school="合成校简称", school_identity_basis="已核对同省同市学校的公开全称与简称")
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        del report["school_identity_basis"]
        with self.assertRaises(ValueError):
            build_planning_brief(profile, payload, research_year=2026)

    def test_admission_score_only_is_mapped_via_the_matching_year_table(self):
        profile, payload = report_fixture()
        candidate = payload["candidates"][0]
        candidate["threshold_rank"] = None
        candidate["threshold_score"] = 630
        # The admission table gives a score, not a rank; retain both real quotes.
        candidate["citations"] = {"threshold_score": payload["score_table"]["rows"][1]["citation"]}
        result = build_planning_brief(profile, payload, research_year=2026)
        selected = next(item for group in result["ordinary"].values() for item in group
                        if item["school"] == candidate["school"])
        self.assertEqual(selected["threshold_rank"], 40000)
        self.assertTrue(selected["personal_tier"])

    def test_one_social_report_converts_cumulative_counts_without_direct_rank_pairs(self):
        profile, payload = report_fixture()
        try:
            result = build_planning_brief(profile, payload, research_year=2026)
        except ValueError as error:
            self.fail(f"Usable school-report counts must produce a reference: {error}")
        self.assertEqual(result["positioning"]["central_rank"], 50000)
        self.assertEqual(result["positioning"]["score"], 610)
        self.assertIn("喜报", result["positioning"]["basis"])
        self.assertIn("社交", result["positioning"]["basis"])
        self.assertFalse(result["delivery"]["authenticated_research"])
        self.assertEqual([len(g) for g in result["ordinary"].values()], [3, 4, 5])

    def test_top_score_only_keeps_delivery_but_does_not_invent_distribution(self):
        profile, payload = report_fixture()
        payload["calibration"]["reports"][0]["metrics"] = [{"kind": "top_score", "score": 630,
            "citation": payload["calibration"]["reports"][0]["citation"]}]
        for item in payload["candidates"]:
            item["benchmark_tier"] = "冲" if item["threshold_rank"] < 50000 else "稳" if item["threshold_rank"] < 60000 else "保"
        result = build_planning_brief(profile, payload, research_year=2026)
        self.assertIsNone(result["positioning"]["central_rank"])
        self.assertEqual(len(result["ordinary"]["保"]), 5)
        self.assertIn("现阶段行动", result["report_text"])

    def test_confirmed_serialized_profile_starts_and_real_brief_marks_generated(self):
        profile, payload = report_fixture()
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            profile_file = workspace / "profile.json"
            profile_file.write_text(json.dumps(profile.to_dict(), ensure_ascii=False), encoding="utf-8")
            started = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "start",
                "--workspace", directory, "--profile", str(profile_file), "--confirmed"],
                cwd=root, capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(started.returncode, 0, started.stderr)
            status = json.loads(started.stdout)
            self.assertFalse(status["report_generated"])
            inputs = workspace / "brief.json"
            inputs.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            rendered = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "brief",
                "--workspace", directory, "--session", status["session_id"], "--submission", str(inputs)],
                cwd=root, capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            result = json.loads(rendered.stdout)
            self.assertTrue(result["report_generated"])
            self.assertGreater(result["research_summary"]["pending"], 0)
            self.assertEqual(Path(result["report"]).read_text(encoding="utf-8"), result["report_text"])


if __name__ == "__main__":
    unittest.main()
