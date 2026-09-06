"""Focused source-to-receipt disambiguation tests for Task 9."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.adapters.admission_bridge import bridge_admission_evidence
from scripts.adapters.school_fit_bridge import bridge_school_fit_evidence
from scripts.contracts import EvidenceStatus, SourceCandidate, SourceTier
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.planning_profile import PlanningProfile
from scripts.planning_session import (
    SessionTransitionError,
    _matching_source_receipt,
    _validate_displayed_school_observation,
    _validate_displayed_school_sources,
    build_task_evidence_outcome,
)
from scripts.query_plan import build_query_plan, load_province_catalog
from scripts.validate_data import ValidatedAdmissionRow
from tests.test_planning_profile import reference_payload


def _outcome(task_id, kind, *, policy_id=None, usable=True):
    bridge = SimpleNamespace(
        source_ids=("shared-policy-page",),
        policy_id=policy_id,
        coverage_status=SimpleNamespace(value="official"),
    )
    return SimpleNamespace(
        task_id=task_id,
        kind=kind,
        year=2026,
        usable=usable,
        _bridges=(bridge,),
    )


def _profile_and_plan():
    student = PlanningProfile.create(reference_payload())
    plan = build_query_plan(
        student,
        load_province_catalog(),
        DecisionPolicySnapshot.load_default(),
    )
    return student, plan


def _admission_bridge(
    task,
    *,
    school_name,
    min_score,
    min_rank,
    fact_id,
    coverage_status,
    program_group="第01组",
):
    candidate = SourceCandidate(
        source_id="shared-admission-page",
        url="https://zs.synthetic.invalid/shared-admission",
        publisher="合成省级招生考试院",
        tier=SourceTier.A,
        published_at=f"{task.year}-06-25",
        retrieved_at="2026-08-30T00:00:00Z",
        content_hash="sha256:" + "c" * 64,
        citation_root="https://zs.synthetic.invalid/",
        summary="合成投档行",
    )
    values = {
        "school_code": fact_id.upper().replace("-", "")[:12],
        "school_name": school_name,
        "program_group": program_group,
        "min_score": min_score,
        "min_rank": min_rank,
    }
    row = ExtractedRow(
        values=values,
        cell_status={key: CellStatus.EXACT for key in values},
        location=f"table[1]/row[{fact_id}]",
        confidence=1,
    )
    table = ExtractedTable(
        table_id=f"admission-{fact_id}",
        caption="合成普通批投档表",
        sheet=None,
        rows=(row,),
        coverage=ExtractedCoverage(
            lower_score=min_score - 10,
            upper_score=min_score + 10,
            lower_rank=max(1, min_rank - 1000),
            upper_rank=min_rank + 1000,
        ),
        warnings=(),
        extraction_method="html-table",
    )
    dataset = ValidatedAdmissionRow.from_mapping(
        {
            "year": task.year,
            "province": task.province,
            "subject_group": task.subject_group,
            **values,
            "remarks": "",
        }
    )
    return bridge_admission_evidence(
        table=table,
        adapter_row=row,
        task=task,
        dataset_row=dataset,
        fact_id=fact_id,
        candidates=(candidate,),
        coverage_status=coverage_status,
    )


class SourceReceiptBindingTest(unittest.TestCase):
    def test_one_public_source_can_bind_admission_and_school_fit_receipts(self):
        student, plan = _profile_and_plan()
        admission_task = next(
            task for task in plan.tasks
            if task.kind == "batch_admission" and task.year == plan.research_year
        )
        fit_task = next(
            task for task in plan.tasks
            if task.kind == "enrollment_plan" and task.year == plan.research_year
        )
        admission = _admission_bridge(
            admission_task,
            school_name="共享来源大学",
            min_score=610,
            min_rank=18000,
            fact_id="shared-row",
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        values = {
            "province": fit_task.province,
            "year": fit_task.year,
            "subject_group": fit_task.subject_group,
            "institution": "共享来源大学",
            "institution_code": admission.dataset_row.school_code,
            "program_group": "第01组",
            "majors": ("历史学",),
            "school_province": "湖北",
            "school_city": "武汉",
            "institution_type": "public",
        }
        fit_row = ExtractedRow(
            values=values,
            cell_status={key: CellStatus.EXACT for key in values},
            location="table[2]/row[1]",
            confidence=1,
        )
        fit_table = ExtractedTable(
            table_id="shared-enrollment",
            caption="同一公开页面的招生计划表",
            sheet=None,
            rows=(fit_row,),
            coverage=ExtractedCoverage(),
            warnings=(),
            extraction_method="html-table",
        )
        fit = bridge_school_fit_evidence(
            profile=student,
            plan=plan,
            task=fit_task,
            tables=(fit_table,),
            adapter_rows=(fit_row,),
            candidates=admission.candidates,
        )
        outcomes = (
            build_task_evidence_outcome(student, plan, admission_task, (admission,)),
            build_task_evidence_outcome(student, plan, fit_task, (fit,)),
        )
        item = SimpleNamespace(
            school_name="共享来源大学",
            min_score=610,
            min_rank=18000,
            major_groups=("第01组",),
            data_year=admission_task.year,
            supporting_years=(admission_task.year,),
            source_ids=("shared-admission-page",),
            evidence_status=EvidenceStatus.OFFICIAL,
        )

        _validate_displayed_school_sources(item, outcomes)
        for mismatch in (
            {"school_name": "无关大学"},
            {"major_groups": ("第02组",)},
            {"min_rank": 19000},
            {
                "data_year": admission_task.year - 1,
                "supporting_years": (admission_task.year - 1,),
            },
            {"source_ids": ("unbound-public-page",)},
        ):
            with self.subTest(mismatch=mismatch):
                with self.assertRaises(SessionTransitionError):
                    _validate_displayed_school_sources(
                        SimpleNamespace(**{**item.__dict__, **mismatch}), outcomes
                    )

    def test_history_matches_the_selected_program_group(self):
        student, plan = _profile_and_plan()
        tasks = sorted((t for t in plan.tasks if t.kind == "batch_admission"
                        and t.target_name == "普通批"), key=lambda t: -t.year)[:2]
        current = _admission_bridge(tasks[0], school_name="示例大学", min_score=610,
            min_rank=18000, fact_id="current-one", coverage_status=EvidenceStatus.OFFICIAL)
        old_one = _admission_bridge(tasks[1], school_name="示例大学", min_score=609,
            min_rank=18500, fact_id="old-one", coverage_status=EvidenceStatus.OFFICIAL)
        old_two = _admission_bridge(tasks[1], school_name="示例大学", min_score=600,
            min_rank=20000, fact_id="old-two", coverage_status=EvidenceStatus.OFFICIAL,
            program_group="第02组")
        outcomes = (
            build_task_evidence_outcome(student, plan, tasks[0], (current,)),
            build_task_evidence_outcome(student, plan, tasks[1], (old_one, old_two)),
        )
        item = SimpleNamespace(school_name="示例大学", min_score=610, min_rank=18000,
            major_groups=("第01组",), data_year=tasks[0].year,
            supporting_years=tuple(t.year for t in tasks),
            source_ids=("shared-admission-page",), evidence_status=EvidenceStatus.OFFICIAL)
        _validate_displayed_school_sources(item, outcomes)

    def test_shared_source_can_be_uniquely_resolved_by_kind_and_policy(self):
        outcomes = (
            _outcome("batch:one", "batch_admission"),
            _outcome("path:one", "special_pathway", policy_id="policy-one"),
        )

        match = _matching_source_receipt(
            outcomes,
            "shared-policy-page",
            kinds={"special_pathway"},
            years={2026},
            require_usable=True,
            policy_id="policy-one",
        )

        self.assertIs(match[0], outcomes[1])

    def test_same_semantic_condition_remains_ambiguous(self):
        outcomes = (
            _outcome("path:one", "special_pathway", policy_id="policy-one"),
            _outcome("path:two", "special_pathway", policy_id="policy-one"),
        )

        self.assertIsNone(
            _matching_source_receipt(
                outcomes,
                "shared-policy-page",
                kinds={"special_pathway"},
                years={2026},
                require_usable=True,
                policy_id="policy-one",
            )
        )

    def test_shared_source_across_declared_supporting_years_is_not_ambiguous(self):
        outcomes = (
            _outcome("batch:2026", "batch_admission"),
            SimpleNamespace(
                **{
                    **_outcome("batch:2025", "batch_admission").__dict__,
                    "year": 2025,
                }
            ),
        )
        for outcome in outcomes:
            bridge = outcome._bridges[0]
            bridge.dataset_row = SimpleNamespace(
                year=outcome.year,
                school_name="示例大学",
                min_score=610,
                min_rank=18000,
            )
            bridge.evidence_status = SimpleNamespace(value="official")

        item = SimpleNamespace(
            school_name="示例大学",
            min_score=610,
            min_rank=18000,
            data_year=2026,
            supporting_years=(2025, 2026),
            source_ids=("shared-policy-page",),
            evidence_status=SimpleNamespace(value="official"),
        )

        _validate_displayed_school_sources(item, outcomes)

    def test_mixed_task_authorizes_exact_row_but_not_partial_sibling(self):
        exact = _outcome("batch:mixed", "batch_admission", usable=False)
        exact_bridge = exact._bridges[0]
        exact_bridge.dataset_row = SimpleNamespace(
            year=2026,
            school_name="完整大学",
            min_score=610,
            min_rank=18000,
        )
        exact_bridge.evidence_status = SimpleNamespace(value="official")
        partial_bridge = SimpleNamespace(
            source_ids=("shared-policy-page",),
            policy_id=None,
            coverage_status=SimpleNamespace(value="partial"),
            evidence_status=SimpleNamespace(value="official"),
            dataset_row=SimpleNamespace(
                year=2026,
                school_name="部分大学",
                min_score=600,
                min_rank=20000,
            ),
        )
        exact._bridges = (exact_bridge, partial_bridge)

        complete_item = SimpleNamespace(
            school_name="完整大学",
            min_score=610,
            min_rank=18000,
            data_year=2026,
            supporting_years=(2026,),
            source_ids=("shared-policy-page",),
            evidence_status=SimpleNamespace(value="official"),
        )
        forged_exact = SimpleNamespace(
            **{
                **complete_item.__dict__,
                "school_name": "部分大学",
                "min_score": 600,
                "min_rank": 20000,
            }
        )
        disclosed_partial = SimpleNamespace(
            **{
                **forged_exact.__dict__,
                "evidence_status": SimpleNamespace(value="partial"),
            }
        )

        _validate_displayed_school_sources(complete_item, (exact,))
        with self.assertRaisesRegex(
            SessionTransitionError,
            "matching exact admission rows",
        ):
            _validate_displayed_school_sources(forged_exact, (exact,))
        with self.assertRaisesRegex(
            SessionTransitionError,
            "partial|exact",
        ):
            _validate_displayed_school_sources(disclosed_partial, (exact,))

        observation = SimpleNamespace(
            school_name="部分大学",
            data_year=2026,
            source_ids=("shared-policy-page",),
            evidence_status=SimpleNamespace(value="partial"),
        )
        _validate_displayed_school_observation(observation, (exact,))
        forged_observation = SimpleNamespace(
            **{**observation.__dict__, "school_name": "无关大学"}
        )
        with self.assertRaisesRegex(
            SessionTransitionError,
            "observation",
        ):
            _validate_displayed_school_observation(forged_observation, (exact,))

    def test_same_outcome_duplicate_school_bridges_are_ambiguous(self):
        exact = _outcome("batch:duplicate-exact", "batch_admission")
        exact_bridge = exact._bridges[0]
        exact_bridge.dataset_row = SimpleNamespace(
            year=2026,
            school_name="重复大学",
            min_score=610,
            min_rank=18000,
        )
        exact_bridge.evidence_status = SimpleNamespace(value="official")
        exact._bridges = (exact_bridge, exact_bridge)
        numeric = SimpleNamespace(
            school_name="重复大学",
            min_score=610,
            min_rank=18000,
            data_year=2026,
            supporting_years=(2026,),
            source_ids=("shared-policy-page",),
            evidence_status=SimpleNamespace(value="official"),
        )
        with self.assertRaisesRegex(SessionTransitionError, "ambiguous"):
            _validate_displayed_school_sources(numeric, (exact,))

        partial = _outcome(
            "batch:duplicate-partial",
            "batch_admission",
            usable=False,
        )
        partial_bridge = partial._bridges[0]
        partial_bridge.dataset_row = SimpleNamespace(
            year=2026,
            school_name="观察大学",
            min_score=600,
            min_rank=20000,
        )
        partial_bridge.evidence_status = SimpleNamespace(value="official")
        partial_bridge.coverage_status = SimpleNamespace(value="partial")
        partial._bridges = (partial_bridge, partial_bridge)
        observation = SimpleNamespace(
            school_name="观察大学",
            data_year=2026,
            source_ids=("shared-policy-page",),
            evidence_status=SimpleNamespace(value="partial"),
        )
        with self.assertRaisesRegex(SessionTransitionError, "ambiguous"):
            _validate_displayed_school_observation(observation, (partial,))

    def test_factory_receipt_mixed_rows_authorize_only_the_complete_bridge(self):
        student, plan = _profile_and_plan()
        task = max(
            (
                item
                for item in plan.tasks
                if item.kind == "batch_admission" and item.target_name == "普通批"
            ),
            key=lambda item: item.year,
        )
        complete = _admission_bridge(
            task,
            school_name="完整大学",
            min_score=610,
            min_rank=18000,
            fact_id="complete-row",
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        partial = _admission_bridge(
            task,
            school_name="部分大学",
            min_score=600,
            min_rank=20000,
            fact_id="partial-row",
            coverage_status=EvidenceStatus.PARTIAL,
        )
        outcome = build_task_evidence_outcome(
            student,
            plan,
            task,
            (complete, partial),
        )
        self.assertFalse(outcome.usable)
        exact_item = SimpleNamespace(
            school_name="完整大学",
            min_score=610,
            min_rank=18000,
            data_year=task.year,
            supporting_years=(task.year,),
            source_ids=("shared-admission-page",),
            evidence_status=EvidenceStatus.OFFICIAL,
        )
        forged_item = SimpleNamespace(
            **{
                **exact_item.__dict__,
                "school_name": "部分大学",
                "min_score": 600,
                "min_rank": 20000,
            }
        )

        _validate_displayed_school_sources(exact_item, (outcome,))
        with self.assertRaisesRegex(
            SessionTransitionError,
            "matching exact admission rows",
        ):
            _validate_displayed_school_sources(forged_item, (outcome,))
        partial_numeric = SimpleNamespace(
            **{
                **forged_item.__dict__,
                "evidence_status": EvidenceStatus.PARTIAL,
            }
        )
        with self.assertRaisesRegex(SessionTransitionError, "partial|exact"):
            _validate_displayed_school_sources(partial_numeric, (outcome,))

        observation = SimpleNamespace(
            school_name="部分大学",
            data_year=task.year,
            source_ids=("shared-admission-page",),
            evidence_status=EvidenceStatus.PARTIAL,
        )
        _validate_displayed_school_observation(observation, (outcome,))
        with self.assertRaisesRegex(SessionTransitionError, "ambiguous"):
            _validate_displayed_school_observation(
                observation,
                (outcome, outcome),
            )

    def test_factory_receipts_require_shared_source_for_every_supporting_year(self):
        student, plan = _profile_and_plan()
        tasks = sorted(
            (
                item
                for item in plan.tasks
                if item.kind == "batch_admission" and item.target_name == "普通批"
            ),
            key=lambda item: item.year,
            reverse=True,
        )[:2]
        outcomes = []
        for task in tasks:
            bridge = _admission_bridge(
                task,
                school_name="多年大学",
                min_score=610,
                min_rank=18000,
                fact_id=f"row-{task.year}",
                coverage_status=EvidenceStatus.OFFICIAL,
            )
            outcomes.append(
                build_task_evidence_outcome(student, plan, task, (bridge,))
            )
        item = SimpleNamespace(
            school_name="多年大学",
            min_score=610,
            min_rank=18000,
            data_year=tasks[0].year,
            supporting_years=tuple(sorted(task.year for task in tasks)),
            source_ids=("shared-admission-page",),
            evidence_status=EvidenceStatus.OFFICIAL,
        )

        _validate_displayed_school_sources(item, tuple(outcomes))
        with self.assertRaisesRegex(
            SessionTransitionError,
            "supporting years lack matching admission receipts",
        ):
            _validate_displayed_school_sources(item, tuple(outcomes[:1]))


if __name__ == "__main__":
    unittest.main()
