"""Focused replay checks for OCR submissions through the host facade."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts import host_workflow, planning_session
from scripts.adapters.ocr_rows import OcrExtractedRow, OcrExtractedTable
from tests.test_planning_session_replay_journal import _profile, _report
from tests.test_rank_evidence_bridge import candidate


ROOT = Path(__file__).resolve().parents[1]
OCR_FIXTURE = ROOT / "tests" / "fixtures" / "replay" / "ocr" / "rows.json"


class HostOcrWorkflowTest(unittest.TestCase):
    def ingest_exact_ocr(self, root: Path):
        payload = json.loads(OCR_FIXTURE.read_text(encoding="utf-8"))
        payload["rows"] = [payload["rows"][0], payload["rows"][2]]
        payload["anchors"][1]["row_index"] = 2
        ocr_path = root / "exact-rows.json"
        ocr_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        original = root / "public-score.png"
        original.write_bytes(b"fixture-public-image-bytes")

        workflow = host_workflow.PlanningWorkflow.start(
            root, _profile(), _report(), confirmed=True
        )
        task = max(
            (item for item in workflow.pending() if item.kind == "score_table"),
            key=lambda item: item.year,
        )
        metadata = candidate("ocr-score").to_dict()
        metadata.pop("content_hash")
        workflow.ingest(
            task.task_id,
            {
                "sources": [
                    {
                        "path": str(original),
                        "adapter": "ocr_rows",
                        "candidate": metadata,
                        "options": {
                            "ocr_path": str(ocr_path),
                            "columns": {
                                "score": "Score",
                                "cumulative_count": "Rank",
                            },
                            "roles": {
                                "score": "score",
                                "cumulative_count": "rank",
                            },
                            "score_scale": [0, 750],
                        },
                    }
                ],
                "records": [{"rows": [0], "coverage_status": "official"}],
            },
        )
        return workflow, task

    def test_exact_ocr_ingest_replays_as_the_genuine_subtype(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workflow, task = self.ingest_exact_ocr(root)

            restored = host_workflow.PlanningWorkflow.resume(
                root, workflow.session.session_id
            )
            bridge = restored.context.task_outcomes[0]._bridges[0]

            self.assertIn(task.task_id, restored.session.completed_task_ids)
            self.assertIs(type(bridge.table), OcrExtractedTable)
            self.assertIs(type(bridge.adapter_row), OcrExtractedRow)
            self.assertEqual(bridge.extraction_method, "host-ocr-rows")
            self.assertEqual(bridge.fact.value["score"], 650)
            self.assertEqual(bridge.fact.value["rank"], 100)
            self.assertEqual(
                bridge.adapter_row.cell_locations["score"],
                "page[1]/image[page-1]/bbox[210,20,330,60]",
            )
            self.assertEqual(
                bridge.table.mapping_snapshot["score_scale"], (0, 750)
            )

    def test_ocr_replay_rejects_changed_mapping_and_cell_locator(self):
        with tempfile.TemporaryDirectory() as temporary:
            workflow, _task = self.ingest_exact_ocr(Path(temporary).resolve())
            outcome = workflow.context.task_outcomes[0]
            record = planning_session._task_outcome_journal_record(outcome)

            valid = planning_session._replay_task_outcome_journal_record(
                record, workflow.profile, workflow.plan
            )
            self.assertEqual(valid.receipt_digest, outcome.receipt_digest)

            def changed_record(mutator):
                changed = deepcopy(record)
                origin = json.loads(changed["bridges"][0]["origin"])
                mutator(origin)
                changed["bridges"][0]["origin"] = json.dumps(
                    origin,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                return changed

            changes = (
                lambda origin: origin["table"]["mapping_snapshot"]["columns"].update(
                    score=["Different score column"]
                ),
                lambda origin: origin["table"]["mapping_snapshot"]["columns"].update(
                    score="Score"
                ),
                lambda origin: origin["table"]["mapping_snapshot"].update(
                    unexpected="value"
                ),
                lambda origin: origin["table"]["rows"][0]["cell_locations"].update(
                    score="C:\\private\\score.png"
                ),
            )
            for mutation in changes:
                with self.subTest(mutation=mutation), self.assertRaises(
                    planning_session.PlanningSessionInputError
                ):
                    planning_session._replay_task_outcome_journal_record(
                        changed_record(mutation), workflow.profile, workflow.plan
                    )


if __name__ == "__main__":
    unittest.main()
