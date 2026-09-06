"""Delivery checks for the host API across real process boundaries."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from scripts import host_workflow
from tests.test_planning_session_replay_journal import _profile, _report, _admission_bridge


class HostWorkflowTest(unittest.TestCase):
    def test_school_charter_public_text_flows_through_the_host_entry(self):
        from tests.test_school_fit_evidence_bridge import charter_values, source
        from tests.test_school_fit_public_text_bridge import _document
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            self.assertGreater(workflow.status()["pending"], 3)
            self.assertEqual(len(workflow.status()["next"]), 3)
            task = next(t for t in workflow.pending() if t.kind == "admission_charter")
            candidate = source("public-charter")
            document = _document(candidate, charter_values(workflow.profile, task))
            raw = root / "charter.txt"
            raw.write_bytes(document.text.encode("utf-8"))
            metadata = candidate.to_dict()
            metadata.pop("content_hash")
            fields = {name: {"value": field.value, "quote": field.quote,
                            "start": field.start, "end": field.end, "status": field.status.value}
                      for name, field in document.fields.items()}
            workflow.ingest(task.task_id, {"sources": [{"path": str(raw), "adapter": "public_text",
                "candidate": metadata, "options": {"fields": fields}}]})
            restored = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            bridge = restored.context.task_outcomes[0]._bridges[0]
            self.assertEqual(bridge.metadata["kind"], "admission_charter")
            self.assertEqual(bridge.extraction_method, "host-public-text")

    def test_cli_prose_receipt_keeps_source_and_windows_text_spans_on_resume(self):
        from tests.test_pathway_evidence_bridge import candidate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            task = next(t for t in workflow.pending() if t.kind == "strong_foundation")
            text = f"官方招生简章\n示例高校{task.year}年招生\n报名入口：https://example.edu.cn/apply"
            raw = root / "public.txt"
            raw.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            quote = f"示例高校{task.year}年招生"
            start = text.index(quote)
            metadata = candidate().to_dict()
            metadata.pop("content_hash")
            fields = {"institution": {"value": "示例高校", "quote": quote,
                                      "start": start, "end": start + len(quote)},
                      "year": {"value": task.year, "quote": quote,
                               "start": start, "end": start + len(quote)}}
            submission = root / "submission.json"
            submission.write_text(json.dumps({"sources": [{"candidate": metadata,
                "path": str(raw), "adapter": "public_text", "options": {"fields": fields}}]},
                ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "ingest",
                "--workspace", str(root), "--session", workflow.session.session_id,
                "--task", task.task_id, "--submission", str(submission)],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            projection = restored.context.task_outcomes[0]._bridges[0].projection
            self.assertEqual(projection.institution, "示例高校")
            self.assertEqual(projection.coverage_status, "partial")
            self.assertIsNone(projection.fees_and_subsidies)
            admission = next(t for t in restored.pending() if t.kind == "batch_admission")
            restored.complete(admission.task_id, (_admission_bridge(restored.profile, restored.plan, admission),))
            from scripts.validate_evidence import validate_bundle_snapshot
            snapshot = validate_bundle_snapshot(restored.context.bundle_path).snapshot
            self.assertIn(metadata["source_id"], {s.to_dict()["source_id"] for s in snapshot.candidates})

    def test_cli_ingests_realistic_captionless_score_table(self):
        from tests.test_rank_evidence_bridge import candidate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            task = max((t for t in workflow.pending() if t.kind == "score_table"), key=lambda t: t.year)
            raw = root / "public.html"
            raw.write_text('<table><tr><th>分数</th><th>累计人数</th></tr>'
                '<tr><td>610</td><td>18000</td></tr><tr><td>609</td><td>18500</td></tr></table>', encoding="utf-8")
            metadata = candidate().to_dict()
            metadata.pop("content_hash")
            payload = {"sources": [{"candidate": metadata, "path": str(raw), "adapter": "html",
                "options": {"table_index": 1, "columns": {"score": "分数", "cumulative_count": "累计人数"},
                            "roles": {"cumulative_count": "rank"}, "score_scale": [0,750]}}],
                "records": [{"rows": [0], "coverage_status": "official"}]}
            submission = root / "submission.json"
            submission.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "scripts.host_workflow", "ingest",
                "--workspace", str(root), "--session", workflow.session.session_id,
                "--task", task.task_id, "--submission", str(submission)],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            self.assertIn(task.task_id, restored.session.completed_task_ids)
            self.assertEqual(restored.context.task_outcomes[0]._bridges[0].fact.value["rank"], 18000)
            older = next(t for t in restored.pending() if t.kind == task.kind and t.year < task.year)
            restored.unavailable([older.task_id], reason="newer_comparable_year_accepted", newer_task=task.task_id)
            self.assertIn(older.task_id, restored.session.unavailable_task_ids)

    def test_confirmation_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "confirm"):
                host_workflow.PlanningWorkflow.start(Path(tmp), _profile(), _report(), confirmed=False)

    def test_completed_source_survives_resume_and_writes_actual_report(self):
        from scripts.questionnaire_intake import build_profile_from_questionnaire
        from tests.test_questionnaire_intake import structured_answers
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile = build_profile_from_questionnaire(structured_answers())
            workflow = host_workflow.PlanningWorkflow.start(root, profile, _report(), confirmed=True)
            session_id = workflow.session.session_id
            task = next(t for t in workflow.pending() if t.kind == "batch_admission")
            bridge = _admission_bridge(workflow.profile, workflow.plan, task)
            workflow.complete(task.task_id, (bridge,))
            restored = host_workflow.PlanningWorkflow.resume(root, session_id)
            self.assertIn(task.task_id, restored.session.completed_task_ids)
            self.assertEqual(len(restored.context.task_outcomes), 1)
            remaining = [t.task_id for t in restored.pending()]
            restored.unavailable(remaining, reason="source_threshold_not_met")
            run = subprocess.run(
                [sys.executable, "-m", "scripts.host_workflow", "finish", "--workspace", str(root),
                 "--session", session_id], capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            published = json.loads(run.stdout)
            self.assertTrue(published["sources"])
            self.assertTrue(published["sources"][0]["url"].startswith("https://"))
            artifact = root / "reports" / (session_id + ".md")
            self.assertTrue(artifact.is_file(), run.stdout)
            content = artifact.read_text(encoding="utf-8")
            self.assertIn("当前最需要做", content)
            self.assertIn("多元", content)
            original = artifact.read_bytes()
            # An interrupted caller can repeat finish without overwriting a result.
            result = host_workflow.PlanningWorkflow.resume(root, session_id).finish()
            self.assertEqual(result.read_bytes(), original)

    def test_unfinished_research_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow = host_workflow.PlanningWorkflow.start(Path(tmp), _profile(), _report(), confirmed=True)
            with self.assertRaisesRegex(ValueError, "research"):
                workflow.finish()


if __name__ == "__main__":
    unittest.main()
