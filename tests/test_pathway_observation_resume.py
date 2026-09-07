"""Synthetic public prose must survive a real journal and CLI finish process."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.host_workflow import PlanningWorkflow
from scripts.planning_session import SessionStage
from tests.test_pathway_evidence_bridge import candidate
from tests.test_planning_session_replay_journal import _report
from tests.test_zhejiang_subjects import zhejiang_profile


ROOT = Path(__file__).resolve().parents[1]


class PathwayObservationResumeTest(unittest.TestCase):
    def test_unsorted_partial_majors_finish_from_an_existing_calculation_checkpoint(self):
        # Entirely synthetic: this verifies the engine, not a real admissions claim.
        majors = ("信息与计算科学", "生物科学", "化学", "历史学", "哲学")
        profile = zhejiang_profile(
            grade="高一", exam_year=2029, best_rank=None, usual_rank=None,
            rank_observations=[{
                "exam_date": "2026-09-01", "scope": "school", "score": 600,
                "max_score": 750, "rank": None, "cohort_size": None,
            }],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workflow = PlanningWorkflow.start(root, profile, _report(), confirmed=True)
            session_id = workflow.session.session_id
            task = max((t for t in workflow.pending() if t.kind == "strong_foundation"),
                       key=lambda t: t.year)
            quote = "招生专业包括" + "、".join(majors)
            text = f"合成测试高校{task.year}年招生简章。\n{quote}。\n仅供程序回归测试。"
            raw = root / "synthetic-public-notice.txt"
            raw.write_bytes(text.encode("utf-8"))
            metadata = candidate(source_id="synthetic-partial-majors",
                                 publisher="合成测试高校", host="policy.example").to_dict()
            metadata.pop("content_hash")
            values = {
                "institution": ("合成测试高校", "合成测试高校"),
                "year": (task.year, f"{task.year}年"),
                "professional_options": (list(majors), quote),
            }
            fields = {name: {"value": value, "quote": excerpt,
                             "start": text.index(excerpt),
                             "end": text.index(excerpt) + len(excerpt)}
                      for name, (value, excerpt) in values.items()}
            workflow.ingest(task.task_id, {"sources": [{
                "candidate": metadata, "path": str(raw), "adapter": "public_text",
                "options": {"fields": fields},
            }]})
            workflow.unavailable([t.task_id for t in workflow.pending()],
                                 reason="capability_unavailable")
            original_projection = workflow.context.task_outcomes[0]._bridges[0].projection
            self.assertEqual(original_projection.professional_options, majors)
            self.assertEqual(original_projection.coverage_status, "partial")
            original_projection_payload = original_projection.to_dict()

            # This is the durable stage left by finish when calculation failed.
            session, _ = workflow.context.finalize_evidence()
            checkpoint = replace(workflow.context, session=session)
            workflow.journal.save(
                session, profile=checkpoint.profile, query_plan=checkpoint.query_plan,
                capability_report=checkpoint.capability_report,
                bundle_path=checkpoint.bundle_path, task_outcomes=checkpoint.task_outcomes,
            )
            saved_journals = {path: path.read_bytes() for path in (root / "journal").glob("*.json")}
            saved_bundle = {path: path.read_bytes() for path in checkpoint.bundle_path.rglob("*")
                            if path.is_file()}
            self.assertTrue(saved_journals)
            self.assertTrue(saved_bundle)
            command = [sys.executable, "-m", "scripts.host_workflow", "finish",
                       "--workspace", str(root), "--session", session_id]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            published = json.loads(run.stdout)
            content = published["report_text"]
            for major in majors:
                self.assertIn(major, content)
            self.assertIn("物理+地理+技术", content)
            self.assertIn("暂无可靠位次", content)
            self.assertIn("待核验", content)
            self.assertEqual(published["delivery"]["mode"], "partial")
            self.assertTrue(published["delivery"]["degraded"])
            self.assertEqual(published["research_summary"]["completed"], 1)
            self.assertEqual(published["research_summary"]["pending"], 0)
            self.assertEqual(published["research_summary"]["unavailable"], len(workflow.plan.tasks) - 1)
            self.assertEqual(Path(published["report"]).read_text(encoding="utf-8"), content)
            self.assertEqual([item["url"] for item in published["sources"]], [metadata["url"]])

            restored = PlanningWorkflow.resume(root, session_id)
            self.assertIs(restored.session.stage, SessionStage.CALCULATION_COMPLETE)
            self.assertEqual(restored.context.task_outcomes[0]._bridges[0].projection.to_dict(),
                             original_projection_payload)
            for path, original_bytes in {**saved_journals, **saved_bundle}.items():
                self.assertEqual(path.read_bytes(), original_bytes, str(path))
            self.assertEqual(raw.read_bytes(), text.encode("utf-8"))

            # Repeating finish in yet another process must not rewrite its receipt or report.
            final_journals = {path: path.read_bytes() for path in (root / "journal").glob("*.json")}
            report_bytes = Path(published["report"]).read_bytes()
            repeated = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                      encoding="utf-8", timeout=120)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout), published)
            self.assertEqual(Path(published["report"]).read_bytes(), report_bytes)
            self.assertEqual({path: path.read_bytes() for path in (root / "journal").glob("*.json")},
                             final_journals)


if __name__ == "__main__":
    unittest.main()
