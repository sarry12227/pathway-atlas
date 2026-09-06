from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


import scripts.planning_session as planning_session
from scripts.adapters.admission_bridge import bridge_admission_evidence
from scripts.contracts import CapabilityReport, CapabilityTier, EvidenceStatus
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.evidence import EvidenceStore
from scripts.planning_profile import PlanningProfile
from scripts.planning_session import (
    PlanningSession,
    PlanningSessionInputError,
    PlanningSessionReplayJournal,
    SessionStage,
    build_task_evidence_outcome,
)
from scripts.query_plan import build_query_plan, load_province_catalog
from scripts.validate_data import ValidatedAdmissionRow
from tests.test_planning_profile import reference_payload
from tests.test_structured_adapters import _AdmissionEvidenceBridgeContractMixin


def _canonical_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _report() -> CapabilityReport:
    return CapabilityReport(
        tier=CapabilityTier.STANDARD,
        host_capabilities=("browse", "search"),
        available_capabilities=("browse", "search"),
        missing_capabilities=("vision",),
        degradations=("missing capability: vision",),
        python_version="3.10.20",
    )


def _profile() -> PlanningProfile:
    return PlanningProfile.create(reference_payload())


def _query_plan(student: PlanningProfile):
    return build_query_plan(
        student,
        load_province_catalog(),
        DecisionPolicySnapshot.load_default(),
    )


def _plan_digest(plan) -> str:
    return _canonical_digest(plan.to_dict())


def _admission_bridge(student, plan, task):
    _old_task, dataset_row, adapter_row, table = (
        _AdmissionEvidenceBridgeContractMixin._inputs()
    )
    contextual_row = ValidatedAdmissionRow.from_mapping(
        {
            **dataset_row.to_dict(),
            "year": task.year,
            "province": task.province,
            "subject_group": task.subject_group,
        }
    )
    return bridge_admission_evidence(
        table=table,
        adapter_row=adapter_row,
        task=task,
        dataset_row=contextual_row,
        fact_id="journal-admission-row",
        candidates=(_AdmissionEvidenceBridgeContractMixin._candidate(),),
        coverage_status=EvidenceStatus.OFFICIAL,
    )


def _resolved_context(root: Path):
    student = _profile()
    plan = _query_plan(student)
    session = (
        PlanningSession.create("1029384756abcdef1029384756abcdef", student)
        .confirm_profile(student.digest)
        .with_preflight(_report())
        .with_query_plan(plan, profile=student)
    )
    task = max(
        (
            item
            for item in plan.tasks
            if item.kind == "batch_admission" and item.target_name == "普通批"
        ),
        key=lambda item: item.year,
    )
    bridge = _admission_bridge(student, plan, task)
    outcome = build_task_evidence_outcome(student, plan, task, (bridge,))
    bundle_root = root / "evidence"
    bundle_root.mkdir()
    evidence_store = EvidenceStore.create(bundle_root, _report())
    for candidate in bridge.candidates:
        evidence_store.add_candidate(candidate)
    bridge.persist(evidence_store)
    evidence_store.finalize()

    session = session.ingest_task(
        task.task_id,
        query_plan_digest=_plan_digest(plan),
        query_plan=plan,
        profile=student,
        outcome="completed",
        evidence_outcome=outcome,
    )
    for pending in plan.tasks:
        if pending.task_id == task.task_id:
            continue
        session = session.ingest_task(
            pending.task_id,
            query_plan_digest=_plan_digest(plan),
            query_plan=plan,
            profile=student,
            outcome="unavailable",
            unavailable_reason="source_threshold_not_met",
        )
    return student, plan, session, outcome, evidence_store.session_path


class PlanningSessionReplayJournalTest(unittest.TestCase):
    def test_recovery_factories_and_context_are_public_module_exports(self):
        expected = {
            "PlanningSessionReplayContext",
            "PlanningSessionReplayJournal",
            "build_task_evidence_outcome",
            "build_evidence_manifest_outcome",
            "build_calculation_outcome",
            "build_report_publication_outcome",
        }

        self.assertLessEqual(expected, set(planning_session.__all__))
        public_namespace = {}
        exec("from scripts.planning_session import *", {}, public_namespace)
        self.assertLessEqual(expected, set(public_namespace))

    def test_pre_research_revisions_restore_only_the_context_owned_at_each_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal_root = root / "journal"
            journal_root.mkdir()
            journal = PlanningSessionReplayJournal(journal_root)
            student = _profile()
            plan = _query_plan(student)
            confirmed = PlanningSession.create(
                "1129384756abcdef1129384756abcdef", student
            ).confirm_profile(student.digest)
            preflight = confirmed.with_preflight(_report())
            query_ready = preflight.with_query_plan(plan, profile=student)

            journal.save(confirmed, profile=student)
            restored_confirmed = journal.load(
                confirmed.session_id, confirmed.revision
            )
            self.assertEqual(restored_confirmed.session.stage, SessionStage.PROFILE_CONFIRMED)
            self.assertEqual(restored_confirmed.profile.to_dict(), student.to_dict())
            self.assertIsNone(restored_confirmed.capability_report)
            self.assertIsNone(restored_confirmed.query_plan)
            self.assertIsNone(restored_confirmed.bundle_path)
            self.assertEqual(restored_confirmed.task_outcomes, ())

            journal.save(
                preflight,
                profile=student,
                capability_report=_report(),
            )
            restored_preflight = journal.load(preflight.session_id, preflight.revision)
            self.assertEqual(restored_preflight.session.stage, SessionStage.PREFLIGHT_COMPLETE)
            self.assertEqual(restored_preflight.capability_report.to_dict(), _report().to_dict())
            self.assertIsNone(restored_preflight.query_plan)
            self.assertIsNone(restored_preflight.bundle_path)
            self.assertEqual(restored_preflight.task_outcomes, ())

            journal.save(
                query_ready,
                profile=student,
                query_plan=plan,
                capability_report=_report(),
            )
            restored_query = journal.load(query_ready.session_id, query_ready.revision)
            self.assertEqual(restored_query.session.stage, SessionStage.QUERY_PLAN_READY)
            self.assertEqual(restored_query.query_plan.to_dict(), plan.to_dict())
            self.assertIsNone(restored_query.bundle_path)
            self.assertEqual(restored_query.task_outcomes, ())
            self.assertEqual(
                tuple(
                    item.task_id
                    for item in restored_query.session.next_tasks(
                        restored_query.query_plan,
                        profile=restored_query.profile,
                    )
                ),
                tuple(item.task_id for item in plan.tasks),
            )

    def test_fresh_python_process_loads_query_ready_and_persists_the_first_outcome(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal_root = root / "journal"
            journal_root.mkdir()
            bundle_path = root / "evidence"
            bundle_path.mkdir()
            journal = PlanningSessionReplayJournal(journal_root)
            student = _profile()
            plan = _query_plan(student)
            query_ready = (
                PlanningSession.create(
                    "1229384756abcdef1229384756abcdef", student
                )
                .confirm_profile(student.digest)
                .with_preflight(_report())
                .with_query_plan(plan, profile=student)
            )
            journal.save(
                query_ready,
                profile=student,
                query_plan=plan,
                capability_report=_report(),
            )
            expected_ids = tuple(item.task_id for item in plan.tasks)

            child = textwrap.dedent(
                """
                import json
                from pathlib import Path
                import sys

                from scripts.planning_session import PlanningSessionReplayJournal

                journal = PlanningSessionReplayJournal(Path(sys.argv[1]))
                context = journal.load(sys.argv[2])
                pending = context.session.next_tasks(
                    context.query_plan,
                    profile=context.profile,
                )
                first = pending[0]
                advanced = context.session.ingest_task(
                    first.task_id,
                    query_plan_digest=context.session.query_plan_digest,
                    query_plan=context.query_plan,
                    profile=context.profile,
                    outcome="unavailable",
                    unavailable_reason="source_threshold_not_met",
                )
                journal.save(
                    advanced,
                    profile=context.profile,
                    query_plan=context.query_plan,
                    capability_report=context.capability_report,
                    bundle_path=Path(sys.argv[3]),
                    task_outcomes=context.task_outcomes,
                )
                remaining = advanced.next_tasks(
                    context.query_plan,
                    profile=context.profile,
                )
                print(json.dumps({
                    "initial_task_id": first.task_id,
                    "next_task_id": remaining[0].task_id,
                    "revision": advanced.revision,
                    "stage": advanced.stage.value,
                }, sort_keys=True))
                """
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child,
                    str(journal_root),
                    query_ready.session_id,
                    str(bundle_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload,
                {
                    "initial_task_id": expected_ids[0],
                    "next_task_id": expected_ids[1],
                    "revision": query_ready.revision + 1,
                    "stage": SessionStage.RESEARCH_IN_PROGRESS.value,
                },
            )

            restored = journal.load(query_ready.session_id)
            self.assertEqual(restored.session.stage, SessionStage.RESEARCH_IN_PROGRESS)
            self.assertEqual(restored.session.unavailable_task_ids, (expected_ids[0],))
            self.assertEqual(restored.task_outcomes, ())
            self.assertEqual(restored.bundle_path, bundle_path)
            self.assertEqual(
                tuple(
                    item.task_id
                    for item in restored.session.next_tasks(
                        restored.query_plan,
                        profile=restored.profile,
                    )
                ),
                expected_ids[1:],
            )

    def test_completed_receipts_survive_all_three_resume_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            student, plan, researching, task_outcome, bundle_path = (
                _resolved_context(root)
            )
            journal_root = root / "journal"
            journal_root.mkdir()
            journal = PlanningSessionReplayJournal(journal_root)

            journal.save(
                researching,
                profile=student,
                query_plan=plan,
                capability_report=_report(),
                bundle_path=bundle_path,
                task_outcomes=(task_outcome,),
            )
            session_id = researching.session_id
            researching_revision = researching.revision
            del student, plan, researching, task_outcome, bundle_path

            restored_research = journal.load(session_id, researching_revision)
            finalized, evidence = restored_research.finalize_evidence()
            self.assertEqual(finalized.stage, SessionStage.EVIDENCE_FINALIZED)
            self.assertEqual(
                evidence.completed_receipt_digests,
                restored_research.session.completed_receipt_digests,
            )

            journal.save(
                finalized,
                profile=restored_research.profile,
                query_plan=restored_research.query_plan,
                capability_report=restored_research.capability_report,
                bundle_path=restored_research.bundle_path,
                task_outcomes=restored_research.task_outcomes,
            )
            restored_evidence = journal.load(finalized.session_id, finalized.revision)
            calculated, calculation = restored_evidence.calculate()
            self.assertEqual(calculated.stage, SessionStage.CALCULATION_COMPLETE)
            self.assertEqual(
                calculation.receipt_digest,
                calculated.calculation_receipt_digest,
            )

            journal.save(
                calculated,
                profile=restored_evidence.profile,
                query_plan=restored_evidence.query_plan,
                capability_report=restored_evidence.capability_report,
                bundle_path=restored_evidence.bundle_path,
                task_outcomes=restored_evidence.task_outcomes,
            )
            restored_calculation = journal.load(
                calculated.session_id, calculated.revision
            )
            published, publication = restored_calculation.publish(format="markdown")
            self.assertEqual(published.stage, SessionStage.REPORT_PUBLISHED)
            self.assertEqual(
                publication.receipt_digest,
                published.publication_receipt_digest,
            )
            self.assertIn("不构成升学建议", publication.markdown)

    def test_serialized_usable_flag_never_authorizes_a_restored_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            student, plan, researching, task_outcome, bundle_path = (
                _resolved_context(root)
            )
            journal_root = root / "journal"
            journal_root.mkdir()
            journal = PlanningSessionReplayJournal(journal_root)
            path = journal.save(
                researching,
                profile=student,
                query_plan=plan,
                capability_report=_report(),
                bundle_path=bundle_path,
                task_outcomes=(task_outcome,),
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("usable", json.dumps(payload["task_receipts"]))
            payload["session"]["completed_usable_flags"] = [False]
            session_body = {
                key: value
                for key, value in payload["session"].items()
                if key != "session_digest"
            }
            payload["session"]["session_digest"] = _canonical_digest(session_body)
            journal_body = {
                key: value for key, value in payload.items() if key != "journal_digest"
            }
            payload["journal_digest"] = _canonical_digest(journal_body)
            path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                PlanningSessionInputError,
                "receipt|ledger|replay",
            ):
                journal.load(researching.session_id, researching.revision)

    def test_coordinated_bridge_and_ledger_rewrite_cannot_finalize_against_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            student, plan, researching, task_outcome, bundle_path = (
                _resolved_context(root)
            )
            journal_root = root / "journal"
            journal_root.mkdir()
            journal = PlanningSessionReplayJournal(journal_root)
            path = journal.save(
                researching,
                profile=student,
                query_plan=plan,
                capability_report=_report(),
                bundle_path=bundle_path,
                task_outcomes=(task_outcome,),
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            origin = json.loads(payload["task_receipts"][0]["bridges"][0]["origin"])
            origin["coverage_status"] = "partial"
            payload["task_receipts"][0]["bridges"][0]["origin"] = json.dumps(
                origin,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            # A coordinated caller can recompute public hashes, but it cannot
            # make the retained evidence bundle agree with a different bridge.
            payload["session"]["completed_usable_flags"] = [False]
            session_body = {
                key: value
                for key, value in payload["session"].items()
                if key != "session_digest"
            }
            payload["session"]["session_digest"] = _canonical_digest(session_body)
            journal_body = {
                key: value for key, value in payload.items() if key != "journal_digest"
            }
            payload["journal_digest"] = _canonical_digest(journal_body)
            path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                PlanningSessionInputError,
                "bundle|receipt|replay",
            ):
                restored = journal.load(researching.session_id, researching.revision)
                restored.finalize_evidence()


if __name__ == "__main__":
    unittest.main()
