from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceFact,
    EvidenceStatus,
    RecommendationProfile,
    SourceCandidate,
    SourceTier,
)
from scripts.evidence import EvidenceStore
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.planning_session import (
    PlanningSession,
    PlanningSessionReplayJournal,
    build_task_evidence_outcome,
)
from scripts.validate_data import ValidatedAdmissionRow
from scripts.validate_evidence import validate_bundle_snapshot
from tests.test_rank_evidence_bridge import plan, profile
from tests.test_planning_profile import reference_payload


def source(
    source_id: str,
    *,
    tier: SourceTier = SourceTier.A,
    publisher: str = "湖北省教育考试院",
    host: str = "fit.hubei.gov.cn",
) -> SourceCandidate:
    return SourceCandidate(
        source_id=source_id,
        url=f"https://{host}/{source_id}.html",
        publisher=publisher,
        tier=tier,
        published_at="2026-06-25",
        retrieved_at="2026-08-30T00:00:00Z",
        content_hash="sha256:" + hashlib.sha256(source_id.encode()).hexdigest(),
        citation_root=f"https://{host}/",
        summary="合成学校专业适配资料",
    )


def row_table(values: dict[str, object], *, table_id: str = "table[1]"):
    row = ExtractedRow(
        values=values,
        cell_status={name: CellStatus.EXACT for name in values},
        location=f"{table_id}/tbody/tr[1]",
        confidence=1,
    )
    return row, ExtractedTable(
        table_id=table_id,
        caption="学校专业适配资料",
        sheet=None,
        rows=(row,),
        coverage=ExtractedCoverage(),
        warnings=(),
        extraction_method="html-table",
    )


def enrollment_values(student, task, **changes):
    values = {
        "province": student.province,
        "year": task.year,
        "subject_group": task.subject_group,
        "institution": "合成示例大学",
        "institution_code": "SYN-A01",
        "program_group": "第01组",
        "majors": ("历史学", "法学"),
        "school_province": "湖北",
        "school_city": "武汉",
        "institution_type": "public",
    }
    values.update(changes)
    return values


def subject_values(student, task, **changes):
    values = {
        "province": student.province,
        "year": task.year,
        "subject_group": task.subject_group,
        "institution": "合成示例大学",
        "institution_code": "SYN-A01",
        "program_group": "第01组",
        "required_secondary_subjects": ("政治",),
        "secondary_subject_rule": "all",
        "special_conditions": "无额外限制",
    }
    values.update(changes)
    return values


def charter_values(student, task, **changes):
    values = {
        "province": student.province,
        "year": task.year,
        "institution": "合成示例大学",
        "institution_code": "SYN-A01",
        "admission_rules": "按投档成绩择优录取",
        "adjustment_rules": "服从调剂时在同一院校专业组内调剂",
        "adjustment_required": False,
        "health_restrictions": "按普通高等学校招生体检工作指导意见执行",
        "language_restrictions": "不限外语语种",
        "single_subject_restrictions": "无单科成绩限制",
        "special_conditions": "无其他特殊条件",
    }
    values.update(changes)
    return values


def tuition_values(student, task, **changes):
    values = {
        "province": student.province,
        "year": task.year,
        "institution": "合成示例大学",
        "institution_code": "SYN-A01",
        "program_group": "第01组",
        "majors": ("历史学", "法学"),
        "annual_fee_amount": 8000,
        "fee_currency": "CNY",
        "fee_period": "academic_year",
        "accommodation_fee": 1200,
        "other_required_fees": "教材费据实结算",
        "financial_aid": "国家奖助学金和校内助学金",
    }
    values.update(changes)
    return values


def province_policy_values(student, task, **changes):
    values = {
        "province": student.province,
        "year": task.year,
        "exam_mode": student.subject_mode,
        "subject_structure": student.subject_mode,
        "batch_structure": "本科普通批按院校专业组投档",
        "effective_date": f"{task.year}-01-01",
    }
    values.update(changes)
    return values


def current_task(active_plan, kind: str):
    return next(
        item
        for item in active_plan.tasks
        if item.kind == kind and item.year == active_plan.research_year
    )


class SchoolFitEvidenceBridgeTest(unittest.TestCase):
    def test_enrollment_bridge_requires_no_charter_or_tuition_fields(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "enrollment_plan")
        values = enrollment_values(student, task)
        for name in (
            "adjustment_required",
            "annual_fee_amount",
            "fee_currency",
            "fee_period",
        ):
            values.pop(name, None)
        extracted_row, table = row_table(values)

        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-enrollment-without-charter-fee"),),
        )

        self.assertEqual(bridge.metadata["kind"], "enrollment_plan")
        self.assertNotIn("adjustment_required", bridge.metadata)
        self.assertNotIn("annual_fee_amount", bridge.metadata)

    def test_adapter_imports_from_flat_scripts_cli_path(self):
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"sys.path.insert(0, {str(root / 'scripts')!r}); "
                    "import adapters.school_fit_bridge"
                ),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_factory_derives_affordability_and_binds_profile_plan_task(self):
        from scripts.adapters.school_fit_bridge import (
            AFFORDABILITY_POLICY_ID,
            SchoolFitEvidenceBridge,
            bridge_school_fit_evidence,
        )

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "tuition_fee")
        extracted_row, table = row_table(tuition_values(student, task))

        with self.assertRaisesRegex(TypeError, "factory-only"):
            SchoolFitEvidenceBridge()
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-school-fit"),),
        )

        self.assertIs(bridge.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(bridge.profile_digest, student.digest)
        self.assertEqual(bridge.task.task_id, task.task_id)
        self.assertEqual(bridge.metadata["affordable_for"], [
            "limited", "moderate", "flexible"
        ])
        self.assertEqual(
            bridge.metadata["affordability_policy"]["policy_id"],
            AFFORDABILITY_POLICY_ID,
        )
        self.assertEqual(bridge.metadata["annual_fee_amount"], 8000)
        self.assertEqual(
            bridge.fact.notes,
            f"query_task:{task.task_id}",
        )

        outcome = build_task_evidence_outcome(
            student, active_plan, task, (bridge,)
        )
        self.assertTrue(outcome.usable)
        self.assertEqual(outcome.evidence_statuses, ("official",))
        outcome.validate(student, active_plan)

    def test_host_cannot_supply_affordability_labels(self):
        from scripts.adapters.school_fit_bridge import (
            SchoolFitBridgeError,
            bridge_school_fit_evidence,
        )

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "tuition_fee")
        extracted_row, table = row_table(
            tuition_values(student, task, affordable_for=("flexible",))
        )

        with self.assertRaisesRegex(SchoolFitBridgeError, "affordability"):
            bridge_school_fit_evidence(
                profile=student,
                plan=active_plan,
                task=task,
                tables=(table,),
                adapter_rows=(extracted_row,),
                candidates=(source("official-school-fit"),),
            )

    def test_charter_and_tuition_are_distinct_typed_bridges(self):
        from scripts.adapters.school_fit_bridge import (
            SchoolFitBridgeError,
            bridge_school_fit_evidence,
        )

        student = profile()
        active_plan = plan(student)
        charter_task = current_task(active_plan, "admission_charter")
        tuition_task = current_task(active_plan, "tuition_fee")
        charter_row, charter_table = row_table(
            charter_values(student, charter_task)
        )
        tuition_row, tuition_table = row_table(
            tuition_values(student, tuition_task)
        )

        charter = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=charter_task,
            tables=(charter_table,),
            adapter_rows=(charter_row,),
            candidates=(source("official-charter"),),
        )
        tuition = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=tuition_task,
            tables=(tuition_table,),
            adapter_rows=(tuition_row,),
            candidates=(source("official-tuition"),),
        )

        self.assertEqual(charter.metadata["kind"], "admission_charter")
        self.assertFalse(charter.metadata["adjustment_required"])
        self.assertEqual(tuition.metadata["kind"], "tuition_fee")
        self.assertEqual(
            tuition.metadata["affordable_for"],
            ["limited", "moderate", "flexible"],
        )
        self.assertNotIn("affordable_for", charter.metadata)
        self.assertNotIn("adjustment_required", tuition.metadata)

        with self.assertRaisesRegex(
            SchoolFitBridgeError,
            "fields do not match|task",
        ):
            bridge_school_fit_evidence(
                profile=student,
                plan=active_plan,
                task=tuition_task,
                tables=(charter_table,),
                adapter_rows=(charter_row,),
                candidates=(source("wrong-kind-row"),),
            )

    def test_optional_charter_and_tuition_omissions_stay_explicitly_unverified(self):
        from scripts.adapters.school_fit_bridge import (
            _replay_persisted_school_fit_evidence_fact,
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)
        charter_task = current_task(active_plan, "admission_charter")
        tuition_task = current_task(active_plan, "tuition_fee")

        charter_row, charter_table = row_table(
            charter_values(
                student,
                charter_task,
                language_restrictions=None,
                single_subject_restrictions=None,
            )
        )
        charter_row = replace(
            charter_row,
            cell_status={
                **dict(charter_row.cell_status),
                "language_restrictions": CellStatus.EMPTY,
                "single_subject_restrictions": CellStatus.EMPTY,
            },
        )
        charter_table = replace(charter_table, rows=(charter_row,))
        tuition_row, tuition_table = row_table(
            tuition_values(
                student,
                tuition_task,
                accommodation_fee=None,
                financial_aid=None,
            )
        )
        tuition_row = replace(
            tuition_row,
            cell_status={
                **dict(tuition_row.cell_status),
                "accommodation_fee": CellStatus.EMPTY,
                "financial_aid": CellStatus.EMPTY,
            },
        )
        tuition_table = replace(tuition_table, rows=(tuition_row,))

        charter = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=charter_task,
            tables=(charter_table,),
            adapter_rows=(charter_row,),
            candidates=(source("partial-charter"),),
        )
        tuition = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=tuition_task,
            tables=(tuition_table,),
            adapter_rows=(tuition_row,),
            candidates=(source("partial-tuition"),),
        )

        self.assertIsNone(charter.metadata["language_restrictions"])
        self.assertIsNone(charter.metadata["single_subject_restrictions"])
        self.assertEqual(
            charter.metadata["unverified_fields"],
            ["language_restrictions", "single_subject_restrictions"],
        )
        self.assertIsNone(tuition.metadata["accommodation_fee"])
        self.assertIsNone(tuition.metadata["financial_aid"])
        self.assertEqual(
            tuition.metadata["unverified_fields"],
            ["accommodation_fee", "financial_aid"],
        )

        def replay(bridge):
            return _replay_persisted_school_fit_evidence_fact(
                bridge.fact.to_dict(),
                {
                    "kind": "school-fit-bridge-origin-v1",
                    "fact_id": bridge.fact.fact_id,
                    "bridge_digest": bridge.bridge_digest,
                    "origin": json.loads(bridge._origin_json),
                },
                student,
                active_plan,
            )

        rebuilt_charter = replay(charter)
        rebuilt_tuition = replay(tuition)
        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": 2025,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )
        merged = merge_school_fit_metadata(
            (admission,),
            (
                rebuilt_charter.fact.to_dict(),
                rebuilt_tuition.fact.to_dict(),
            ),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertEqual(
            merged["charter_unverified_fields"],
            ("language_restrictions", "single_subject_restrictions"),
        )
        self.assertNotIn("charter_language_restrictions", merged)
        self.assertNotIn("charter_single_subject_restrictions", merged)
        self.assertEqual(
            merged["tuition_unverified_fields"],
            ("accommodation_fee", "financial_aid"),
        )
        self.assertNotIn("tuition_accommodation_fee", merged)
        self.assertNotIn("tuition_financial_aid", merged)

    def test_explicit_exact_empty_subject_requirement_means_unrestricted(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "subject_requirement")
        extracted_row, table = row_table(
            subject_values(
                student,
                task,
                required_secondary_subjects=(),
                secondary_subject_rule="all",
            )
        )

        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-unrestricted-subject"),),
        )

        self.assertIs(bridge.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(bridge.metadata["required_secondary_subjects"], [])

    def test_conflicting_sources_do_not_expose_metadata_or_authorize_use(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "enrollment_plan")
        first_row, first_table = row_table(
            enrollment_values(student, task, school_city="武汉"),
            table_id="table[1]",
        )
        second_row, second_table = row_table(
            enrollment_values(student, task, school_city="宜昌"),
            table_id="table[2]",
        )
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(first_table, second_table),
            adapter_rows=(first_row, second_row),
            candidates=(
                source(
                    "school-fit-b1",
                    tier=SourceTier.B,
                    publisher="独立发布方甲",
                    host="fit-a.example.cn",
                ),
                source(
                    "school-fit-b2",
                    tier=SourceTier.B,
                    publisher="独立发布方乙",
                    host="fit-b.example.cn",
                ),
            ),
        )

        self.assertIs(bridge.evidence_status, EvidenceStatus.CONFLICT)
        self.assertIsNone(bridge.metadata)
        self.assertIsNone(bridge.fact.value)
        outcome = build_task_evidence_outcome(
            student, active_plan, task, (bridge,)
        )
        self.assertFalse(outcome.usable)

    def test_wrong_task_or_non_exact_cell_is_rejected_before_receipt(self):
        from scripts.adapters.school_fit_bridge import (
            SchoolFitBridgeError,
            bridge_school_fit_evidence,
        )

        student = profile()
        active_plan = plan(student)
        enrollment_task = current_task(active_plan, "enrollment_plan")
        charter_task = current_task(active_plan, "admission_charter")
        tuition_task = current_task(active_plan, "tuition_fee")
        subject_task = current_task(active_plan, "subject_requirement")
        extracted_row, table = row_table(enrollment_values(student, enrollment_task))

        with self.assertRaises(SchoolFitBridgeError):
            bridge_school_fit_evidence(
                profile=student,
                plan=active_plan,
                task=subject_task,
                tables=(table,),
                adapter_rows=(extracted_row,),
                candidates=(source("official-school-fit"),),
            )

        uncertain = replace(
            extracted_row,
            cell_status={
                **dict(extracted_row.cell_status),
                "school_city": CellStatus.UNCERTAIN,
            },
        )
        uncertain_table = replace(table, rows=(uncertain,))
        with self.assertRaisesRegex(SchoolFitBridgeError, "exact"):
            bridge_school_fit_evidence(
                profile=student,
                plan=active_plan,
                task=enrollment_task,
                tables=(uncertain_table,),
                adapter_rows=(uncertain,),
                candidates=(source("official-school-fit"),),
            )

    def test_bundle_and_replay_journal_rebuild_the_typed_bridge(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "enrollment_plan")
        extracted_row, table = row_table(enrollment_values(student, task))
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-school-fit"),),
        )
        receipt = build_task_evidence_outcome(
            student, active_plan, task, (bridge,)
        )
        capability = CapabilityReport(
            tier=CapabilityTier.FULL,
            host_capabilities=("browse", "search", "vision"),
            available_capabilities=("browse", "search", "vision"),
            missing_capabilities=(),
            degradations=(),
            python_version="3.10.20",
        )
        session = (
            PlanningSession.create(
                "af0123456789abcdef0123456789abcd", student
            )
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
            if pending.task_id == task.task_id:
                continue
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
            for candidate in bridge.candidates:
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

        self.assertEqual(len(restored.task_outcomes), 1)
        rebuilt = restored.task_outcomes[0]._bridges[0]
        self.assertEqual(rebuilt.metadata["school_code"], "SYN-A01")
        self.assertEqual(rebuilt.fact.source_ids, ("official-school-fit",))


class SchoolFitMergeTest(unittest.TestCase):
    def test_annual_fallback_selects_only_the_newest_decisive_year(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)

        def task(year):
            return next(
                item for item in active_plan.tasks
                if item.kind == "tuition_fee" and item.year == year
            )

        def bridge(year, source_id, *, amount=8000, tier=SourceTier.A,
                   second_amount=None):
            active_task = task(year)
            first_row, first_table = row_table(
                tuition_values(student, active_task, annual_fee_amount=amount),
                table_id="table[1]",
            )
            tables = [first_table]
            rows = [first_row]
            sources = [
                source(
                    source_id,
                    tier=tier,
                    publisher=f"发布方-{source_id}",
                    host=f"{source_id}.example.cn",
                )
            ]
            if second_amount is not None:
                second_row, second_table = row_table(
                    tuition_values(
                        student,
                        active_task,
                        annual_fee_amount=second_amount,
                    ),
                    table_id="table[2]",
                )
                tables.append(second_table)
                rows.append(second_row)
                sources.append(
                    source(
                        f"{source_id}-b",
                        tier=SourceTier.B,
                        publisher=f"发布方-{source_id}-b",
                        host=f"{source_id}-b.example.cn",
                    )
                )
            return bridge_school_fit_evidence(
                profile=student,
                plan=active_plan,
                task=active_task,
                tables=tuple(tables),
                adapter_rows=tuple(rows),
                candidates=tuple(sources),
            )

        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": 2025,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )

        current_missing = bridge(
            2026,
            "tuition-current-missing",
            tier=SourceTier.B,
        )
        prior_official = bridge(2025, "tuition-prior-official", amount=9000)
        fallback = merge_school_fit_metadata(
            (admission,),
            (current_missing.fact.to_dict(), prior_official.fact.to_dict()),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertEqual(fallback["tuition_annual_fee_amount"], 9000)
        self.assertEqual(fallback["school_fit_tuition_status"], "reference")
        self.assertEqual(fallback["school_fit_tuition_year"], 2025)
        self.assertEqual(
            fallback["school_fit_tuition_source_ids"],
            ("tuition-prior-official",),
        )
        self.assertNotIn("tuition_fee", fallback.get("school_fit_conflict_kinds", ()))

        partial_fact = current_missing.fact.to_dict()
        partial_fact["status"] = "partial"
        partial_only = merge_school_fit_metadata(
            (admission,),
            (partial_fact,),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertEqual(partial_only["school_fit_tuition_status"], "partial")
        self.assertEqual(partial_only["school_fit_tuition_year"], 2026)
        masked_fact = current_missing.fact.to_dict()
        masked_fact["status"] = "masked"
        masked_only = merge_school_fit_metadata(
            (admission,),
            (masked_fact,),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertEqual(masked_only["school_fit_tuition_status"], "masked")
        self.assertEqual(masked_only["school_fit_tuition_year"], 2026)

        partial_fallback = merge_school_fit_metadata(
            (admission,),
            (partial_fact, prior_official.fact.to_dict()),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertEqual(partial_fallback["school_fit_tuition_status"], "reference")
        self.assertEqual(partial_fallback["school_fit_tuition_year"], 2025)
        self.assertEqual(
            partial_fallback["school_fit_tuition_current_status"], "partial"
        )
        self.assertEqual(partial_fallback["school_fit_tuition_current_year"], 2026)
        self.assertEqual(
            partial_fallback["school_fit_tuition_current_source_ids"],
            ("tuition-current-missing",),
        )

        current_official = bridge(2026, "tuition-current-official", amount=8500)
        prior_conflict = bridge(
            2025,
            "tuition-prior-conflict",
            amount=9000,
            tier=SourceTier.B,
            second_amount=10000,
        )
        current_wins = merge_school_fit_metadata(
            (admission,),
            (current_official.fact.to_dict(), prior_conflict.fact.to_dict()),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertEqual(current_wins["tuition_annual_fee_amount"], 8500)
        self.assertEqual(current_wins["school_fit_tuition_status"], "official")
        self.assertEqual(current_wins["school_fit_tuition_year"], 2026)
        self.assertEqual(
            current_wins["school_fit_tuition_source_ids"],
            ("tuition-current-official",),
        )
        self.assertNotIn(
            "tuition_fee", current_wins.get("school_fit_conflict_kinds", ())
        )

        current_conflict = bridge(
            2026,
            "tuition-current-conflict",
            amount=8000,
            tier=SourceTier.B,
            second_amount=60000,
        )
        blocked = merge_school_fit_metadata(
            (admission,),
            (current_conflict.fact.to_dict(),),
            profile=student,
            plan=active_plan,
        )[0].to_dict()
        self.assertNotIn("tuition_annual_fee_amount", blocked)
        self.assertEqual(blocked["school_fit_tuition_status"], "conflict")
        self.assertEqual(blocked["school_fit_tuition_year"], 2026)
        self.assertEqual(
            blocked["school_fit_tuition_source_ids"],
            ("tuition-current-conflict", "tuition-current-conflict-b"),
        )
        self.assertIn("tuition_fee", blocked["school_fit_conflict_kinds"])

    def test_charter_and_tuition_merge_under_distinct_sources_and_statuses(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)
        charter_task = current_task(active_plan, "admission_charter")
        tuition_task = current_task(active_plan, "tuition_fee")
        charter_row, charter_table = row_table(
            charter_values(student, charter_task, adjustment_required=True)
        )
        tuition_row, tuition_table = row_table(
            tuition_values(student, tuition_task, annual_fee_amount=68000)
        )
        charter = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=charter_task,
            tables=(charter_table,),
            adapter_rows=(charter_row,),
            candidates=(source("official-charter"),),
        )
        tuition = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=tuition_task,
            tables=(tuition_table,),
            adapter_rows=(tuition_row,),
            candidates=(source("official-tuition"),),
        )
        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": active_plan.research_year - 1,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )

        projection = merge_school_fit_metadata(
            (admission,),
            (charter.fact.to_dict(), tuition.fact.to_dict()),
            profile=student,
            plan=active_plan,
        )[0].to_dict()

        self.assertTrue(projection["charter_adjustment_required"])
        self.assertEqual(projection["tuition_annual_fee_amount"], 68000)
        self.assertEqual(projection["tuition_affordable_for"], ("flexible",))
        self.assertEqual(
            projection["school_fit_charter_source_ids"],
            ("official-charter",),
        )
        self.assertEqual(
            projection["school_fit_tuition_source_ids"],
            ("official-tuition",),
        )
        self.assertEqual(projection["school_fit_charter_status"], "official")
        self.assertEqual(projection["school_fit_tuition_status"], "official")

    def test_province_policy_is_merged_with_its_exact_status_and_sources(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "province_policy")
        extracted_row, table = row_table(province_policy_values(student, task))
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-province-policy"),),
        )
        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": active_plan.research_year - 1,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )

        projection = merge_school_fit_metadata(
            (admission,),
            (bridge.fact.to_dict(),),
            profile=student,
            plan=active_plan,
        )[0].to_dict()

        self.assertEqual(
            projection["school_fit_province_policy_source_ids"],
            ("official-province-policy",),
        )
        self.assertEqual(
            projection["school_fit_province_policy_status"], "official"
        )
        self.assertEqual(
            projection["province_policy_exam_mode"], student.subject_mode
        )

    def test_explicit_unrestricted_subject_requirement_survives_merge(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "subject_requirement")
        extracted_row, table = row_table(
            subject_values(
                student,
                task,
                required_secondary_subjects=(),
                secondary_subject_rule="any",
                special_conditions="再选不限",
            )
        )
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-unrestricted-subject"),),
        )
        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": active_plan.research_year - 1,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )

        projection = merge_school_fit_metadata(
            (admission,),
            (bridge.fact.to_dict(),),
            profile=student,
            plan=active_plan,
        )[0].to_dict()

        self.assertEqual(projection["required_secondary_subjects"], ())
        self.assertEqual(projection["secondary_subject_rule"], "any")
        self.assertEqual(projection["subject_special_conditions"], "再选不限")

    def test_plan_and_subject_facts_merge_by_school_program_key(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)
        enrollment_task = current_task(active_plan, "enrollment_plan")
        subject_task = current_task(active_plan, "subject_requirement")
        plan_row, plan_table = row_table(enrollment_values(student, enrollment_task))
        subject_row, subject_table = row_table(subject_values(student, subject_task))
        plan_bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=enrollment_task,
            tables=(plan_table,),
            adapter_rows=(plan_row,),
            candidates=(source("official-enrollment"),),
        )
        subject_bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=subject_task,
            tables=(subject_table,),
            adapter_rows=(subject_row,),
            candidates=(source("official-subject"),),
        )
        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": active_plan.research_year - 1,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )

        merged = merge_school_fit_metadata(
            (admission,),
            (plan_bridge.fact.to_dict(), subject_bridge.fact.to_dict()),
            profile=student,
            plan=active_plan,
        )

        self.assertEqual(len(merged), 1)
        projection = merged[0].to_dict()
        self.assertEqual(projection["majors_in_group"], ("历史学", "法学"))
        self.assertEqual(projection["city_location"], "武汉")
        self.assertEqual(projection["school_province"], "湖北")
        self.assertEqual(projection["institution_type"], "public")
        self.assertNotIn("adjustment_required", projection)
        self.assertEqual(
            projection["required_secondary_subjects"], ("政治",)
        )
        self.assertEqual(projection["secondary_subject_rule"], "all")
        self.assertEqual(
            projection["school_fit_source_ids"],
            ("official-enrollment", "official-subject"),
        )

    def test_conflict_and_missing_metadata_are_not_merged(self):
        from scripts.adapters.school_fit_bridge import (
            bridge_school_fit_evidence,
            merge_school_fit_metadata,
        )

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "enrollment_plan")
        first_row, first_table = row_table(
            enrollment_values(student, task, school_city="武汉"), table_id="table[1]"
        )
        second_row, second_table = row_table(
            enrollment_values(student, task, school_city="宜昌"), table_id="table[2]"
        )
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(first_table, second_table),
            adapter_rows=(first_row, second_row),
            candidates=(
                source("conflict-a", tier=SourceTier.B, publisher="甲", host="a.example.cn"),
                source("conflict-b", tier=SourceTier.B, publisher="乙", host="b.example.cn"),
            ),
        )
        admission = ValidatedAdmissionRow.from_mapping(
            {
                "year": active_plan.research_year - 1,
                "province": student.province,
                "subject_group": active_plan.subject_group,
                "school_code": "SYN-A01",
                "school_name": "合成示例大学",
                "program_group": "第01组",
                "min_score": 605,
                "min_rank": 20000,
                "remarks": "",
            }
        )

        projection = merge_school_fit_metadata(
            (admission,),
            (bridge.fact.to_dict(),),
            profile=student,
            plan=active_plan,
        )[0].to_dict()

        self.assertNotIn("city_location", projection)
        self.assertNotIn("institution_type", projection)
        self.assertEqual(
            projection["school_fit_conflict_kinds"],
            ("enrollment_plan",),
        )
        self.assertEqual(
            projection["school_fit_source_ids"],
            ("conflict-a", "conflict-b"),
        )


class SchoolFitResearchSnapshotTest(unittest.TestCase):
    def test_bundle_snapshot_merges_typed_fit_facts_before_calculation(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence
        from scripts.research_snapshot import (
            build_research_snapshot,
            validate_research_snapshot,
        )
        from scripts.generate_report import _public_admission_rows
        from tests.test_research_snapshot import bridges

        student, active_plan, rank_bridge, admission_bridge = bridges()
        enrollment_task = current_task(active_plan, "enrollment_plan")
        charter_task = current_task(active_plan, "admission_charter")
        tuition_task = current_task(active_plan, "tuition_fee")
        subject_task = current_task(active_plan, "subject_requirement")
        enrollment_row, enrollment_table = row_table(
            enrollment_values(
                student,
                enrollment_task,
                institution=admission_bridge.dataset_row.school_name,
                institution_code=admission_bridge.dataset_row.school_code,
                program_group=admission_bridge.dataset_row.program_group,
            )
        )
        subject_row, subject_table = row_table(
            subject_values(
                student,
                subject_task,
                institution=admission_bridge.dataset_row.school_name,
                institution_code=admission_bridge.dataset_row.school_code,
                program_group=admission_bridge.dataset_row.program_group,
            )
        )
        charter_row, charter_table = row_table(
            charter_values(
                student,
                charter_task,
                institution=admission_bridge.dataset_row.school_name,
                institution_code=admission_bridge.dataset_row.school_code,
            )
        )
        tuition_row, tuition_table = row_table(
            tuition_values(
                student,
                tuition_task,
                institution=admission_bridge.dataset_row.school_name,
                institution_code=admission_bridge.dataset_row.school_code,
                program_group=admission_bridge.dataset_row.program_group,
            )
        )
        enrollment_bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=enrollment_task,
            tables=(enrollment_table,),
            adapter_rows=(enrollment_row,),
            candidates=(
                source(
                    "snapshot-enrollment",
                    publisher="湖北省招生计划发布机关",
                    host="plan.hubei.gov.cn",
                ),
            ),
        )
        subject_bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=subject_task,
            tables=(subject_table,),
            adapter_rows=(subject_row,),
            candidates=(
                source(
                    "snapshot-subject",
                    publisher="湖北省选科要求发布机关",
                    host="subject.hubei.gov.cn",
                ),
            ),
        )
        charter_bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=charter_task,
            tables=(charter_table,),
            adapter_rows=(charter_row,),
            candidates=(
                source(
                    "snapshot-charter",
                    publisher="合成示例大学招生办公室",
                    host="charter.example.edu.cn",
                ),
            ),
        )
        tuition_bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=tuition_task,
            tables=(tuition_table,),
            adapter_rows=(tuition_row,),
            candidates=(
                source(
                    "snapshot-tuition",
                    publisher="合成示例大学财务处",
                    host="tuition.example.edu.cn",
                ),
            ),
        )
        capability = CapabilityReport(
            tier=CapabilityTier.FULL,
            host_capabilities=("browse", "search", "vision"),
            available_capabilities=("browse", "search", "vision"),
            missing_capabilities=(),
            degradations=(),
            python_version="3.10.20",
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(Path(temporary).resolve(), capability)
            all_bridges = (
                rank_bridge,
                admission_bridge,
                charter_bridge,
                enrollment_bridge,
                subject_bridge,
                tuition_bridge,
            )
            for bridge in all_bridges:
                for candidate in bridge.candidates:
                    store.add_candidate(candidate)
                bridge.persist(store)
            store.finalize()
            validation = validate_bundle_snapshot(store.session_path)
            self.assertEqual(validation.issues, ())
            assert validation.snapshot is not None
            persisted_facts = tuple(
                item.to_dict() for item in validation.snapshot.facts
            )
            snapshot = build_research_snapshot(
                student,
                active_plan,
                store.session_path,
                DecisionPolicySnapshot.load_default(),
            )

        row = snapshot.admission_rows[0].to_dict()
        self.assertEqual(row["majors_in_group"], ("历史学", "法学"))
        self.assertEqual(row["required_secondary_subjects"], ("政治",))
        self.assertFalse(row["charter_adjustment_required"])
        self.assertEqual(
            row["tuition_affordable_for"],
            ("limited", "moderate", "flexible"),
        )
        self.assertEqual(
            row["school_fit_source_ids"],
            (
                "snapshot-charter",
                "snapshot-enrollment",
                "snapshot-subject",
                "snapshot-tuition",
            ),
        )
        self.assertEqual(
            tuple(fact["field"].split(":")[1] for fact in snapshot.school_fit_facts),
            (
                "admission_charter",
                "enrollment_plan",
                "subject_requirement",
                "tuition_fee",
            ),
        )
        public_rows = _public_admission_rows(
            snapshot.admission_rows,
            RecommendationProfile(
                rank=20000,
                target_province=student.province,
                subject_group=active_plan.subject_group,
                secondary_subjects=frozenset(student.secondary_subjects),
            ),
            persisted_facts,
        )
        self.assertEqual(public_rows[0]["evidence_status"], "official")
        self.assertEqual(public_rows[0]["city_location"], "武汉")
        validate_research_snapshot(snapshot, student)

    def test_persisted_school_fit_fact_without_factory_origin_is_rejected(self):
        from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence
        from scripts.research_snapshot import (
            ResearchSnapshotError,
            build_research_snapshot,
        )

        student = profile()
        active_plan = plan(student)
        task = current_task(active_plan, "tuition_fee")
        extracted_row, table = row_table(tuition_values(student, task))
        bridge = bridge_school_fit_evidence(
            profile=student,
            plan=active_plan,
            task=task,
            tables=(table,),
            adapter_rows=(extracted_row,),
            candidates=(source("official-forged-fit"),),
        )
        forged_value = dict(bridge.fact.value)
        forged_value["annual_fee_amount"] = 1
        forged = EvidenceFact(
            fact_id=bridge.fact.fact_id,
            field=bridge.fact.field,
            value=forged_value,
            unit=bridge.fact.unit,
            status=bridge.fact.status,
            source_ids=bridge.fact.source_ids,
            method=bridge.fact.method,
            notes=bridge.fact.notes,
        )
        capability = CapabilityReport(
            tier=CapabilityTier.FULL,
            host_capabilities=("browse",),
            available_capabilities=("browse",),
            missing_capabilities=(),
            degradations=(),
            python_version="3.10.20",
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(Path(temporary), capability)
            store.add_candidate(bridge.candidates[0])
            store.add_fact(
                forged,
                year=task.year,
                extraction_method=bridge.extraction_method,
                locator=bridge.locator,
            )
            store.finalize()

            with self.assertRaisesRegex(
                ResearchSnapshotError,
                "factory origin|typed bridge",
            ):
                build_research_snapshot(
                    student,
                    active_plan,
                    store.session_path,
                    DecisionPolicySnapshot.load_default(),
                )


class SchoolFitDecisionTraceTest(unittest.TestCase):
    @staticmethod
    def _scenario():
        from scripts.rank_locator import RankScenario

        return RankScenario._create(
            status=EvidenceStatus.INFERRED,
            basis="authenticated_interval",
            optimistic_rank=18000,
            central_rank=20000,
            conservative_rank=22000,
            confidence="medium",
            source_ids=("rank-source",),
            contributing_years=(2025,),
            backtest_error=0.05,
            reasons=("authenticated_interval",),
            channel_kinds=("school_anchor",),
            channel_statuses=("official",),
            rejected_channel_count=0,
        )

    @staticmethod
    def _row(**changes):
        row = {
            "year": 2025,
            "province": "湖北",
            "subject_group": "历史+政治+地理",
            "school_code": "SYN-A01",
            "school_name": "合成示例大学",
            "program_group": "第01组",
            "min_score": 605,
            "min_rank": 20000,
            "remarks": "",
            "evidence_status": "official",
            "coverage_status": "official",
            "coverage_min_rank": 15000,
            "coverage_max_rank": 25000,
            "source_ids": ("admission-source",),
        }
        row.update(changes)
        return row

    def test_fit_sources_survive_into_field_specific_decision_reasons(self):
        from scripts.planning_profile import PlanningProfile
        from scripts.school_recommend import personalize_school_recommendations

        student = PlanningProfile.create(reference_payload())
        result = personalize_school_recommendations(
            (
                self._row(
                    majors_in_group=("历史学", "法学"),
                    school_province="湖北",
                    city_location="武汉",
                    institution_type="public",
                    required_secondary_subjects=("政治",),
                    secondary_subject_rule="all",
                    adjustment_required=False,
                    affordable_for=("limited", "moderate", "flexible"),
                    charter_adjustment_required=False,
                    tuition_affordable_for=("limited", "moderate", "flexible"),
                    school_fit_source_ids=(
                        "fit-charter",
                        "fit-plan",
                        "fit-subject",
                        "fit-tuition",
                    ),
                    school_fit_enrollment_source_ids=("fit-plan",),
                    school_fit_subject_source_ids=("fit-subject",),
                    school_fit_charter_source_ids=("fit-charter",),
                    school_fit_tuition_source_ids=("fit-tuition",),
                    school_fit_province_policy_source_ids=("fit-province",),
                    school_fit_enrollment_status="reference",
                    school_fit_subject_status="official",
                    school_fit_charter_status="official",
                    school_fit_tuition_status="reference",
                    school_fit_province_policy_status="corroborated",
                    province_policy_exam_mode=student.subject_mode,
                ),
            ),
            student,
            None,
            rank_scenario=self._scenario(),
            subject_selection_key="历史+政治+地理",
        )

        decision = result.decision("合成示例大学")
        reasons = {reason.code: reason for reason in decision.reasons}
        self.assertLessEqual(
            {
                "admission-source",
                "fit-charter",
                "fit-plan",
                "fit-subject",
                "fit-tuition",
                "fit-province",
            },
            set(result.items[0].source_ids),
        )
        self.assertIs(
            reasons["SCHOOL_PROVINCE_POLICY_MATCH"].evidence_status,
            EvidenceStatus.CORROBORATED,
        )
        self.assertIs(
            reasons["SCHOOL_TARGET_MAJOR_COMMITTED"].evidence_status,
            EvidenceStatus.REFERENCE,
        )
        self.assertIs(
            reasons["SCHOOL_SUBJECT_MATCH"].evidence_status,
            EvidenceStatus.OFFICIAL,
        )
        for code in (
            "SCHOOL_PROVINCE_POLICY_MATCH",
            "SCHOOL_SUBJECT_MATCH",
            "SCHOOL_TARGET_MAJOR_COMMITTED",
            "SCHOOL_TARGET_REGION_MATCH",
            "SCHOOL_INSTITUTION_TYPE_MATCH",
            "SCHOOL_AFFORDABILITY_MATCH",
            "SCHOOL_ADJUSTMENT_MATCH",
        ):
            self.assertIn(code, reasons)
            self.assertTrue(
                {
                    "fit-charter",
                    "fit-plan",
                    "fit-subject",
                    "fit-tuition",
                    "fit-province",
                }.intersection(
                    reasons[code].source_ids
                ),
                code,
            )

        from scripts.docx_export import export_docx
        from scripts.report_model import StudentProfile, build_report_model, render_markdown
        from tests.test_generate_report_evidence import evidence_snapshot

        report = build_report_model(
            StudentProfile(
                province=student.province,
                subject_mode=student.subject_mode,
                subject_group=student.subject_group,
                secondary_subjects=student.secondary_subjects,
                rank=20000,
                grade=student.grade,
                current_year=2026,
            ),
            result,
            result.rank_scenario,
            None,
            evidence_snapshot(),
            planning_profile=student,
        )
        self.assertLessEqual(
            {
                "admission-source",
                "fit-charter",
                "fit-plan",
                "fit-subject",
                "fit-tuition",
                "fit-province",
            },
            set(report.source_ids),
        )
        self.assertEqual(
            set(report.recommendations[0].fit_evidence_statuses),
            {
                EvidenceStatus.OFFICIAL,
                EvidenceStatus.CORROBORATED,
                EvidenceStatus.REFERENCE,
            },
        )
        markdown = render_markdown(report)
        self.assertIn("普通批逐维判断证据", markdown)
        self.assertIn("[SCHOOL_SUBJECT_MATCH]", markdown)
        self.assertIn("证据等级：官方；来源编号：fit-subject", markdown)
        with tempfile.TemporaryDirectory() as temporary:
            output = export_docx(report, Path(temporary) / "school-fit.docx")
            from docx import Document

            document = Document(output)
            text = "\n".join(
                paragraph.text for paragraph in document.paragraphs
            )
        self.assertIn("普通批逐维判断证据", text)
        self.assertIn("[SCHOOL_SUBJECT_MATCH]", text)
        self.assertIn("证据等级：官方；来源编号：fit-subject", text)

    def test_enrollment_compatibility_fields_cannot_authorize_charter_or_tuition(self):
        from scripts.planning_profile import PlanningProfile
        from scripts.school_recommend import personalize_school_recommendations

        student = PlanningProfile.create(reference_payload())
        legacy_only = self._row(
            majors_in_group=("历史学", "法学"),
            school_province="湖北",
            city_location="武汉",
            institution_type="public",
            adjustment_required=False,
            affordable_for=("limited", "moderate", "flexible"),
            school_fit_source_ids=("fit-enrollment",),
            school_fit_enrollment_source_ids=("fit-enrollment",),
            school_fit_enrollment_status="official",
        )

        legacy_reasons = {
            reason.code: reason
            for reason in personalize_school_recommendations(
                (legacy_only,),
                student,
                None,
                rank_scenario=self._scenario(),
                subject_selection_key="历史+政治+地理",
            ).decision("合成示例大学").reasons
        }
        self.assertIs(
            legacy_reasons["SCHOOL_ADJUSTMENT_UNVERIFIED"].evidence_status,
            EvidenceStatus.MISSING,
        )
        self.assertIs(
            legacy_reasons["SCHOOL_AFFORDABILITY_UNVERIFIED"].evidence_status,
            EvidenceStatus.MISSING,
        )
        self.assertNotIn(
            "fit-enrollment",
            legacy_reasons["SCHOOL_ADJUSTMENT_UNVERIFIED"].source_ids,
        )
        self.assertNotIn(
            "fit-enrollment",
            legacy_reasons["SCHOOL_AFFORDABILITY_UNVERIFIED"].source_ids,
        )

        dedicated = {
            **legacy_only,
            "charter_adjustment_required": False,
            "tuition_affordable_for": ("limited", "moderate", "flexible"),
            "school_fit_source_ids": (
                "fit-charter",
                "fit-enrollment",
                "fit-tuition",
            ),
            "school_fit_charter_source_ids": ("fit-charter",),
            "school_fit_tuition_source_ids": ("fit-tuition",),
            "school_fit_charter_status": "official",
            "school_fit_tuition_status": "reference",
        }
        dedicated_reasons = {
            reason.code: reason
            for reason in personalize_school_recommendations(
                (dedicated,),
                student,
                None,
                rank_scenario=self._scenario(),
                subject_selection_key="历史+政治+地理",
            ).decision("合成示例大学").reasons
        }
        self.assertEqual(
            dedicated_reasons["SCHOOL_ADJUSTMENT_MATCH"].source_ids,
            ("fit-charter",),
        )
        self.assertIs(
            dedicated_reasons["SCHOOL_ADJUSTMENT_MATCH"].evidence_status,
            EvidenceStatus.OFFICIAL,
        )
        self.assertEqual(
            dedicated_reasons["SCHOOL_AFFORDABILITY_MATCH"].source_ids,
            ("fit-tuition",),
        )
        self.assertIs(
            dedicated_reasons["SCHOOL_AFFORDABILITY_MATCH"].evidence_status,
            EvidenceStatus.REFERENCE,
        )

    def test_charter_optional_restrictions_become_traceable_manual_review(self):
        from scripts.planning_profile import PlanningProfile
        from scripts.school_recommend import personalize_school_recommendations

        payload = deepcopy(reference_payload())
        payload["constraints"]["health_constraints"] = ["色觉限制"]
        student = PlanningProfile.create(payload)
        partial_row = self._row(
            charter_adjustment_required=False,
            charter_health_restrictions="按招生体检指导意见执行",
            charter_unverified_fields=(
                "language_restrictions",
                "single_subject_restrictions",
                "special_conditions",
            ),
            school_fit_source_ids=("fit-charter",),
            school_fit_charter_source_ids=("fit-charter",),
            school_fit_charter_status="official",
        )
        result = personalize_school_recommendations(
            (partial_row,),
            student,
            None,
            rank_scenario=self._scenario(),
            subject_selection_key="历史+政治+地理",
        )
        reasons = {
            reason.code: reason
            for reason in result.decision("合成示例大学").reasons
        }
        health = reasons["SCHOOL_CHARTER_HEALTH_REVIEW_REQUIRED"]
        self.assertEqual(health.source_ids, ("fit-charter",))
        self.assertIs(health.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(
            health.input_fields,
            (),
        )
        restrictions = reasons["SCHOOL_CHARTER_RESTRICTIONS_UNVERIFIED"]
        self.assertEqual(restrictions.source_ids, ("fit-charter",))
        self.assertIs(restrictions.evidence_status, EvidenceStatus.MISSING)
        self.assertEqual(
            restrictions.input_fields,
            (),
        )
        self.assertTrue(any("不得解释为" in warning for warning in result.warnings))

        exact_row = {
            **partial_row,
            "charter_language_restrictions": "不限外语语种",
            "charter_single_subject_restrictions": "无单科成绩限制",
            "charter_special_conditions": "无其他特殊条件",
            "charter_unverified_fields": (),
        }
        exact_reasons = {
            reason.code: reason
            for reason in personalize_school_recommendations(
                (exact_row,),
                student,
                None,
                rank_scenario=self._scenario(),
                subject_selection_key="历史+政治+地理",
            ).decision("合成示例大学").reasons
        }
        self.assertIn(
            "SCHOOL_CHARTER_RESTRICTIONS_REVIEW_REQUIRED",
            exact_reasons,
        )

    def test_missing_or_conflicting_fit_metadata_is_explicitly_unverified(self):
        from scripts.planning_profile import PlanningProfile
        from scripts.school_recommend import personalize_school_recommendations

        student = PlanningProfile.create(reference_payload())
        missing = personalize_school_recommendations(
            (self._row(),),
            student,
            None,
            rank_scenario=self._scenario(),
            subject_selection_key="历史+政治+地理",
        ).decision("合成示例大学")
        missing_codes = {reason.code for reason in missing.reasons}
        self.assertNotIn("SCHOOL_SUBJECT_MATCH", missing_codes)
        self.assertLessEqual(
            {
                "SCHOOL_SUBJECT_UNVERIFIED",
                "SCHOOL_TARGET_MAJOR_UNVERIFIED",
                "SCHOOL_REGION_UNVERIFIED",
                "SCHOOL_INSTITUTION_TYPE_UNVERIFIED",
                "SCHOOL_AFFORDABILITY_UNVERIFIED",
                "SCHOOL_ADJUSTMENT_UNVERIFIED",
            },
            missing_codes,
        )

        conflict = personalize_school_recommendations(
            (
                self._row(
                    school_fit_conflict_kinds=(
                        "admission_charter",
                        "enrollment_plan",
                        "tuition_fee",
                    ),
                    school_fit_source_ids=("fit-conflict-a", "fit-conflict-b"),
                ),
            ),
            student,
            None,
            rank_scenario=self._scenario(),
            subject_selection_key="历史+政治+地理",
        ).decision("合成示例大学")
        conflict_reasons = {reason.code: reason for reason in conflict.reasons}
        for code in (
            "SCHOOL_TARGET_MAJOR_UNVERIFIED",
            "SCHOOL_REGION_UNVERIFIED",
            "SCHOOL_INSTITUTION_TYPE_UNVERIFIED",
            "SCHOOL_AFFORDABILITY_UNVERIFIED",
            "SCHOOL_ADJUSTMENT_UNVERIFIED",
        ):
            reason = conflict_reasons[code]
            self.assertIs(reason.evidence_status, EvidenceStatus.CONFLICT)
            self.assertTrue(
                {"fit-conflict-a", "fit-conflict-b"}.intersection(
                    reason.source_ids
                )
            )
        self.assertIs(
            conflict_reasons["SCHOOL_SUBJECT_UNVERIFIED"].evidence_status,
            EvidenceStatus.MISSING,
        )

    def test_each_q14_q16_q20_school_fit_input_changes_only_its_decision_result(self):
        from scripts.planning_profile import PlanningProfile
        from scripts.school_recommend import personalize_school_recommendations

        def student(*, majors, regions, excluded=(), budget="flexible",
                    institution_types=("public", "cooperative"),
                    adjustment="consider"):
            payload = deepcopy(reference_payload())
            payload["priorities"]["target_majors"] = list(majors)
            payload["priorities"]["target_regions"] = list(regions)
            payload["constraints"]["excluded_regions"] = list(excluded)
            payload["constraints"]["budget_level"] = budget
            payload["constraints"]["institution_types"] = list(
                institution_types
            )
            payload["constraints"]["adjustment_preference"] = adjustment
            return PlanningProfile.create(payload)

        public = self._row(
            school_code="SYN-P",
            school_name="公办历史武汉大学",
            min_rank=19000,
            majors_in_group=("历史学",),
            school_province="湖北",
            city_location="武汉",
            institution_type="public",
            required_secondary_subjects=("政治",),
            secondary_subject_rule="all",
            adjustment_required=False,
            affordable_for=("limited", "moderate", "flexible"),
            charter_adjustment_required=False,
            tuition_affordable_for=("limited", "moderate", "flexible"),
            school_fit_source_ids=(
                "fit-public-charter",
                "fit-public-plan",
                "fit-public-subject",
                "fit-public-tuition",
            ),
            school_fit_enrollment_source_ids=("fit-public-plan",),
            school_fit_subject_source_ids=("fit-public-subject",),
            school_fit_charter_source_ids=("fit-public-charter",),
            school_fit_tuition_source_ids=("fit-public-tuition",),
            school_fit_enrollment_status="official",
            school_fit_subject_status="official",
            school_fit_charter_status="official",
            school_fit_tuition_status="official",
        )
        cooperative = self._row(
            school_code="SYN-C",
            school_name="上海高费调剂法学大学",
            min_rank=19500,
            majors_in_group=("法学",),
            school_province="上海",
            city_location="上海",
            institution_type="cooperative",
            required_secondary_subjects=("政治",),
            secondary_subject_rule="all",
            adjustment_required=True,
            affordable_for=("flexible",),
            charter_adjustment_required=True,
            tuition_affordable_for=("flexible",),
            school_fit_source_ids=(
                "fit-cooperative-charter",
                "fit-cooperative-plan",
                "fit-cooperative-subject",
                "fit-cooperative-tuition",
            ),
            school_fit_enrollment_source_ids=("fit-cooperative-plan",),
            school_fit_subject_source_ids=("fit-cooperative-subject",),
            school_fit_charter_source_ids=("fit-cooperative-charter",),
            school_fit_tuition_source_ids=("fit-cooperative-tuition",),
            school_fit_enrollment_status="official",
            school_fit_subject_status="official",
            school_fit_charter_status="official",
            school_fit_tuition_status="official",
        )
        rows = (public, cooperative)

        def evaluate(profile):
            return personalize_school_recommendations(
                rows,
                profile,
                None,
                rank_scenario=self._scenario(),
                subject_selection_key="历史+政治+地理",
            )

        history = evaluate(
            student(majors=("历史学",), regions=("武汉", "上海"))
        )
        law = evaluate(student(majors=("法学",), regions=("武汉", "上海")))
        self.assertEqual(
            tuple(item.school_name for item in history.items),
            ("公办历史武汉大学", "上海高费调剂法学大学"),
        )
        self.assertEqual(
            tuple(item.school_name for item in law.items),
            ("上海高费调剂法学大学", "公办历史武汉大学"),
        )

        wuhan = evaluate(
            student(majors=("历史学", "法学"), regions=("武汉",))
        )
        shanghai = evaluate(
            student(majors=("历史学", "法学"), regions=("上海",))
        )
        self.assertEqual(wuhan.items[0].school_name, "公办历史武汉大学")
        self.assertEqual(shanghai.items[0].school_name, "上海高费调剂法学大学")

        cases = (
            (
                student(
                    majors=("历史学", "法学"),
                    regions=("武汉",),
                    excluded=("上海",),
                ),
                "上海高费调剂法学大学",
                "SCHOOL_EXCLUDED_REGION",
                "fit-cooperative-plan",
            ),
            (
                student(
                    majors=("历史学", "法学"),
                    regions=("武汉",),
                    budget="limited",
                ),
                "上海高费调剂法学大学",
                "SCHOOL_AFFORDABILITY_BLOCKED",
                "fit-cooperative-tuition",
            ),
            (
                student(
                    majors=("历史学", "法学"),
                    regions=("武汉",),
                    institution_types=("cooperative",),
                ),
                "公办历史武汉大学",
                "SCHOOL_INSTITUTION_TYPE_BLOCKED",
                "fit-public-plan",
            ),
            (
                student(
                    majors=("历史学", "法学"),
                    regions=("武汉",),
                    adjustment="reject",
                ),
                "上海高费调剂法学大学",
                "SCHOOL_ADJUSTMENT_BLOCKED",
                "fit-cooperative-charter",
            ),
        )
        for active_profile, school_name, code, fit_source in cases:
            with self.subTest(code=code):
                decision = evaluate(active_profile).decision(school_name)
                self.assertEqual(decision.outcome, "excluded")
                reason = next(item for item in decision.reasons if item.code == code)
                self.assertIn(fit_source, reason.source_ids)


if __name__ == "__main__":
    unittest.main()
