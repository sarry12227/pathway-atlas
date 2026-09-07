"""Synthetic saved documents test the host boundary, never live admissions data."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import host_workflow
from tests.test_document_fallback_adapters import _synthetic_xls, PDF_FIXTURE
from tests.test_planning_session_replay_journal import _profile, _report
from tests.test_rank_evidence_bridge import candidate


def _source(path, adapter, options):
    metadata = candidate().to_dict()
    metadata.pop("content_hash")
    return {"path": str(path), "adapter": adapter, "candidate": metadata, "options": options}


class HostDocumentFallbackTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("xlrd"), "optional xlrd unavailable")
    def test_cross_school_xls_admission_rows_keep_source_order_through_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source_path = root / "synthetic-admission.xls"
            raw = _synthetic_xls(unordered=True, admission=True)
            source_path.write_bytes(raw)
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            task = next(t for t in workflow.pending() if t.kind == "batch_admission")
            workflow.ingest(task.task_id, {"sources": [_source(source_path, "xls", {
                "sheet": "Synthetic", "columns": {
                    "school_code": "SchoolCode", "school_name": "Name", "program_group": "ProgramGroup",
                    "min_score": "Score", "min_rank": "Rank",
                }, "roles": {"min_score": "score", "min_rank": "rank"}, "score_scale": [0, 750],
            })], "records": [{"rows": [index], "coverage_status": "official"} for index in range(3)]})
            replayed = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            bridges = replayed.context.task_outcomes[0]._bridges
            self.assertEqual(len(bridges), 3)
            by_code = {bridge.dataset_row.school_code: bridge for bridge in bridges}
            for code, name, score, rank, location in (
                ("0001", "Synthetic A", 630, 101, "Synthetic!A2:E2"),
                ("0002", "Synthetic B", 650, 51, "Synthetic!A3:E3"),
                ("0003", "Synthetic C", 615, 202, "Synthetic!A4:E4"),
            ):
                bridge = by_code[code]
                self.assertEqual(bridge.dataset_row.school_name, name)
                self.assertEqual(bridge.dataset_row.min_score, score)
                self.assertEqual(bridge.dataset_row.min_rank, rank)
                self.assertEqual(bridge.adapter_row.location, location)
                self.assertIn(location, bridge.locator)
                self.assertEqual([row.values["school_code"] for row in bridge.table.rows], ["0001", "0002", "0003"])
                self.assertEqual([row.values["min_score"] for row in bridge.table.rows], [630, 650, 615])
                self.assertEqual(bridge.extraction_method, "xls-worksheet")
            self.assertEqual(source_path.read_bytes(), raw)
            self.assertEqual(replayed.session.completed_task_ids, (task.task_id,))

    def test_saved_pdf_numeric_rows_reach_rank_receipt_with_page_and_line(self):
        from tests.test_pdf_table_fallback import synthetic_pdf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source_path = root / "synthetic.pdf"
            synthetic_pdf(source_path, [["Synthetic score table", "Score Count Cumulative",
                                         "650 5 100", "620 320 420"]])
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            task = next(t for t in workflow.pending() if t.kind == "score_table")
            workflow.ingest(task.task_id, {"sources": [_source(source_path, "pdf_table", {
                "columns": {"score": "Score", "cumulative_count": "Cumulative"},
                "roles": {"cumulative_count": "rank"}, "score_scale": [0, 750],
                "headers": ["Score", "Count", "Cumulative"],
                "page_number": 1, "header_line": 2, "first_data_line": 3, "last_data_line": 4,
                "column_group": 1,
            })], "records": [{"rows": [0], "coverage_status": "official"}]})
            replayed = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            bridge = replayed.context.task_outcomes[0]._bridges[0]
            self.assertEqual(bridge.fact.value["rank"], 100)
            self.assertEqual(bridge.extraction_method, "pdf-text-table")
            self.assertIn("page[1]/line[3]", bridge.locator)
            # Official source status does not make the selected range a whole cohort.
            self.assertEqual(bridge.fact.value["coverage_min_score"], 620)
            self.assertEqual(bridge.fact.value["coverage_max_score"], 650)
            self.assertNotIn("cohort_size", bridge.fact.value)

    @unittest.skipUnless(importlib.util.find_spec("xlrd"), "optional xlrd unavailable")
    def test_saved_xls_rank_row_reaches_replay_with_original_source_hash(self):
        import hashlib
        from scripts.validate_evidence import validate_bundle_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source_path = root / "synthetic.xls"
            raw = _synthetic_xls()
            source_path.write_bytes(raw)
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            task = next(t for t in workflow.pending() if t.kind == "score_table")
            workflow.ingest(task.task_id, {"sources": [_source(source_path, "xls", {
                "sheet": "Synthetic", "columns": {"score": "Score", "cumulative_count": "Rank"},
                "roles": {"cumulative_count": "rank"}, "score_scale": [0, 750],
            })], "records": [{"rows": [0], "coverage_status": "official"}]})
            replayed = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            bridge = replayed.context.task_outcomes[0]._bridges[0]
            self.assertEqual(bridge.fact.value["rank"], 101)
            self.assertEqual(bridge.extraction_method, "xls-worksheet")
            snapshot = validate_bundle_snapshot(replayed.context.bundle_path).snapshot
            self.assertEqual(snapshot.candidates[0].to_dict()["content_hash"], "sha256:" + hashlib.sha256(raw).hexdigest())

    def test_saved_pdf_partial_policy_keeps_missing_fields_and_page_locators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
            task = next(t for t in workflow.pending() if t.kind == "strong_foundation")
            workflow.ingest(task.task_id, {"sources": [_source(PDF_FIXTURE, "pdf_text", {
                "field_map": {"institution": [1, "Synthetic Admission Snapshot"]},
            })]})
            replayed = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
            projection = replayed.context.task_outcomes[0]._bridges[0].projection
            self.assertEqual(projection.institution, "Synthetic Admission Snapshot")
            self.assertNotEqual(projection.coverage_status, "complete")
            self.assertIsNone(projection.fees_and_subsidies)
            institution = next(item for item in projection.field_provenance if item.field == "institution")
            self.assertTrue(all(value.startswith("page[1]/text[") for value in institution.locators))
            self.assertEqual(replayed.status()["older_year_resolution"], [])

    def test_missing_document_parsers_return_exit_three_and_keep_task_pending(self):
        for adapter, modules in (("xls", {"xlrd": None}), ("pdf_text", {"pdfplumber": None, "pypdf": None})):
            with self.subTest(adapter=adapter), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                workflow = host_workflow.PlanningWorkflow.start(root, _profile(), _report(), confirmed=True)
                kind = "score_table" if adapter == "xls" else "strong_foundation"
                task = next(t for t in workflow.pending() if t.kind == kind)
                source_path = PDF_FIXTURE
                options = {"field_map": {"institution": [1, "Synthetic Admission Snapshot"]}}
                if adapter == "xls":
                    source_path = root / "synthetic.xls"
                    source_path.write_bytes(_synthetic_xls())
                    options = {"sheet": "Synthetic", "columns": {"score": "Score", "rank": "Rank"}}
                submission = root / "submission.json"
                submission.write_text(json.dumps({"sources": [_source(source_path, adapter, options)],
                    "records": [{"rows": [0]}]}), encoding="utf-8")
                before = workflow.session.to_dict()
                output, errors = StringIO(), StringIO()
                with mock.patch.dict("sys.modules", modules), redirect_stdout(output), redirect_stderr(errors):
                    code = host_workflow.main(["ingest", "--workspace", str(root), "--session",
                        workflow.session.session_id, "--task", task.task_id, "--submission", str(submission)])
                self.assertEqual(code, 3, errors.getvalue())
                self.assertNotIn(str(root), errors.getvalue())
                replayed = host_workflow.PlanningWorkflow.resume(root, workflow.session.session_id)
                self.assertEqual(replayed.session.to_dict(), before)
                self.assertIn(task.task_id, [item.task_id for item in replayed.pending()])


if __name__ == "__main__":
    unittest.main()
