from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from contracts import CapabilityReport, CapabilityTier  # noqa: E402
from planning_session import (  # noqa: E402
    CalculationOutcome,
    EvidenceManifestOutcome,
    TaskEvidenceOutcome,
    build_calculation_outcome,
    build_evidence_manifest_outcome,
    build_report_publication_outcome,
    build_task_evidence_outcome,
    PlanningSession,
    PlanningSessionInputError,
    PlanningSessionStore,
    PlanningSessionStoreError,
    SessionStage,
    SessionTransitionError,
)


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def report() -> CapabilityReport:
    return CapabilityReport(
        tier=CapabilityTier.STANDARD,
        host_capabilities=("browse", "search"),
        available_capabilities=("browse", "search"),
        missing_capabilities=("vision",),
        degradations=("missing capability: vision",),
        python_version="3.10.20",
    )


def planning_profile():
    from planning_profile import PlanningProfile
    from tests.test_planning_profile import reference_payload

    return PlanningProfile.create(reference_payload())


def query_plan(profile=None):
    from decision_policy import DecisionPolicySnapshot
    from query_plan import build_query_plan, load_province_catalog

    return build_query_plan(
        planning_profile() if profile is None else profile,
        load_province_catalog(),
        DecisionPolicySnapshot.load_default(),
    )


def plan_digest(plan=None) -> str:
    active = query_plan() if plan is None else plan
    encoded = json.dumps(
        active.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def ready_session() -> PlanningSession:
    student = planning_profile()
    plan = query_plan(student)
    return (
        PlanningSession.create("0123456789abcdef0123456789abcdef", student)
        .confirm_profile(student.digest)
        .with_preflight(report())
        .with_query_plan(plan, profile=student)
    )


def accepted_pathway_outcome():
    from scripts.adapters.pathway_bridge import bridge_pathway_policy_evidence
    from tests.test_pathway_evidence_bridge import profile, plan, project, task_for

    student = profile()
    active_plan = plan(student)
    task = task_for(active_plan)
    bridge = bridge_pathway_policy_evidence(
        project(student=student, query_plan=active_plan, task=task)
    )
    outcome = build_task_evidence_outcome(
        student, active_plan, task, (bridge,)
    )
    session = (
        PlanningSession.create(
            "fedcba9876543210fedcba9876543210", student
        )
        .confirm_profile(student.digest)
        .with_preflight(report())
        .with_query_plan(active_plan, profile=student)
    )
    return student, active_plan, task, outcome, session


def pathway_outcome_for(student, active_plan, task, *, projection=None):
    from scripts.adapters.pathway_bridge import bridge_pathway_policy_evidence
    from tests.test_pathway_evidence_bridge import project

    bridge = bridge_pathway_policy_evidence(
        projection
        or project(student=student, query_plan=active_plan, task=task)
    )
    return build_task_evidence_outcome(
        student, active_plan, task, (bridge,)
    )


def admission_bridge_for_receipt(
    student,
    active_plan,
    task,
    *,
    coverage_status=None,
):
    from copy import copy

    from scripts.adapters.admission_bridge import bridge_admission_evidence
    from scripts.contracts import EvidenceStatus
    from scripts.validate_data import ValidatedAdmissionRow
    from tests.test_structured_adapters import _AdmissionEvidenceBridgeContractMixin

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
        task=copy(task),
        dataset_row=contextual_row,
        fact_id="receipt-admission-row",
        candidates=(_AdmissionEvidenceBridgeContractMixin._candidate(),),
        coverage_status=coverage_status or EvidenceStatus.OFFICIAL,
    )


def rehash_snapshot(payload: dict[str, object]) -> dict[str, object]:
    body = {
        key: value for key, value in payload.items() if key != "session_digest"
    }
    payload["session_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return payload


def completed_outcome(
    session: PlanningSession,
    task_id: str,
    *,
    profile=None,
    plan=None,
) -> PlanningSession:
    active_profile = planning_profile() if profile is None else profile
    active_plan = query_plan(active_profile) if plan is None else plan
    return session.ingest_task(
        task_id,
        query_plan_digest=plan_digest(active_plan),
        query_plan=active_plan,
        profile=active_profile,
        outcome="completed",
        artifact_digest=digest("artifact-" + task_id),
        provenance_digest=digest("provenance-" + task_id),
    )


def fully_resolved_session() -> PlanningSession:
    student = planning_profile()
    plan = query_plan(student)
    session = (
        PlanningSession.create("0123456789abcdef0123456789abcdef", student)
        .confirm_profile(student.digest)
        .with_preflight(report())
        .with_query_plan(plan, profile=student)
    )
    for task in plan.tasks:
        session = session.ingest_task(
            task.task_id,
            query_plan_digest=plan_digest(plan),
            query_plan=plan,
            profile=student,
            outcome="unavailable",
            unavailable_reason="current_year_not_published",
        )
    return session


def empty_evidence_outcome(
    session: PlanningSession,
    student,
    active_plan,
    root: Path,
) -> EvidenceManifestOutcome:
    """Build the only CLI-safe zero-completion manifest from a validated bundle."""

    from evidence import EvidenceStore

    root.mkdir(parents=True)
    store = EvidenceStore.create(root, report())
    store.finalize()
    return build_evidence_manifest_outcome(
        session,
        student,
        active_plan,
        bundle_path=store.session_path,
        task_outcomes=(),
        capability_report=report(),
    )


def all_unavailable_chain(root: Path):
    student = planning_profile()
    active_plan = query_plan(student)
    resolved = fully_resolved_session()
    evidence_outcome = empty_evidence_outcome(
        resolved, student, active_plan, root
    )
    finalized = resolved.finalize_evidence(
        evidence_outcome,
        query_plan=active_plan,
        profile=student,
    )
    calculation_outcome = build_calculation_outcome(
        finalized,
        evidence_outcome,
        student,
        active_plan,
    )
    calculated = finalized.with_calculation(
        calculation_outcome,
        query_plan=active_plan,
        profile=student,
    )
    publication_outcome = build_report_publication_outcome(calculation_outcome)
    published = calculated.publish_report(
        publication_outcome,
        query_plan=active_plan,
        profile=student,
    )
    return (
        student,
        active_plan,
        resolved,
        evidence_outcome,
        finalized,
        calculation_outcome,
        calculated,
        publication_outcome,
        published,
    )


def finalized_payload_with_null_completed_receipt(root: Path) -> dict[str, object]:
    """Forge the structurally aligned state no real finalize can produce."""

    finalized = all_unavailable_chain(root)[4]
    payload = finalized.to_dict()
    task_id = payload["unavailable_task_ids"].pop(0)
    payload["unavailable_reason_codes"].pop(0)
    payload["completed_task_ids"] = [task_id]
    payload["completed_artifact_digests"] = [digest("raw-artifact")]
    payload["completed_provenance_digests"] = [digest("raw-provenance")]
    payload["completed_usable_flags"] = [False]
    payload["completed_receipt_digests"] = [None]
    return rehash_snapshot(payload)


class PlanningSessionTransitionTest(unittest.TestCase):
    def test_partial_admission_receipt_is_canonical_but_never_usable_or_skippable(self):
        from scripts.contracts import EvidenceStatus
        from tests.test_pathway_evidence_bridge import profile, plan

        student = profile()
        active_plan = plan(student)
        family = tuple(
            item
            for item in active_plan.tasks
            if item.kind == "batch_admission" and item.target_name == "普通批"
        )
        newer, older = family[:2]
        bridge = admission_bridge_for_receipt(
            student,
            active_plan,
            newer,
            coverage_status=EvidenceStatus.PARTIAL,
        )
        receipt = build_task_evidence_outcome(
            student, active_plan, newer, (bridge,)
        )
        self.assertFalse(receipt.usable)

        ready = (
            PlanningSession.create(
                "aabbccddeeff00112233445566778899", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        completed = ready.ingest_task(
            newer.task_id,
            query_plan_digest=plan_digest(active_plan),
            query_plan=active_plan,
            profile=student,
            outcome="completed",
            evidence_outcome=receipt,
        )
        self.assertEqual(completed.completed_usable_flags, (False,))
        with self.assertRaises(SessionTransitionError):
            completed.ingest_task(
                older.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="unavailable",
                newer_evidence_outcome=receipt,
                unavailable_reason="newer_comparable_year_accepted",
            )

    def test_receipt_rejects_a_canonical_plan_from_another_profile_context(self):
        from scripts.planning_profile import PlanningProfile
        from tests.test_pathway_evidence_bridge import profile, plan

        hubei = profile()
        hunan_payload = hubei.to_dict()
        hunan_payload.pop("mode")
        hunan_payload.pop("digest")
        hunan_payload["province"] = "湖南"
        hunan_payload["city"] = "长沙"
        hunan = PlanningProfile.create(hunan_payload)
        hunan_plan = plan(hunan)
        task = next(
            item
            for item in hunan_plan.tasks
            if item.kind == "batch_admission" and item.target_name == "普通批"
        )
        bridge = admission_bridge_for_receipt(hunan, hunan_plan, task)

        with self.assertRaisesRegex(
            SessionTransitionError, "profile and query plan"
        ):
            build_task_evidence_outcome(
                hubei,
                hunan_plan,
                task,
                (bridge,),
            )

    def test_query_plan_universe_requires_the_confirmed_canonical_profile(self):
        from scripts.planning_profile import PlanningProfile
        from tests.test_pathway_evidence_bridge import profile, plan

        hubei = profile()
        hunan_payload = hubei.to_dict()
        hunan_payload.pop("mode")
        hunan_payload.pop("digest")
        hunan_payload["province"] = "湖南"
        hunan_payload["city"] = "长沙"
        hunan = PlanningProfile.create(hunan_payload)
        hunan_plan = plan(hunan)
        preflight = (
            PlanningSession.create(
                "8899aabbccddeeff0011223344556677", hubei
            )
            .confirm_profile(hubei.digest)
            .with_preflight(report())
        )

        with self.assertRaisesRegex(
            SessionTransitionError, "profile and query plan"
        ):
            preflight.with_query_plan(hunan_plan, profile=hubei)
        with self.assertRaises(TypeError):
            preflight.with_query_plan(hunan_plan)

    def test_restored_foreign_plan_universe_cannot_finalize_all_unavailable(self):
        from scripts.planning_profile import PlanningProfile
        from tests.test_pathway_evidence_bridge import profile, plan

        hubei = profile()
        hunan_payload = hubei.to_dict()
        hunan_payload.pop("mode")
        hunan_payload.pop("digest")
        hunan_payload["province"] = "湖南"
        hunan_payload["city"] = "长沙"
        hunan = PlanningProfile.create(hunan_payload)
        hubei_plan = plan(hubei)
        hunan_plan = plan(hunan)
        ready = (
            PlanningSession.create(
                "9988aabbccddeeff0011223344556677", hubei
            )
            .confirm_profile(hubei.digest)
            .with_preflight(report())
            .with_query_plan(hubei_plan, profile=hubei)
        )

        # A host can recompute the public snapshot SHA, so the transition seam
        # must replay the typed profile and plan rather than trust these fields.
        restored_payload = ready.to_dict()
        restored_payload["query_plan_digest"] = plan_digest(hunan_plan)
        restored_payload["expected_task_ids"] = sorted(
            task.task_id for task in hunan_plan.tasks
        )
        restored = PlanningSession.from_dict(rehash_snapshot(restored_payload))
        with self.assertRaisesRegex(
            SessionTransitionError, "profile and query plan"
        ):
            restored.next_tasks(hunan_plan, profile=hubei)

        resolved_payload = restored.to_dict()
        resolved_payload["stage"] = SessionStage.RESEARCH_IN_PROGRESS.value
        resolved_payload["revision"] = 3 + len(hunan_plan.tasks)
        resolved_payload["unavailable_task_ids"] = sorted(
            task.task_id for task in hunan_plan.tasks
        )
        resolved_payload["unavailable_reason_codes"] = [
            "source_threshold_not_met"
            for _task in resolved_payload["unavailable_task_ids"]
        ]
        resolved = PlanningSession.from_dict(rehash_snapshot(resolved_payload))
        with self.assertRaisesRegex(
            SessionTransitionError, "profile and query plan"
        ):
            resolved.finalize_evidence(
                digest("foreign-manifest"),
                query_plan=hunan_plan,
                profile=hubei,
            )

    def test_profile_derived_pathway_trace_and_task_universe_are_exactly_bound(self):
        from scripts.planning_profile import PlanningProfile
        from tests.test_pathway_evidence_bridge import profile, plan

        interested_payload = profile().to_dict()
        interested_payload.pop("mode")
        interested_payload.pop("digest")
        interested_payload["pathway_preferences"]["strong_foundation"] = "interested"
        interested = PlanningProfile.create(interested_payload)
        excluded_payload = interested.to_dict()
        excluded_payload.pop("mode")
        excluded_payload.pop("digest")
        excluded_payload["pathway_preferences"]["strong_foundation"] = "not_interested"
        excluded = PlanningProfile.create(excluded_payload)
        excluded_plan = plan(excluded)
        ordinary_task = next(
            task
            for task in excluded_plan.tasks
            if task.kind == "batch_admission" and task.target_name == "普通批"
        )
        ordinary_bridge = admission_bridge_for_receipt(
            excluded, excluded_plan, ordinary_task
        )

        with self.assertRaisesRegex(
            SessionTransitionError, "profile and query plan"
        ):
            build_task_evidence_outcome(
                interested,
                excluded_plan,
                ordinary_task,
                (ordinary_bridge,),
            )

    def test_bare_completed_digests_cannot_cross_the_manifest_boundary(self):
        from tests.test_pathway_evidence_bridge import profile, plan

        student = profile()
        active_plan = plan(student)
        session = (
            PlanningSession.create(
                "7766aabbccddeeff0011223344556677", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        for task in active_plan.tasks:
            session = session.ingest_task(
                task.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="completed",
                artifact_digest=digest("raw-artifact-" + task.task_id),
                provenance_digest=digest("raw-provenance-" + task.task_id),
            )

        with self.assertRaisesRegex(TypeError, "EvidenceManifestOutcome"):
            session.finalize_evidence(
                digest("arbitrary-manifest"),
                query_plan=active_plan,
                profile=student,
            )

    def test_all_unavailable_chain_publishes_only_factory_built_degraded_output(self):
        from evidence import EvidenceStore

        student = planning_profile()
        active_plan = query_plan(student)
        session = (
            PlanningSession.create(
                "6655aabbccddeeff0011223344556677", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        for task in active_plan.tasks:
            session = session.ingest_task(
                task.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="unavailable",
                unavailable_reason="source_threshold_not_met",
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(Path(temporary).resolve(), report())
            store.finalize()
            evidence = build_evidence_manifest_outcome(
                session,
                student,
                active_plan,
                bundle_path=store.session_path,
                task_outcomes=(),
                capability_report=report(),
            )
            self.assertIsInstance(evidence, EvidenceManifestOutcome)
            finalized = session.finalize_evidence(
                evidence,
                query_plan=active_plan,
                profile=student,
            )
            with self.assertRaisesRegex(TypeError, "CalculationOutcome"):
                finalized.with_calculation(
                    digest("arbitrary-calculation"),
                    query_plan=active_plan,
                    profile=student,
                )
            calculation = build_calculation_outcome(
                finalized,
                evidence,
                student,
                active_plan,
            )
            self.assertIsInstance(calculation, CalculationOutcome)
            self.assertTrue(calculation.degraded)
            self.assertEqual(calculation.model.recommendations, ())
            self.assertTrue(calculation.model.pathways)
            self.assertTrue(
                all(
                    item.status == "pending_verification"
                    and item.evidence_status.value == "missing"
                    and not item.source_ids
                    and item.target_year is None
                    and item.data_year is None
                    for item in calculation.model.pathways
                )
            )
            calculated = finalized.with_calculation(
                calculation,
                query_plan=active_plan,
                profile=student,
            )
            with self.assertRaisesRegex(TypeError, "ReportPublicationOutcome"):
                calculated.publish_report(
                    digest("arbitrary-report"),
                    query_plan=active_plan,
                    profile=student,
                )
            publication = build_report_publication_outcome(calculation)
            published = calculated.publish_report(
                publication,
                query_plan=active_plan,
                profile=student,
            )
            self.assertEqual(published.stage, SessionStage.REPORT_PUBLISHED)
            self.assertIn("不构成升学建议", publication.markdown)

    def test_factory_consumes_the_immutable_planning_profile_without_retaining_answers(self):
        from planning_profile import PlanningProfile
        from tests.test_planning_profile import reference_payload

        profile = PlanningProfile.create(reference_payload())
        session = PlanningSession.create(
            "0123456789abcdef0123456789abcdef", profile
        )
        self.assertEqual(session.profile_digest, profile.digest)
        snapshot = session.to_dict()
        self.assertNotIn("province", snapshot)
        self.assertNotIn("priorities", snapshot)

    def test_exact_order_and_immutable_factory_only_records(self):
        student = planning_profile()
        active_plan = query_plan(student)
        session = PlanningSession.create(
            "0123456789abcdef0123456789abcdef", student
        )
        self.assertEqual(session.stage, SessionStage.INTAKE)
        with self.assertRaises(SessionTransitionError):
            session.with_query_plan(active_plan, profile=student)
        with self.assertRaises(TypeError):
            PlanningSession()
        with self.assertRaises((TypeError, FrozenInstanceError)):
            replace(session, revision=99)
        with self.assertRaises(FrozenInstanceError):
            session.revision = 99

        confirmed = session.confirm_profile(student.digest)
        preflight = confirmed.with_preflight(report())
        ready = preflight.with_query_plan(active_plan, profile=student)
        researching = fully_resolved_session()
        resolved_profile = planning_profile()
        resolved_plan = query_plan(resolved_profile)
        with tempfile.TemporaryDirectory() as temporary:
            evidence_outcome = empty_evidence_outcome(
                researching,
                resolved_profile,
                resolved_plan,
                Path(temporary) / "evidence",
            )
            finalized = researching.finalize_evidence(
                evidence_outcome,
                query_plan=resolved_plan,
                profile=resolved_profile,
            )
            calculation_outcome = build_calculation_outcome(
                finalized,
                evidence_outcome,
                resolved_profile,
                resolved_plan,
            )
            calculated = finalized.with_calculation(
                calculation_outcome,
                query_plan=resolved_plan,
                profile=resolved_profile,
            )
            publication_outcome = build_report_publication_outcome(
                calculation_outcome
            )
            published = calculated.publish_report(
                publication_outcome,
                query_plan=resolved_plan,
                profile=resolved_profile,
            )
        self.assertEqual(published.stage, SessionStage.REPORT_PUBLISHED)
        self.assertGreaterEqual(published.revision, 7)

    def test_validated_plan_binds_the_canonical_expected_task_universe(self):
        plan = query_plan()
        ready = ready_session()
        self.assertEqual(
            ready.expected_task_ids,
            tuple(sorted(task.task_id for task in plan.tasks)),
        )
        self.assertEqual(ready.query_plan_digest, plan_digest(plan))
        with self.assertRaises(TypeError):
            (
                PlanningSession.create(
                    "abcdef0123456789abcdef0123456789", digest("profile")
                )
                .confirm_profile(digest("profile"))
                .with_preflight(report())
                .with_query_plan(plan_digest(plan), profile=planning_profile())
            )

    def test_query_plan_requires_the_exact_canonical_typed_factory_record(self):
        plan = query_plan()
        forged_type = type(
            "QueryPlan",
            (),
            {
                "to_dict": lambda _self: plan.to_dict(),
                "tasks": (plan.tasks[0],),
            },
        )
        forged = forged_type()
        preflight = (
            PlanningSession.create(
                "0123456789abcdef0123456789abcdef", digest("profile")
            )
            .confirm_profile(digest("profile"))
            .with_preflight(report())
        )
        with self.assertRaises(TypeError):
            preflight.with_query_plan(forged, profile=planning_profile())

    def test_receipt_replay_rejects_detached_plan_tasks_and_mutated_profiles(self):
        from copy import copy

        student, active_plan, task, outcome, _ready = accepted_pathway_outcome()
        detached = copy(task)
        object.__setattr__(detached, "year", task.year + 1)
        with self.assertRaises(ValueError):
            build_task_evidence_outcome(
                student,
                active_plan,
                detached,
                outcome._bridges,
            )

        mutated_plan = copy(active_plan)
        object.__setattr__(
            mutated_plan,
            "tasks",
            (detached, *active_plan.tasks[1:]),
        )
        with self.assertRaises((TypeError, ValueError)):
            outcome.validate(student, mutated_plan)

        mutated_profile = copy(student)
        object.__setattr__(mutated_profile, "province", "湖南")
        with self.assertRaises(TypeError):
            outcome.validate(mutated_profile, active_plan)

    def test_receipt_rejects_admission_bridge_task_metadata_outside_canonical_task(self):
        from tests.test_pathway_evidence_bridge import profile, plan

        student = profile()
        active_plan = plan(student)
        task = next(
            item
            for item in active_plan.tasks
            if item.kind == "batch_admission" and item.target_name == "普通批"
        )
        bridge = admission_bridge_for_receipt(student, active_plan, task)
        object.__setattr__(
            bridge.task,
            "query_variants",
            (f"{task.province} {task.year} {task.subject_group} 伪造普通批",),
        )

        with self.assertRaises(PlanningSessionInputError):
            build_task_evidence_outcome(
                student,
                active_plan,
                task,
                (bridge,),
            )

    def test_receipt_rejects_pathway_projection_task_context_mismatch(self):
        from scripts.adapters import CellStatus
        from scripts.adapters.pathway_bridge import bridge_pathway_policy_evidence
        from scripts.adapters.pathway_extraction import _project_from_input
        from tests.test_pathway_evidence_bridge import (
            profile,
            plan,
            policy_table,
            project,
            task_for,
        )

        student = profile()
        active_plan = plan(student)
        task = task_for(active_plan)
        input_projection = project(
            student=student,
            query_plan=active_plan,
            task=task,
            extraction=policy_table(
                year=task.year,
                statuses={"institution": CellStatus.UNCERTAIN},
            ),
        ).input_projection
        input_projection["task"]["source_policy_version"] = "9.9"
        task_payload = {
            key: value
            for key, value in input_projection["task"].items()
            if key != "task_digest"
        }
        input_projection["task"]["task_digest"] = "sha256:" + hashlib.sha256(
            json.dumps(
                task_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        bridge = bridge_pathway_policy_evidence(
            _project_from_input(input_projection)
        )

        with self.assertRaises(PlanningSessionInputError):
            build_task_evidence_outcome(
                student,
                active_plan,
                task,
                (bridge,),
            )

    def test_ingest_requires_a_known_task_and_controlled_outcome_metadata(self):
        student = planning_profile()
        active_plan = query_plan(student)
        ready = (
            PlanningSession.create(
                "0123456789abcdef0123456789abcdef", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        known = ready.expected_task_ids[0]
        for unknown in (
            "score-table:ffffffffffffffffffffffff",
            "phone:13800138000",
        ):
            with self.subTest(unknown=unknown), self.assertRaises(
                PlanningSessionInputError
            ):
                ready.ingest_task(
                    unknown,
                    query_plan_digest=plan_digest(active_plan),
                    query_plan=active_plan,
                    profile=student,
                    outcome="completed",
                    artifact_digest=digest("artifact"),
                    provenance_digest=digest("provenance"),
                )
        with self.assertRaises(PlanningSessionInputError):
            ready.ingest_task(
                known,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="completed",
            )
        with self.assertRaises(PlanningSessionInputError):
            ready.ingest_task(
                known,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="unavailable",
                unavailable_reason="host_said_no",
            )

    def test_newer_comparable_year_requires_a_digest_bound_usable_newer_sibling(self):
        student, plan, newer, outcome, ready = accepted_pathway_outcome()
        family = (newer.kind, newer.target_name)
        family_tasks = tuple(
            task
            for task in plan.tasks
            if (task.kind, task.target_name) == family
        )
        newer, older = family_tasks[:2]

        with self.assertRaises(SessionTransitionError):
            ready.ingest_task(
                older.task_id,
                query_plan_digest=plan_digest(plan),
                query_plan=plan,
                profile=student,
                outcome="unavailable",
                unavailable_reason="newer_comparable_year_accepted",
            )

        unusable = ready.ingest_task(
            newer.task_id,
            query_plan_digest=plan_digest(plan),
            query_plan=plan,
            profile=student,
            outcome="completed",
            artifact_digest=digest("artifact-conflict"),
            provenance_digest=digest("provenance-conflict"),
        )
        with self.assertRaises(SessionTransitionError):
            unusable.ingest_task(
                older.task_id,
                query_plan_digest=plan_digest(plan),
                query_plan=plan,
                profile=student,
                outcome="unavailable",
                unavailable_reason="newer_comparable_year_accepted",
            )

        accepted = ready.ingest_task(
            newer.task_id,
            query_plan_digest=plan_digest(plan),
            outcome="completed",
            query_plan=plan,
            profile=student,
            evidence_outcome=outcome,
        )
        with self.assertRaises(SessionTransitionError):
            accepted.ingest_task(
                older.task_id,
                query_plan_digest=plan_digest(plan),
                query_plan=plan,
                profile=student,
                outcome="unavailable",
                unavailable_reason="newer_comparable_year_accepted",
            )
        resolved = accepted.ingest_task(
            older.task_id,
            query_plan_digest=plan_digest(plan),
            query_plan=plan,
            profile=student,
            outcome="unavailable",
            newer_evidence_outcome=outcome,
            unavailable_reason="newer_comparable_year_accepted",
        )

        self.assertEqual(
            resolved.unavailable_reason_codes,
            ("newer_comparable_year_accepted",),
        )
        self.assertEqual(resolved.completed_usable_flags, (True,))
        self.assertEqual(PlanningSession.from_dict(resolved.to_dict()), resolved)
        schema = json.loads(
            (ROOT / "schemas" / "planning-session.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            "newer_comparable_year_accepted",
            schema["$defs"]["unavailableReason"]["enum"],
        )

    def test_only_factory_evidence_can_persist_a_usable_completed_outcome(self):
        student = planning_profile()
        active_plan = query_plan(student)
        ready = (
            PlanningSession.create(
                "0123456789abcdef0123456789abcdef", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        task_id = ready.expected_task_ids[0]
        completed = ready.ingest_task(
            task_id,
            query_plan_digest=plan_digest(active_plan),
            query_plan=active_plan,
            profile=student,
            outcome="completed",
            artifact_digest=digest("artifact"),
            provenance_digest=digest("provenance"),
        )
        self.assertEqual(completed.completed_usable_flags, (False,))
        self.assertEqual(
            PlanningSession.from_dict(completed.to_dict()).completed_usable_flags,
            (False,),
        )
        with self.assertRaises(TypeError):
            TaskEvidenceOutcome()
        student, active_plan, task, outcome, accepted_ready = (
            accepted_pathway_outcome()
        )
        accepted = accepted_ready.ingest_task(
            task.task_id,
            query_plan_digest=plan_digest(active_plan),
            outcome="completed",
            query_plan=active_plan,
            profile=student,
            evidence_outcome=outcome,
        )
        self.assertEqual(accepted.completed_usable_flags, (True,))

    def test_rehashed_snapshot_flags_and_receipt_digest_never_authorize_a_skip(self):
        student, active_plan, newer, outcome, ready = accepted_pathway_outcome()
        older = next(
            item
            for item in active_plan.tasks
            if item.kind == newer.kind
            and item.target_name == newer.target_name
            and item.year < newer.year
        )
        raw = ready.ingest_task(
            newer.task_id,
            query_plan_digest=plan_digest(active_plan),
            query_plan=active_plan,
            profile=student,
            outcome="completed",
            artifact_digest=outcome.artifact_digest,
            provenance_digest=outcome.provenance_digest,
        )
        forged = raw.to_dict()
        forged["completed_usable_flags"] = [True]
        forged["completed_receipt_digests"] = [outcome.receipt_digest]
        restored = PlanningSession.from_dict(rehash_snapshot(forged))

        with self.assertRaises(SessionTransitionError):
            restored.ingest_task(
                older.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="unavailable",
                unavailable_reason="newer_comparable_year_accepted",
            )

    def test_receipt_must_match_every_stored_completed_digest(self):
        student, active_plan, newer, outcome, ready = accepted_pathway_outcome()
        older = next(
            item
            for item in active_plan.tasks
            if item.kind == newer.kind
            and item.target_name == newer.target_name
            and item.year < newer.year
        )
        accepted = ready.ingest_task(
            newer.task_id,
            query_plan_digest=plan_digest(active_plan),
            outcome="completed",
            query_plan=active_plan,
            profile=student,
            evidence_outcome=outcome,
        )

        for field, replacement in (
            ("completed_artifact_digests", [digest("forged-artifact")]),
            ("completed_provenance_digests", [digest("forged-provenance")]),
            ("completed_receipt_digests", [digest("forged-receipt")]),
        ):
            forged = accepted.to_dict()
            forged[field] = replacement
            restored = PlanningSession.from_dict(rehash_snapshot(forged))
            with self.subTest(field=field), self.assertRaises(
                SessionTransitionError
            ):
                restored.ingest_task(
                    older.task_id,
                    query_plan_digest=plan_digest(active_plan),
                    query_plan=active_plan,
                    profile=student,
                    outcome="unavailable",
                    newer_evidence_outcome=outcome,
                    unavailable_reason="newer_comparable_year_accepted",
                )

    def test_comparable_receipt_must_be_same_family_and_strictly_newer(self):
        student, active_plan, newer, outcome, ready = accepted_pathway_outcome()
        family_tasks = tuple(
            item
            for item in active_plan.tasks
            if item.kind == newer.kind and item.target_name == newer.target_name
        )
        older = family_tasks[1]
        other = next(
            item
            for item in active_plan.tasks
            if item.kind == "comprehensive_evaluation"
            and item.year == newer.year
        )
        other_outcome = pathway_outcome_for(student, active_plan, other)
        other_completed = ready.ingest_task(
            other.task_id,
            query_plan_digest=plan_digest(active_plan),
            query_plan=active_plan,
            profile=student,
            outcome="completed",
            evidence_outcome=other_outcome,
        )
        with self.assertRaises(SessionTransitionError):
            other_completed.ingest_task(
                older.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="unavailable",
                newer_evidence_outcome=other_outcome,
                unavailable_reason="newer_comparable_year_accepted",
            )

        older_outcome = pathway_outcome_for(student, active_plan, older)
        older_completed = ready.ingest_task(
            older.task_id,
            query_plan_digest=plan_digest(active_plan),
            query_plan=active_plan,
            profile=student,
            outcome="completed",
            evidence_outcome=older_outcome,
        )
        with self.assertRaises(SessionTransitionError):
            older_completed.ingest_task(
                newer.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="unavailable",
                newer_evidence_outcome=older_outcome,
                unavailable_reason="newer_comparable_year_accepted",
            )

    def test_partial_and_conflict_receipts_cannot_authorize_historical_skips(self):
        from scripts.contracts import SourceTier
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            policy_table,
            project,
        )

        student, active_plan, newer, _outcome, ready = accepted_pathway_outcome()
        older = next(
            item
            for item in active_plan.tasks
            if item.kind == newer.kind
            and item.target_name == newer.target_name
            and item.year < newer.year
        )
        partial_projection = project(
            student=student,
            query_plan=active_plan,
            task=newer,
            extraction=policy_table(
                year=newer.year,
                professional_options=None,
            ),
        )
        conflict_sources = (
            candidate(
                "receipt-conflict-b1",
                tier=SourceTier.B,
                publisher="回执冲突乙一",
                host="receipt-b1.example.cn",
            ),
            candidate(
                "receipt-conflict-b2",
                tier=SourceTier.B,
                publisher="回执冲突乙二",
                host="receipt-b2.example.cn",
            ),
        )
        conflict_projection = project(
            student=student,
            query_plan=active_plan,
            task=newer,
            extraction=(
                policy_table(year=newer.year),
                policy_table(
                    year=newer.year,
                    professional_options="冲突专业",
                ),
            ),
            field_map=tuple(
                {name: name for name in POLICY_FIELDS}
                for _ in conflict_sources
            ),
            candidates=conflict_sources,
        )
        for label, projection in (
            ("partial", partial_projection),
            ("conflict", conflict_projection),
        ):
            receipt = pathway_outcome_for(
                student, active_plan, newer, projection=projection
            )
            self.assertFalse(receipt.usable)
            completed = ready.ingest_task(
                newer.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="completed",
                evidence_outcome=receipt,
            )
            with self.subTest(label=label), self.assertRaises(
                SessionTransitionError
            ):
                completed.ingest_task(
                    older.task_id,
                    query_plan_digest=plan_digest(active_plan),
                    query_plan=active_plan,
                    profile=student,
                    outcome="unavailable",
                    newer_evidence_outcome=receipt,
                    unavailable_reason="newer_comparable_year_accepted",
                )

    def test_corroborated_and_reference_receipts_cannot_stop_official_history_lookup(self):
        from scripts.contracts import SourceTier
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            policy_table,
            project,
        )

        student, active_plan, newer, _outcome, ready = accepted_pathway_outcome()
        older = next(
            item
            for item in active_plan.tasks
            if item.kind == newer.kind
            and item.target_name == newer.target_name
            and item.year < newer.year
        )
        cases = (
            (
                "corroborated",
                SourceTier.B,
                (
                    candidate(
                        "receipt-b1",
                        tier=SourceTier.B,
                        publisher="独立乙级来源一",
                        host="receipt-b1.example.cn",
                    ),
                    candidate(
                        "receipt-b2",
                        tier=SourceTier.B,
                        publisher="独立乙级来源二",
                        host="receipt-b2.example.cn",
                    ),
                ),
            ),
            (
                "reference",
                SourceTier.C,
                (
                    candidate(
                        "receipt-reference-one",
                        tier=SourceTier.C,
                        publisher="独立丙级来源一",
                        host="reference-one.example.cn",
                    ),
                    candidate(
                        "receipt-reference-two",
                        tier=SourceTier.C,
                        publisher="独立丙级来源二",
                        host="reference-two.example.cn",
                    ),
                    candidate(
                        "receipt-reference-three",
                        tier=SourceTier.C,
                        publisher="独立丙级来源三",
                        host="reference-three.example.cn",
                    ),
                ),
            ),
        )
        for expected_status, _tier, sources in cases:
            projection = project(
                student=student,
                query_plan=active_plan,
                task=newer,
                extraction=tuple(
                    policy_table(year=newer.year) for _ in sources
                ),
                field_map=tuple(
                    {name: name for name in POLICY_FIELDS} for _ in sources
                ),
                candidates=sources,
            )
            receipt = pathway_outcome_for(
                student, active_plan, newer, projection=projection
            )
            self.assertTrue(receipt.usable)
            self.assertEqual(receipt.evidence_statuses, (expected_status,))
            completed = ready.ingest_task(
                newer.task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="completed",
                evidence_outcome=receipt,
            )
            with self.subTest(status=expected_status), self.assertRaises(
                SessionTransitionError
            ):
                completed.ingest_task(
                    older.task_id,
                    query_plan_digest=plan_digest(active_plan),
                    query_plan=active_plan,
                    profile=student,
                    outcome="unavailable",
                    newer_evidence_outcome=receipt,
                    unavailable_reason="newer_comparable_year_accepted",
                )

    def test_completed_provenance_is_a_fixed_digest_and_never_free_text(self):
        student = planning_profile()
        active_plan = query_plan(student)
        ready = (
            PlanningSession.create(
                "0123456789abcdef0123456789abcdef", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        task_id = ready.expected_task_ids[0]
        completed = ready.ingest_task(
            task_id,
            query_plan_digest=plan_digest(active_plan),
            query_plan=active_plan,
            profile=student,
            outcome="completed",
            artifact_digest=digest("artifact"),
            provenance_digest=digest("provenance"),
        )
        self.assertEqual(
            completed.completed_provenance_digests,
            (digest("provenance"),),
        )
        self.assertEqual(
            PlanningSession.from_dict(completed.to_dict()), completed
        )
        for unsafe in (
            "phone:13800138000",
            "secret:api-key",
            "C:\\private\\evidence.json",
            "free text provenance",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(
                PlanningSessionInputError
            ):
                ready.ingest_task(
                    task_id,
                    query_plan_digest=plan_digest(active_plan),
                    query_plan=active_plan,
                    profile=student,
                    outcome="completed",
                    artifact_digest=digest("artifact"),
                    provenance_digest=unsafe,
                )
            with self.assertRaises(TypeError):
                replace(
                    completed,
                    completed_provenance_digests=(unsafe,),
                )
            forged = completed.to_dict()
            forged["completed_provenance_digests"] = [unsafe]
            with self.assertRaises(PlanningSessionInputError):
                PlanningSession.from_dict(forged)

    def test_finalize_requires_the_exact_expected_partition_without_bypass(self):
        student = planning_profile()
        active_plan = query_plan(student)
        ready = (
            PlanningSession.create(
                "0123456789abcdef0123456789abcdef", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        partial = completed_outcome(ready, ready.expected_task_ids[0])
        with self.assertRaises(SessionTransitionError):
            partial.finalize_evidence(
                digest("manifest"),
                query_plan=active_plan,
                profile=student,
            )
        resolved = fully_resolved_session()
        with tempfile.TemporaryDirectory() as temporary:
            outcome = empty_evidence_outcome(
                resolved,
                student,
                active_plan,
                Path(temporary) / "evidence",
            )
            finalized = resolved.finalize_evidence(
                outcome,
                query_plan=active_plan,
                profile=student,
            )
        self.assertEqual(finalized.stage, SessionStage.EVIDENCE_FINALIZED)

    def test_status_is_a_deterministic_controlled_projector_for_every_stage(self):
        student = planning_profile()
        active_plan = query_plan(student)
        intake = PlanningSession.create(
            "0123456789abcdef0123456789abcdef", student
        )
        confirmed = intake.confirm_profile(student.digest)
        preflight = confirmed.with_preflight(report())
        ready = preflight.with_query_plan(active_plan, profile=student)
        research = completed_outcome(
            ready,
            ready.expected_task_ids[0],
            profile=student,
            plan=active_plan,
        )
        with tempfile.TemporaryDirectory() as temporary:
            (
                _resolved_student,
                _resolved_plan,
                _resolved,
                _evidence_outcome,
                evidence,
                _calculation_outcome,
                calculation,
                _publication_outcome,
                published,
            ) = all_unavailable_chain(Path(temporary) / "evidence")
            sessions = (
                intake,
                confirmed,
                preflight,
                ready,
                research,
                evidence,
                calculation,
                published,
            )
            expected_actions = (
                "confirm_profile",
                "run_preflight",
                "build_query_plan",
                "query_task",
                "query_task",
                "compute",
                "publish_report",
                None,
            )
            for session, action in zip(sessions, expected_actions):
                with self.subTest(stage=session.stage.value):
                    first = session.status()
                    self.assertEqual(first, session.status())
                    self.assertEqual(
                        set(first),
                        {
                            "session_id",
                            "revision",
                            "stage",
                            "coverage",
                            "next_actions",
                            "degradations",
                        },
                    )
                    coverage = first["coverage"]
                    self.assertEqual(
                        coverage["remaining"],
                        coverage["expected"]
                        - coverage["completed"]
                        - coverage["unavailable"],
                    )
                    self.assertEqual(
                        coverage["gap_task_ids"],
                        sorted(
                            set(session.expected_task_ids)
                            - set(session.completed_task_ids)
                            - set(session.unavailable_task_ids)
                        ),
                    )
                    if action is None:
                        self.assertEqual(first["next_actions"], [])
                    else:
                        self.assertEqual(first["next_actions"][0]["type"], action)
                    visible = json.dumps(first, ensure_ascii=False)
                    for forbidden in (str(ROOT), "C:\\", "/home/", "张三"):
                        self.assertNotIn(forbidden, visible)

    def test_preflight_codes_survive_and_full_coverage_requests_finalization(self):
        student = planning_profile()
        active_plan = query_plan(student)
        confirmed = PlanningSession.create(
            "0123456789abcdef0123456789abcdef", student
        ).confirm_profile(student.digest)
        preflight = confirmed.with_preflight(report())
        self.assertEqual(
            preflight.preflight_degradation_codes, ("missing_vision",)
        )
        ready = preflight.with_query_plan(active_plan, profile=student)
        researching = completed_outcome(
            ready,
            ready.expected_task_ids[0],
            profile=student,
            plan=active_plan,
        )
        with tempfile.TemporaryDirectory() as temporary:
            (
                _student,
                _active_plan,
                resolved,
                _evidence_outcome,
                finalized,
                _calculation_outcome,
                calculated,
                _publication_outcome,
                published,
            ) = all_unavailable_chain(Path(temporary) / "evidence")
            for session in (
                preflight,
                ready,
                researching,
                resolved,
                finalized,
                calculated,
                published,
            ):
                with self.subTest(stage=session.stage.value):
                    self.assertIn("missing_vision", session.status()["degradations"])
                    self.assertNotIn(
                        "missing capability: vision",
                        json.dumps(session.to_dict(), ensure_ascii=False),
                    )

        self.assertEqual(resolved.coverage["remaining"], 0)
        self.assertEqual(
            resolved.status()["next_actions"],
            [{"type": "finalize_evidence"}],
        )
        recovered = PlanningSession.from_dict(resolved.to_dict())
        self.assertIn("missing_vision", recovered.status()["degradations"])

        forged = preflight.to_dict()
        forged["preflight_degradation_codes"] = ["C:\\private\\vision.txt"]
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(forged)

    def test_stale_replay_wrong_dependency_and_duplicate_task_fail_closed(self):
        student = planning_profile()
        active_plan = query_plan(student)
        ready = (
            PlanningSession.create(
                "0123456789abcdef0123456789abcdef", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        task_id = ready.expected_task_ids[0]
        first = completed_outcome(
            ready,
            task_id,
            profile=student,
            plan=active_plan,
        )
        with self.assertRaises(SessionTransitionError):
            ready.ingest_task(
                task_id,
                query_plan_digest=digest("stale-plan"),
                query_plan=active_plan,
                profile=student,
                outcome="completed",
                artifact_digest=digest("artifact"),
                provenance_digest=digest("provenance"),
            )
        with self.assertRaises(SessionTransitionError):
            first.ingest_task(
                task_id,
                query_plan_digest=plan_digest(active_plan),
                query_plan=active_plan,
                profile=student,
                outcome="completed",
                artifact_digest=digest("artifact"),
                provenance_digest=digest("provenance"),
            )
        with self.assertRaises(SessionTransitionError):
            first.finalize_evidence(
                digest("manifest"),
                query_plan=active_plan,
                profile=student,
            )

    def test_profile_revision_invalidates_every_downstream_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed = all_unavailable_chain(
                Path(temporary) / "evidence"
            )[-1]
        revised = completed.revise_profile(digest("new-profile"))
        self.assertEqual(revised.stage, SessionStage.PROFILE_CONFIRMED)
        self.assertEqual(revised.profile_digest, digest("new-profile"))
        for name in (
            "preflight_digest",
            "query_plan_digest",
            "evidence_manifest_hash",
            "evidence_receipt_digest",
            "calculation_digest",
            "calculation_receipt_digest",
            "report_digest",
            "publication_receipt_digest",
        ):
            self.assertIsNone(getattr(revised, name))
        self.assertEqual(revised.completed_task_ids, ())
        self.assertEqual(revised.unavailable_task_ids, ())
        self.assertEqual(revised.expected_task_ids, ())

    def test_strict_snapshot_rejects_unknown_keys_bad_ids_and_forgery(self):
        payload = ready_session().to_dict()
        payload["questionnaire_text"] = "private"
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(payload)
        for bad in ("human-name", "A" * 32, "0123", "../session"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PlanningSession.create(bad, digest("profile"))
        forged = ready_session().to_dict()
        forged["stage"] = "report_published"
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(forged)

    def test_serialization_is_deterministic_and_contains_no_source_or_paths(self):
        session = ready_session()
        first = session.to_json_bytes()
        second = session.to_json_bytes()
        self.assertEqual(first, second)
        self.assertEqual(
            first,
            json.dumps(
                session.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        text = first.decode("utf-8")
        for forbidden in ("questionnaire", "C:\\", "/home/", str(ROOT), "张三"):
            self.assertNotIn(forbidden, text)


class PlanningSessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = PlanningSessionStore(self.root)

    def test_private_fsync_precedes_exclusive_same_directory_publish(self):
        session = ready_session()
        observed = []
        real_fsync = os.fsync
        real_link = os.link

        def fsync(descriptor):
            observed.append(("fsync", tuple(self.root.iterdir())))
            return real_fsync(descriptor)

        def link(source, destination):
            observed.append(("link", Path(source).parent, Path(destination).parent))
            return real_link(source, destination)

        with mock.patch("planning_session.os.fsync", side_effect=fsync), mock.patch(
            "planning_session.os.link", side_effect=link
        ):
            path = self.store.save(session)
        self.assertEqual(observed[0][0], "fsync")
        self.assertEqual(observed[-1][0], "link")
        self.assertEqual(observed[-1][1:], (self.root, self.root))
        self.assertEqual(self.store.load(session.session_id), session)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_competing_destination_is_preserved_and_partial_files_are_cleaned(self):
        session = ready_session()

        def competitor(_source, destination):
            Path(destination).write_bytes(b"RIVAL")
            raise FileExistsError("rival destination")

        with mock.patch("planning_session.os.link", side_effect=competitor):
            with self.assertRaises(PlanningSessionStoreError) as caught:
                self.store.save(session)
        self.assertNotIn(str(self.root), str(caught.exception))
        files = list(self.root.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b"RIVAL")

    def test_primary_publish_error_survives_cleanup_failure(self):
        with mock.patch("planning_session.os.link", side_effect=OSError("PRIMARY")), mock.patch(
            "planning_session.Path.unlink", autospec=True, side_effect=OSError("CLEANUP")
        ):
            with self.assertRaises(PlanningSessionStoreError) as caught:
                self.store.save(ready_session())
        self.assertEqual(caught.exception.reason_code, "publish_failed")

    def test_primary_baseexception_survives_cleanup_baseexception(self):
        with mock.patch(
            "planning_session.os.link", side_effect=SystemExit("PRIMARY")
        ), mock.patch(
            "planning_session.Path.unlink",
            autospec=True,
            side_effect=KeyboardInterrupt("CLEANUP"),
        ):
            with self.assertRaisesRegex(SystemExit, "PRIMARY"):
                self.store.save(ready_session())

    def test_cleanup_baseexception_without_primary_becomes_controlled_error(self):
        with mock.patch(
            "planning_session.Path.unlink",
            autospec=True,
            side_effect=KeyboardInterrupt("CLEANUP"),
        ):
            with self.assertRaises(PlanningSessionStoreError) as caught:
                self.store.save(ready_session())
        self.assertEqual(caught.exception.reason_code, "cleanup_failed")

    def test_partial_write_failure_leaves_no_owned_snapshot(self):
        with mock.patch("planning_session.os.fsync", side_effect=OSError("partial")):
            with self.assertRaises(PlanningSessionStoreError) as caught:
                self.store.save(ready_session())
        self.assertEqual(caught.exception.reason_code, "publish_failed")
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX group/world mode bits")
    def test_published_snapshot_remains_owner_private(self):
        path = self.store.save(ready_session())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_load_rejects_toctou_replacement_and_path_neutralizes_error(self):
        session = ready_session()
        path = self.store.save(session)
        real_lstat = os.lstat
        calls = 0

        def changed(candidate):
            nonlocal calls
            calls += 1
            value = real_lstat(candidate)
            if candidate == path and calls > 1:
                return os.stat_result((*value[:7], value.st_mtime + 1, *value[9:]))
            return value

        with mock.patch("planning_session.os.lstat", side_effect=changed):
            with self.assertRaises(PlanningSessionStoreError) as caught:
                self.store.load(session.session_id)
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_save_rejects_same_name_temp_replacement_and_preserves_rival(self):
        real_link = os.link
        replacement_succeeded = False

        def replace_temp(source, destination):
            nonlocal replacement_succeeded
            replacement = Path(source).with_suffix(".rival")
            os.replace(source, replacement)
            replacement_succeeded = True
            Path(source).write_bytes(b"RIVAL")
            real_link(source, destination)

        with mock.patch("planning_session.os.link", side_effect=replace_temp):
            with self.assertRaises(PlanningSessionStoreError):
                self.store.save(ready_session())
        if replacement_succeeded:
            self.assertTrue(
                any(path.read_bytes() == b"RIVAL" for path in self.root.iterdir())
            )
        else:
            self.assertEqual(list(self.root.iterdir()), [])

    def test_read_between_path_checks_uses_one_fd_and_rejects_replacement(self):
        session = ready_session()
        path = self.store.save(session)
        original_read = os.read
        replaced = False

        def replace_during_read(descriptor, size):
            nonlocal replaced
            if not replaced:
                replaced = True
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(session.to_json_bytes())
                os.replace(replacement, path)
            return original_read(descriptor, size)

        with mock.patch("planning_session.os.read", side_effect=replace_during_read):
            with self.assertRaises(PlanningSessionStoreError):
                self.store.load(session.session_id)

    def test_equal_length_same_inode_overwrite_with_restored_mtime_fails_closed(self):
        session = ready_session()
        path = self.store.save(session)
        original = path.read_bytes()
        original_stat = os.stat(path)
        rival = original.replace(b'"stage":"query_plan_ready"', b'"stage":"report_published"')
        self.assertEqual(len(rival), len(original))
        self.assertNotEqual(rival, original)
        original_read = os.read
        overwritten = False

        def overwrite_after_first_read(descriptor, size):
            nonlocal overwritten
            chunk = original_read(descriptor, size)
            if chunk and not overwritten:
                overwritten = True
                with path.open("r+b") as stream:
                    stream.write(rival)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.utime(
                    path,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
            return chunk

        with mock.patch(
            "planning_session._lock_for_stable_read",
            return_value=lambda: None,
        ), mock.patch(
            "planning_session.os.read", side_effect=overwrite_after_first_read
        ):
            with self.assertRaises(PlanningSessionStoreError):
                self.store.load(session.session_id)
        self.assertTrue(overwritten)


class PlanningSessionCompatibilityTest(unittest.TestCase):
    @staticmethod
    def _rehash(payload):
        body = {
            key: value for key, value in payload.items() if key != "session_digest"
        }
        payload["session_digest"] = "sha256:" + hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return payload

    def test_schema_has_the_exact_canonical_snapshot_surface(self):
        schema = json.loads(
            (ROOT / "schemas" / "planning-session.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(ready_session().to_dict()))
        self.assertEqual(set(schema["properties"]), set(ready_session().to_dict()))

    def test_runtime_rejects_unreachable_rehashed_states(self):
        impossible = PlanningSession.create(
            "0123456789abcdef0123456789abcdef", digest("profile")
        ).to_dict()
        impossible["revision"] = 1
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(self._rehash(impossible))

        incomplete = fully_resolved_session().to_dict()
        incomplete["stage"] = "evidence_finalized"
        incomplete["evidence_manifest_hash"] = digest("manifest")
        incomplete["unavailable_task_ids"] = incomplete["unavailable_task_ids"][:-1]
        incomplete["unavailable_reason_codes"] = incomplete[
            "unavailable_reason_codes"
        ][:-1]
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(self._rehash(incomplete))

        too_few_transitions = fully_resolved_session().to_dict()
        too_few_transitions["stage"] = "evidence_finalized"
        too_few_transitions["revision"] = 5
        too_few_transitions["evidence_manifest_hash"] = digest("manifest")
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(self._rehash(too_few_transitions))

        noncanonical = ready_session().to_dict()
        noncanonical["expected_task_ids"] = list(
            reversed(noncanonical["expected_task_ids"])
        )
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(self._rehash(noncanonical))

    def test_finalized_completed_tasks_require_non_null_factory_receipts(self):
        student = planning_profile()
        active_plan = query_plan(student)
        ready = (
            PlanningSession.create(
                "abcdef0123456789abcdef0123456789", student
            )
            .confirm_profile(student.digest)
            .with_preflight(report())
            .with_query_plan(active_plan, profile=student)
        )
        raw_research = completed_outcome(
            ready,
            ready.expected_task_ids[0],
            profile=student,
            plan=active_plan,
        )
        self.assertEqual(raw_research.completed_receipt_digests, (None,))
        self.assertEqual(
            PlanningSession.from_dict(raw_research.to_dict()), raw_research
        )

        with tempfile.TemporaryDirectory() as temporary:
            forged = finalized_payload_with_null_completed_receipt(
                Path(temporary) / "evidence"
            )
        with self.assertRaises(PlanningSessionInputError):
            PlanningSession.from_dict(forged)

    def test_real_draft_validator_rejects_schema_unreachable_states_when_available(self):
        try:
            import jsonschema
        except ModuleNotFoundError:
            self.skipTest("jsonschema is unavailable")
        schema = json.loads(
            (ROOT / "schemas" / "planning-session.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        valid = ready_session().to_dict()
        validator.validate(valid)
        self.assertEqual(schema["properties"]["revision"]["maximum"], 99_999_999)
        for name in (
            "preflight_degradation_codes",
            "expected_task_ids",
            "completed_task_ids",
            "unavailable_task_ids",
        ):
            self.assertEqual(
                schema["properties"][name]["x-canonical-order"], "ascending"
            )

        impossible = PlanningSession.create(
            "0123456789abcdef0123456789abcdef", digest("profile")
        ).to_dict()
        impossible["revision"] = 1
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(self._rehash(impossible))

    def test_schema_requires_receipts_exactly_at_their_authenticated_stages(self):
        try:
            import jsonschema
        except ModuleNotFoundError:
            self.skipTest("jsonschema is unavailable")
        schema = json.loads(
            (ROOT / "schemas" / "planning-session.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validator = jsonschema.Draft202012Validator(schema)
        with tempfile.TemporaryDirectory() as temporary:
            (
                _student,
                _plan,
                resolved,
                _evidence_outcome,
                finalized,
                _calculation_outcome,
                calculated,
                _publication_outcome,
                published,
            ) = all_unavailable_chain(Path(temporary) / "evidence")
            for session in (resolved, finalized, calculated, published):
                validator.validate(session.to_dict())

            invalid_cases = (
                (resolved, "evidence_receipt_digest", digest("too-early")),
                (finalized, "evidence_receipt_digest", None),
                (finalized, "calculation_receipt_digest", digest("too-early")),
                (calculated, "calculation_receipt_digest", None),
                (calculated, "publication_receipt_digest", digest("too-early")),
                (published, "publication_receipt_digest", None),
            )
            for session, field, value in invalid_cases:
                payload = session.to_dict()
                payload[field] = value
                with self.subTest(stage=session.stage.value, field=field), self.assertRaises(
                    jsonschema.ValidationError
                ):
                    validator.validate(payload)

            with self.assertRaises(jsonschema.ValidationError):
                validator.validate(
                    finalized_payload_with_null_completed_receipt(
                        Path(temporary) / "forged-evidence"
                    )
                )

    def test_module_imports_under_real_python_310(self):
        candidate = (
            ROOT
            / ".superpowers"
            / "uv-python"
            / "cpython-3.10.20-windows-x86_64-none"
            / "python.exe"
        )
        if not candidate.is_file():
            self.skipTest("bundled Python 3.10 is unavailable")
        result = subprocess.run(
            [str(candidate), "-c", "import scripts.planning_session"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
