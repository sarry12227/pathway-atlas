"""Synthetic regressions for Zhejiang's seven-subject discovery and delivery."""

from itertools import combinations, permutations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.host_workflow import PlanningWorkflow
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.planning_profile import PlanningProfile
from scripts.province_registry import SubjectSelectionError, canonical_discovery_subject_key
from scripts.query_plan import build_query_plan, load_province_catalog, validate_query_plan_payload
from tests.test_planning_profile import reference_payload
from tests.test_planning_session_replay_journal import _report, _admission_bridge


def zhejiang_profile(selection=("物理", "地理", "技术"), **changes):
    payload = reference_payload()
    payload.update(province="浙江", city="杭州", subject_mode="3+3",
                   subject_group=selection[0], secondary_subjects=list(selection[1:]),
                   score_basis="赋分")
    payload.update(changes)
    return PlanningProfile.create(payload)


class ZhejiangSubjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_province_catalog()
        cls.policy = DecisionPolicySnapshot.load_default()

    def build(self, selection=("物理", "地理", "技术"), **changes):
        return build_query_plan(zhejiang_profile(selection, **changes), self.catalog, self.policy)

    def test_all_35_zhejiang_combinations_produce_exact_subject_queries(self):
        # Official seven subjects, ordered independently to preserve existing six-subject keys.
        subjects = ("物理", "化学", "生物", "政治", "历史", "地理", "技术")
        count = 0
        for selection in combinations(subjects, 3):
            with self.subTest(selection=selection):
                plan = self.build(selection)
                self.assertEqual(plan.subject_group, "+".join(selection))
                for task in plan.tasks:
                    self.assertEqual(task.subject_group, "+".join(selection))
                    self.assertTrue(all("+".join(selection) in q for q in task.query_variants))
                self.assertEqual(validate_query_plan_payload(plan.to_dict()).to_dict(), plan.to_dict())
                count += 1
        self.assertEqual(count, 35)

    def test_technology_in_any_of_three_slots_keeps_query_ids_stable(self):
        ids = None
        for selection in permutations(("物理", "地理", "技术")):
            with self.subTest(selection=selection):
                plan = self.build(selection)
                self.assertEqual(plan.subject_group, "物理+地理+技术")
                current = tuple(task.task_id for task in plan.tasks)
                if ids is None:
                    ids = current
                self.assertEqual(current, ids)

    def test_technology_remains_invalid_in_other_catalog_provinces(self):
        for entry in self.catalog.provinces:
            if entry.province == "浙江":
                continue
            with self.subTest(province=entry.province), self.assertRaises(SubjectSelectionError):
                self.build(province=entry.province, subject_mode=entry.mode)

    def test_unknown_duplicate_or_incomplete_subjects_still_fail(self):
        for selection in (("物理", "技术", "技术"), ("物理", "地理", "天文"),
                          ("物理", "技术"), ("物理", "化学", "地理", "技术")):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.build(selection)

    def test_research_snapshot_config_accepts_the_same_technology_key(self):
        from scripts.province_registry import canonical_subject_selection_key
        from scripts.research_snapshot import build_research_snapshot
        student = zhejiang_profile()
        plan = self.build()
        task = next(t for t in plan.tasks if t.kind == "batch_admission")
        snapshot = build_research_snapshot(student, plan,
            (_admission_bridge(student, plan, task),), self.policy)
        self.assertEqual(canonical_subject_selection_key(snapshot.config,
            student.subject_group, student.secondary_subjects), "物理+地理+技术")

    def test_pathway_subject_requirements_do_not_treat_technology_as_chemistry(self):
        from tests.test_path_recommend_generic import evaluate_pathways, pathway_policy, exact_rank
        payload = zhejiang_profile(grade="高三", exam_year=2026,
            eligibility_facts=["完成高考报名"]).to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload["constraints"]["service_commitment"] = "accept"
        payload["pathway_preferences"]["service_oriented"] = "interested"
        student = PlanningProfile.create(payload)
        for requirement, expected in (("物理和技术", "PATH_ACADEMIC_MATCH"),
                                      ("物理和化学", "PATH_ACADEMIC_SUBJECT_BLOCKED"),
                                      ("物理/化学", "PATH_ACADEMIC_SUBJECT_UNCERTAIN")):
            with self.subTest(requirement=requirement):
                policy = pathway_policy(province="浙江", subject_mode="3+3",
                                        subject_requirements=(requirement,))
                result = evaluate_pathways(student, (policy,), rank_scenario=exact_rank()).items[0]
                self.assertEqual(next(r.code for r in result.decision_reasons
                                      if r.dimension == "academic_fit"), expected)
                if expected == "PATH_ACADEMIC_SUBJECT_BLOCKED":
                    self.assertEqual(result.investment_decision, "不建议")

    def test_discovery_keeps_legacy_keys_and_requires_zhejiang_for_technology(self):
        self.assertEqual(canonical_discovery_subject_key("3+3", "地理", ["化学", "物理"]),
                         "物理+化学+地理")
        self.assertEqual(canonical_discovery_subject_key("3+1+2", "物理", ["地理", "化学"]),
                         "物理+化学+地理")
        for province in ("浙江", "浙江省"):
            self.assertEqual(canonical_discovery_subject_key(
                "3+3", "技术", ["地理", "物理"], province=province), "物理+地理+技术")
        with self.assertRaises(SubjectSelectionError):
            canonical_discovery_subject_key("3+3", "技术", ["地理", "物理"])

    def test_technology_survives_evidence_resume_and_cli_report_text(self):
        from tests.test_rank_evidence_bridge import candidate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workflow = PlanningWorkflow.start(root, zhejiang_profile(), _report(), confirmed=True)
            session_id = workflow.session.session_id
            task = next(t for t in workflow.pending() if t.kind == "score_table")
            raw = root / "synthetic-score.html"
            raw.write_text('<table><tr><th>分数</th><th>累计人数</th></tr>'
                           '<tr><td>610</td><td>18000</td></tr>'
                           '<tr><td>609</td><td>18500</td></tr></table>', encoding="utf-8")
            metadata = candidate(publisher="浙江省教育考试院", host="www.zjzs.net").to_dict()
            metadata.pop("content_hash")
            workflow.ingest(task.task_id, {"sources": [{"candidate": metadata, "path": str(raw),
                "adapter": "html", "options": {"table_index": 1,
                "columns": {"score": "分数", "cumulative_count": "累计人数"},
                "roles": {"cumulative_count": "rank"}, "score_scale": [0, 750]}}],
                "records": [{"rows": [0], "coverage_status": "official"}]})
            restored = PlanningWorkflow.resume(root, session_id)
            self.assertIn(task.task_id, restored.session.completed_task_ids)
            self.assertEqual(restored.plan.subject_group, "物理+地理+技术")
            admission = next(t for t in restored.pending() if t.kind == "batch_admission")
            restored.complete(admission.task_id, (_admission_bridge(restored.profile, restored.plan, admission),))
            restored.unavailable([t.task_id for t in restored.pending()], reason="source_threshold_not_met")
            run = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "finish",
                "--workspace", str(root), "--session", session_id], capture_output=True,
                text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)
            published = json.loads(run.stdout)
            self.assertIn("物理+地理+技术", published["report_text"])
            self.assertEqual(Path(published["report"]).read_text(encoding="utf-8"), published["report_text"])
            final = PlanningWorkflow.resume(root, session_id)
            self.assertEqual(final.profile.subject_group, "物理")
            self.assertEqual(set(final.profile.secondary_subjects), {"地理", "技术"})
            self.assertEqual(final.report_text(), published["report_text"])

    def test_school_requirements_consider_all_three_slots_without_inventing_chemistry(self):
        from tests.test_school_recommend_generic import (
            authenticated_school_fit_row, planning_profile, exact_rank, ordinary_policy,
            personalize_school_recommendations,
        )
        rows = [authenticated_school_fit_row(enrollment=True, subject=True,
            province="浙江", subject_group="物理+地理+技术", school_code=code, school_name=name,
            required_secondary_subjects=required, secondary_subject_rule="all")
            for code, name, required in (
                ("Z001", "合成技术条件大学", ["技术"]),
                ("Z002", "合成物理技术大学", ["物理", "技术"]),
                ("Z003", "合成物化条件大学", ["物理", "化学"]),
            )]
        for selection in permutations(("物理", "地理", "技术")):
            for policy in (None, ordinary_policy()):
                with self.subTest(selection=selection, policy=policy is not None):
                    student = planning_profile(province="浙江", subject_mode="3+3",
                        subject_group=selection[0], secondary_subjects=list(selection[1:]))
                    result = personalize_school_recommendations(rows, student, policy,
                        rank_scenario=exact_rank(), subject_selection_key="物理+地理+技术")
                    self.assertEqual({item.school_name for item in result.items},
                                     {"合成技术条件大学", "合成物理技术大学"})
                    self.assertIn("SCHOOL_SUBJECT_MISMATCH", {
                        r.code for r in result.decision("合成物化条件大学").reasons})


if __name__ == "__main__":
    unittest.main()
