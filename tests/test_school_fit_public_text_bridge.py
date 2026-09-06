from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.adapters.public_text import PublicTextField, bind_public_text
from scripts.contracts import CapabilityReport, CapabilityTier, EvidenceStatus
from scripts.evidence import EvidenceStore
from scripts.planning_session import (
    PlanningSession,
    PlanningSessionReplayJournal,
    build_task_evidence_outcome,
)
from tests.test_rank_evidence_bridge import plan, profile
from tests.test_school_fit_evidence_bridge import (
    charter_values,
    current_task,
    enrollment_values,
    province_policy_values,
    source,
    subject_values,
    tuition_values,
)


def _document(candidate, values, *, missing=(), suffix=""):
    fields = {}
    parts = []
    for name, value in values.items():
        if name in missing:
            fields[name] = PublicTextField.missing()
            continue
        rendered = "、".join(value) if isinstance(value, (list, tuple)) else str(value)
        quote = f"{name}：{rendered}"
        start = sum(len(part) for part in parts) + len(parts)
        parts.append(quote)
        fields[name] = PublicTextField(
            value=value,
            quote=quote,
            start=start,
            end=start + len(quote),
        )
    return bind_public_text(
        source_id=candidate.source_id,
        url=candidate.url,
        text="。".join(parts) + suffix,
        fields=fields,
    )


def _capability():
    return CapabilityReport(
        tier=CapabilityTier.FULL,
        host_capabilities=("browse", "search", "vision"),
        available_capabilities=("browse", "search", "vision"),
        missing_capabilities=(),
        degradations=(),
        python_version="3.10.20",
    )


class SchoolFitPublicTextBridgeTest(unittest.TestCase):
    def test_all_school_fit_kinds_consume_typed_public_prose(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_public_text,
            validate_school_fit_evidence_bridge,
        )

        student = profile()
        active_plan = plan(student)
        cases = {
            "province_policy": province_policy_values,
            "enrollment_plan": enrollment_values,
            "subject_requirement": subject_values,
            "admission_charter": charter_values,
            "tuition_fee": tuition_values,
        }
        for kind, values_factory in cases.items():
            with self.subTest(kind=kind):
                task = current_task(active_plan, kind)
                candidate = source(f"public-{kind}")
                document = _document(candidate, values_factory(student, task))

                bridge = bridge_school_fit_public_text(
                    student, active_plan, task, (document,), (candidate,)
                )

                self.assertIs(bridge.evidence_status, EvidenceStatus.OFFICIAL)
                self.assertEqual(bridge.extraction_method, "host-public-text")
                self.assertEqual(bridge.metadata["kind"], kind)
                self.assertEqual(bridge.metadata["year"], task.year)
                origin = json.loads(bridge._origin_json)
                self.assertEqual(origin["adapter_kind"], "public-text")
                self.assertEqual(origin["documents"][0]["text"], document.text)
                year_binding = origin["documents"][0]["fields"]["year"]
                self.assertEqual(
                    document.text[year_binding["start"] : year_binding["end"]],
                    year_binding["quote"],
                )
                self.assertEqual(origin["sources"][0], candidate.to_dict())
                self.assertNotIn("tables", origin)
                self.assertEqual(
                    validate_school_fit_evidence_bridge(
                        bridge, student, active_plan
                    ).to_dict(),
                    bridge.to_dict(),
                )

    def test_optional_absent_public_fields_stay_missing(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_public_text

        student = profile()
        active_plan = plan(student)
        cases = (
            (
                "admission_charter",
                charter_values,
                ("health_restrictions", "special_conditions"),
            ),
            (
                "tuition_fee",
                tuition_values,
                ("accommodation_fee", "financial_aid"),
            ),
        )
        for kind, values_factory, missing in cases:
            with self.subTest(kind=kind):
                task = current_task(active_plan, kind)
                candidate = source(f"public-partial-{kind}")
                document = _document(
                    candidate,
                    values_factory(student, task),
                    missing=missing,
                )

                bridge = bridge_school_fit_public_text(
                    student, active_plan, task, (document,), (candidate,)
                )

                self.assertEqual(bridge.metadata["unverified_fields"], sorted(missing))
                for field in missing:
                    self.assertIsNone(bridge.metadata[field])

    def test_source_bound_projection_changes_when_raw_prose_changes(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_public_text

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "enrollment_plan")
        candidate = source("public-changing-prose")
        values = enrollment_values(student, task)

        original = bridge_school_fit_public_text(
            student,
            active_plan,
            task,
            (_document(candidate, values),),
            (candidate,),
        )
        revised = bridge_school_fit_public_text(
            student,
            active_plan,
            task,
            (_document(candidate, values, suffix="。网页修订"),),
            (candidate,),
        )

        self.assertNotEqual(revised.bridge_digest, original.bridge_digest)
        self.assertNotEqual(revised._origin_json, original._origin_json)

    def test_replay_journal_rebuilds_public_school_fit_from_raw_text(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_public_text

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "admission_charter")
        candidate = source("public-charter-journal")
        document = _document(candidate, charter_values(student, task))
        bridge = bridge_school_fit_public_text(
            student, active_plan, task, (document,), (candidate,)
        )
        receipt = build_task_evidence_outcome(student, active_plan, task, (bridge,))
        capability = _capability()
        session = (
            PlanningSession.create("bf0123456789abcdef0123456789abcd", student)
            .confirm_profile(student.digest)
            .with_preflight(capability)
            .with_query_plan(active_plan, profile=student)
            .ingest_task(
                task.task_id,
                query_plan_digest=receipt.query_plan_digest,
                query_plan=active_plan,
                profile=student,
                outcome="completed",
                evidence_outcome=receipt,
            )
        )
        for pending in active_plan.tasks:
            if pending.task_id != task.task_id:
                session = session.ingest_task(
                    pending.task_id,
                    query_plan_digest=receipt.query_plan_digest,
                    query_plan=active_plan,
                    profile=student,
                    outcome="unavailable",
                    unavailable_reason="source_threshold_not_met",
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence_root = root / "bundle-root"
            evidence_root.mkdir()
            store = EvidenceStore.create(evidence_root, capability)
            store.add_candidate(candidate)
            bridge.persist(store)
            store.finalize()
            journal_root = root / "journal"
            journal_root.mkdir()
            journal = PlanningSessionReplayJournal(journal_root)
            journal.save(
                session,
                profile=student,
                query_plan=active_plan,
                capability_report=capability,
                bundle_path=store.session_path,
                task_outcomes=(receipt,),
            )

            restored = journal.load(session.session_id, session.revision)

        rebuilt = restored.task_outcomes[0]._bridges[0]
        self.assertEqual(rebuilt.metadata, bridge.metadata)
        self.assertEqual(rebuilt.bridge_digest, bridge.bridge_digest)
        self.assertEqual(
            json.loads(rebuilt._origin_json)["documents"][0]["text"],
            document.text,
        )


if __name__ == "__main__":
    unittest.main()
