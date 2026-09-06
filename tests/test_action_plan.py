from __future__ import annotations

from types import SimpleNamespace
import unittest

from scripts.contracts import EvidenceStatus
from scripts.planning_profile import PlanningProfile


def profile(
    *,
    grade: str = "高二",
    concerns: tuple[str, ...] = (),
    has_materials: bool = True,
) -> PlanningProfile:
    from tests.test_planning_profile import reference_payload

    payload = reference_payload()
    payload["grade"] = grade
    payload["priorities"] = dict(payload["priorities"])
    payload["priorities"]["concerns"] = list(concerns)
    if not has_materials:
        payload["preparation_assets"] = dict(payload["preparation_assets"])
        payload["preparation_assets"]["awards"] = []
        payload["preparation_assets"]["activities"] = []
    return PlanningProfile.create(payload)


class ActionPlanTest(unittest.TestCase):
    def test_excluded_pathway_never_creates_application_or_readiness_actions(self):
        from scripts.action_plan import build_action_plan

        plan = build_action_plan(
            profile(concerns=("强基准备",)),
            rank_scenario=None,
            recommendations=(),
            pathways=(
                {
                    "policy_id": "excluded-pathway",
                    "status": "excluded",
                    "investment_decision": "不建议",
                    "timeline": ("2026-08-01",),
                    "preparation_actions": ("准备材料",),
                    "missing_constraints": ("不符合资格",),
                    "evidence_status": EvidenceStatus.OFFICIAL,
                    "source_ids": ("excluded-source",),
                },
            ),
            evidence_status=EvidenceStatus.OFFICIAL,
        )

        self.assertFalse(any(
            item.pathway_ids == ("excluded-pathway",)
            and item.reason_code in {
                "deadline_window", "qualification_blocker", "long_lead_readiness",
            }
            for item in plan
        ))

    def test_actionable_concrete_deadlines_sort_chronologically_and_plain_text_is_not_a_deadline(self):
        from scripts.action_plan import build_action_plan

        plan = build_action_plan(
            profile(),
            rank_scenario=None,
            recommendations=(),
            pathways=(
                {
                    "policy_id": "later", "status": "formal", "investment_decision": "重点准备",
                    "timeline": ("2026-09-01",), "preparation_actions": (),
                    "missing_constraints": (), "evidence_status": EvidenceStatus.OFFICIAL,
                    "source_ids": ("later-source",),
                },
                {
                    "policy_id": "earlier", "status": "formal", "investment_decision": "重点准备",
                    "timeline": ("2026-08-01",), "preparation_actions": (),
                    "missing_constraints": (), "evidence_status": EvidenceStatus.OFFICIAL,
                    "source_ids": ("earlier-source",),
                },
                {
                    "policy_id": "plain-text", "status": "formal", "investment_decision": "重点准备",
                    "timeline": ("报名说明待核验",), "preparation_actions": (),
                    "missing_constraints": (), "evidence_status": EvidenceStatus.OFFICIAL,
                    "source_ids": ("plain-source",),
                },
                {
                    "policy_id": "generic-window", "status": "formal", "investment_decision": "重点准备",
                    "timeline": ("报名窗口",), "preparation_actions": (),
                    "missing_constraints": (), "evidence_status": EvidenceStatus.OFFICIAL,
                    "source_ids": ("window-source",),
                },
                {
                    "policy_id": "before-window", "status": "formal", "investment_decision": "重点准备",
                    "timeline": ("报名前",), "preparation_actions": (),
                    "missing_constraints": (), "evidence_status": EvidenceStatus.OFFICIAL,
                    "source_ids": ("before-source",),
                },
            ),
            evidence_status=EvidenceStatus.OFFICIAL,
        )

        deadlines = [item for item in plan if item.reason_code == "deadline_window"]
        self.assertEqual(
            [(item.deadline, item.pathway_ids) for item in deadlines],
            [("2026-08-01", ("earlier",)), ("2026-09-01", ("later",))],
        )
        self.assertFalse(any(item.pathway_ids == ("plain-text",) for item in deadlines))
        self.assertFalse(any(item.pathway_ids == ("generic-window",) for item in deadlines))
        self.assertFalse(any(item.pathway_ids == ("before-window",) for item in deadlines))

    def test_pathway_actions_require_explicit_formal_actionable_decision(self):
        from scripts.action_plan import build_action_plan

        base = {
            "timeline": ("2026-08-01",),
            "preparation_actions": ("准备材料",),
            "missing_constraints": (),
            "evidence_status": EvidenceStatus.OFFICIAL,
            "source_ids": ("pathway-source",),
        }
        rejected = {
            "missing-status": {},
            "unknown-status": {"status": "unknown", "investment_decision": "重点准备"},
            "missing-decision": {"status": "formal"},
            "excluded-decision": {"status": "formal", "investment_decision": "排除"},
        }
        for policy_id, override in rejected.items():
            with self.subTest(policy_id=policy_id):
                plan = build_action_plan(
                    profile(concerns=("任意关注",)), None, (),
                    ({**base, "policy_id": policy_id, **override},),
                    EvidenceStatus.OFFICIAL,
                )
                self.assertFalse(any(
                    item.pathway_ids == (policy_id,)
                    and item.reason_code in {
                        "deadline_window", "qualification_blocker", "long_lead_readiness",
                    }
                    for item in plan
                ))

        safe_plan = build_action_plan(
            profile(concerns=("任意关注",)), None, (),
            ({**base, "policy_id": "safe", "status": "formal", "investment_decision": "重点准备"},),
            EvidenceStatus.OFFICIAL,
        )
        self.assertEqual(
            {
                item.reason_code for item in safe_plan if item.pathway_ids == ("safe",)
            },
            {"deadline_window", "long_lead_readiness"},
        )

    def test_profile_fields_change_readiness_action_without_free_text_parsing(self):
        from scripts.action_plan import build_action_plan

        pathway = {
            "policy_id": "strong-foundation", "status": "formal", "investment_decision": "重点准备",
            "timeline": (), "preparation_actions": ("准备材料",),
            "missing_constraints": (), "evidence_status": EvidenceStatus.OFFICIAL,
            "source_ids": ("policy-source",),
        }
        senior = build_action_plan(profile(grade="高三"), None, (), (pathway,), EvidenceStatus.OFFICIAL)
        junior = build_action_plan(profile(grade="高二"), None, (), (pathway,), EvidenceStatus.OFFICIAL)
        concerned = build_action_plan(
            profile(grade="高二", concerns=("任意已选关注项",)),
            None, (), (pathway,), EvidenceStatus.OFFICIAL,
        )
        no_materials = build_action_plan(
            profile(grade="高二", has_materials=False),
            None, (), (pathway,), EvidenceStatus.OFFICIAL,
        )
        senior_readiness = next(item for item in senior if item.reason_code == "long_lead_readiness")
        junior_readiness = next(item for item in junior if item.reason_code == "long_lead_readiness")
        concerned_readiness = next(item for item in concerned if item.reason_code == "long_lead_readiness")
        no_materials_readiness = next(item for item in no_materials if item.reason_code == "long_lead_readiness")
        self.assertNotEqual(
            (senior_readiness.phase, senior_readiness.reason, senior_readiness.completion_criteria),
            (junior_readiness.phase, junior_readiness.reason, junior_readiness.completion_criteria),
        )
        self.assertNotEqual(junior_readiness.reason, concerned_readiness.reason)
        self.assertNotEqual(
            junior_readiness.completion_criteria,
            no_materials_readiness.completion_criteria,
        )

    def test_missing_rank_review_never_inherits_overall_official_status(self):
        from scripts.action_plan import build_action_plan

        plan = build_action_plan(
            profile(),
            SimpleNamespace(status=EvidenceStatus.MISSING, source_ids=("untrusted-rank",)),
            (), (), EvidenceStatus.OFFICIAL,
        )
        review = next(item for item in plan if item.reason_code == "rank_context_review")
        self.assertEqual(review.evidence_status, EvidenceStatus.MISSING)
        self.assertEqual(review.source_ids, ())

    def test_deadline_factory_rejects_uncontrolled_text(self):
        from scripts.action_plan import ActionItem

        values = {
            "action_id": "deadline-check", "title": "deadline check",
            "completion_criteria": ("done",), "phase": "现在",
            "deadline": "报名截止待确认", "urgency": "normal",
            "strategic_value": "medium", "effort": "low", "blocking": False,
            "depends_on": (), "school_ids": (), "pathway_ids": (),
            "reason_code": "deadline_check", "reason": "reason",
            "consequence": "consequence", "evidence_status": EvidenceStatus.MISSING,
            "source_ids": (),
        }
        for unsupported in ("报名截止待确认", "报名窗口", "报名前"):
            with self.subTest(deadline=unsupported):
                with self.assertRaisesRegex(ValueError, "deadline"):
                    ActionItem.create(**{**values, "deadline": unsupported})

    def test_pending_prerequisites_precede_followups_without_ordinary_coupling(self):
        from dataclasses import replace

        from scripts.action_plan import ActionItem, build_action_plan
        from scripts.path_recommend import evaluate_pathways
        from tests.test_generate_report_evidence import (
            _legacy_pathway_policy,
            _legacy_pathway_profile,
            pathway_rank_scenario,
        )

        pending = evaluate_pathways(
            _legacy_pathway_profile(),
            (replace(
                _legacy_pathway_policy(formal=False),
                timeline=("2026-08-01",),
            ),),
            rank_scenario=pathway_rank_scenario(),
        ).items[0]

        plan = build_action_plan(
            profile(concerns=("强基准备",)),
            rank_scenario=None,
            recommendations=(),
            pathways=(pending,),
            evidence_status=EvidenceStatus.PARTIAL,
        )

        self.assertTrue(all(isinstance(item, ActionItem) for item in plan))
        ordinary_gap = next(
            item for item in plan if item.reason_code == "evidence_gap_review"
        )
        pathway_gap = next(
            item for item in plan if item.reason_code == "pathway_evidence_review"
        )
        qualification = next(
            item for item in plan if item.reason_code == "qualification_blocker"
        )
        deadline = next(item for item in plan if item.reason_code == "deadline_window")
        readiness = next(item for item in plan if item.reason_code == "long_lead_readiness")
        self.assertEqual(qualification.depends_on, (pathway_gap.action_id,))
        self.assertEqual(deadline.depends_on, (qualification.action_id,))
        self.assertEqual(readiness.depends_on, (qualification.action_id,))
        self.assertNotIn(ordinary_gap.action_id, qualification.depends_on)
        self.assertNotIn(ordinary_gap.action_id, deadline.depends_on)
        self.assertNotIn(ordinary_gap.action_id, readiness.depends_on)
        self.assertLess(
            next(index for index, item in enumerate(plan) if item.reason_code == "long_lead_readiness"),
            next(index for index, item in enumerate(plan) if item.reason_code == "final_official_review"),
        )

    def test_partial_ordinary_batch_never_blocks_an_official_pathway(self):
        """Catches using ordinary-batch evidence as a pathway prerequisite."""

        from scripts.action_plan import build_action_plan
        from tests.test_generate_report_evidence import formal_pathway_result

        formal = formal_pathway_result().items[0]
        plan = build_action_plan(
            profile(concerns=("专项准备",)),
            rank_scenario=SimpleNamespace(status=EvidenceStatus.OFFICIAL),
            recommendations=(),
            pathways=(formal,),
            evidence_status=EvidenceStatus.PARTIAL,
        )

        self.assertTrue(any(item.action_id == "evidence-gap-review" for item in plan))
        pathway_actions = tuple(
            item for item in plan if item.pathway_ids == (formal.policy_id,)
        )
        self.assertTrue(pathway_actions)
        self.assertTrue(all(
            "evidence-gap-review" not in item.depends_on for item in pathway_actions
        ))

    def test_real_pending_pathway_builds_evidence_qualification_and_followup_chain(self):
        """Catches dropping a real pending PathwayItem/ReportPathway from actions."""

        from scripts.action_plan import build_action_plan
        from scripts.report_model import build_report_model
        from tests.test_generate_report_evidence import (
            evidence_snapshot,
            pathway_result,
            rank_estimate,
            recommendations,
            student,
        )

        pending_result = pathway_result()
        pending = pending_result.items[0]
        direct = build_action_plan(
            profile(concerns=("专项准备",)),
            rank_scenario=SimpleNamespace(status=EvidenceStatus.OFFICIAL),
            recommendations=(),
            pathways=(pending,),
            evidence_status=EvidenceStatus.OFFICIAL,
        )
        report = build_report_model(
            student(), recommendations(), rank_estimate(), pending_result,
            evidence_snapshot(),
        )
        self.assertEqual(report.pathways[0].status, "pending_verification")
        self.assertIsNone(report.pathways[0].target_rank)

        expected_ids = {
            f"pathway-evidence-review:{pending.policy_id}",
            f"qualification-blocker:{pending.policy_id}",
            f"long-lead-readiness:{pending.policy_id}",
        }
        for plan in (direct, report.action_items):
            with self.subTest(plan_type=type(plan[0]).__name__):
                by_id = {item.action_id: item for item in plan}
                self.assertTrue(expected_ids <= by_id.keys())
                evidence = by_id[f"pathway-evidence-review:{pending.policy_id}"]
                qualification = by_id[f"qualification-blocker:{pending.policy_id}"]
                readiness = by_id[f"long-lead-readiness:{pending.policy_id}"]
                self.assertEqual(evidence.evidence_status, EvidenceStatus.CONFLICT)
                self.assertEqual(qualification.depends_on, (evidence.action_id,))
                self.assertEqual(readiness.depends_on, (qualification.action_id,))
                self.assertEqual(readiness.pathway_ids, (pending.policy_id,))

    def test_pending_pathway_never_blocks_another_pathways_readiness(self):
        """Catches merging per-path readiness into one cross-path dependency chain."""

        from scripts.action_plan import build_action_plan
        from tests.test_generate_report_evidence import (
            formal_pathway_result,
            pathway_result,
        )

        formal = formal_pathway_result().items[0]
        pending = pathway_result().items[0]
        plan = build_action_plan(
            profile(concerns=("专项准备",)),
            rank_scenario=SimpleNamespace(status=EvidenceStatus.OFFICIAL),
            recommendations=(),
            pathways=(formal, pending),
            evidence_status=EvidenceStatus.OFFICIAL,
        )
        by_id = {item.action_id: item for item in plan}

        formal_readiness = by_id[f"long-lead-readiness:{formal.policy_id}"]
        pending_readiness = by_id[f"long-lead-readiness:{pending.policy_id}"]
        self.assertEqual(formal_readiness.depends_on, ())
        self.assertEqual(
            pending_readiness.depends_on,
            (f"qualification-blocker:{pending.policy_id}",),
        )

    def test_deadlines_then_phase_effort_and_stable_ties_are_deterministic(self):
        """Catches value or urgency being used as an undocumented weighted score."""

        from scripts.action_plan import ActionItem, order_actions

        def action(
            action_id,
            *,
            phase="现在",
            deadline=None,
            strategic_value="medium",
            urgency="normal",
            blocking=False,
        ):
            return ActionItem.create(
                action_id=action_id,
                title=action_id,
                completion_criteria=("done",),
                phase=phase,
                deadline=deadline,
                urgency=urgency,
                strategic_value=strategic_value,
                effort="low",
                blocking=blocking,
                depends_on=(),
                school_ids=(),
                pathway_ids=(),
                reason_code="ordering_probe",
                reason="reason",
                consequence="consequence",
                evidence_status=EvidenceStatus.OFFICIAL,
                source_ids=("source",),
            )

        unordered = (
            action("semester-high", phase="本学期", strategic_value="high", urgency="urgent"),
            action("now-low", strategic_value="low", urgency="urgent"),
            action("now-high-normal", strategic_value="high", urgency="normal"),
            action("now-high-urgent-b", strategic_value="high", urgency="urgent"),
            action("now-high-urgent-a", strategic_value="high", urgency="urgent"),
            action("now-later", deadline="2026-09-01", strategic_value="low", urgency="low"),
            action("now-earlier", deadline="2026-08-01", strategic_value="low", urgency="low"),
        )

        expected = (
            "now-earlier",
            "now-later",
            "now-high-normal",
            "now-high-urgent-a",
            "now-high-urgent-b",
            "now-low",
            "semester-high",
        )
        self.assertEqual(
            tuple(item.action_id for item in order_actions(unordered)),
            expected,
        )
        self.assertEqual(
            tuple(item.action_id for item in order_actions(reversed(unordered))),
            expected,
        )

    def test_ready_hard_deadline_is_global_but_never_bypasses_a_dependency(self):
        """Catches an undated current-phase task hiding a ready hard deadline."""

        from scripts.action_plan import ActionItem, order_actions

        def action(action_id, *, phase="现在", deadline=None, depends_on=()):
            return ActionItem.create(
                action_id=action_id,
                title=action_id,
                completion_criteria=("done",),
                phase=phase,
                deadline=deadline,
                urgency="high",
                strategic_value="high",
                effort="low",
                blocking=False,
                depends_on=depends_on,
                school_ids=(),
                pathway_ids=(),
                reason_code="deadline_ordering_probe",
                reason="reason",
                consequence="consequence",
                evidence_status=EvidenceStatus.OFFICIAL,
                source_ids=("source",),
            )

        ordered = order_actions((
            action("now-undated"),
            action("later-phase-ready", phase="报名前", deadline="2026-08-02"),
            action("deadline-prerequisite"),
            action(
                "earlier-but-blocked",
                phase="报名前",
                deadline="2026-08-01",
                depends_on=("deadline-prerequisite",),
            ),
        ))

        self.assertEqual(
            tuple(item.action_id for item in ordered),
            (
                "later-phase-ready",
                "deadline-prerequisite",
                "earlier-but-blocked",
                "now-undated",
            ),
        )

    def test_undated_priority_is_blocker_long_lead_uncertainty_then_phase(self):
        """Catches phase/value sorting ahead of the documented sequential rules."""

        from scripts.action_plan import ActionItem, order_actions

        def action(
            action_id,
            *,
            phase="现在",
            strategic_value="medium",
            effort="low",
            blocking=False,
            reason_code="ordinary_followup",
        ):
            return ActionItem.create(
                action_id=action_id,
                title=action_id,
                completion_criteria=("done",),
                phase=phase,
                deadline=None,
                urgency="normal",
                strategic_value=strategic_value,
                effort=effort,
                blocking=blocking,
                depends_on=(),
                school_ids=(),
                pathway_ids=(),
                reason_code=reason_code,
                reason="reason",
                consequence="consequence",
                evidence_status=EvidenceStatus.PARTIAL,
                source_ids=("source",),
            )

        ordered = order_actions((
            action("ordinary-now-high", strategic_value="high"),
            action(
                "blocker-now-medium",
                strategic_value="medium",
                blocking=True,
            ),
            action(
                "blocker-next-low",
                phase="下一阶段",
                strategic_value="low",
                blocking=True,
            ),
            action(
                "long-lead-next",
                phase="下一阶段",
                strategic_value="high",
                effort="high",
                reason_code="long_lead_readiness",
            ),
            action(
                "rank-gap-now",
                effort="high",
                reason_code="rank_context_review",
            ),
            action(
                "evidence-gap-next",
                phase="下一阶段",
                reason_code="evidence_gap_review",
            ),
            action(
                "pathway-gap-next",
                phase="下一阶段",
                effort="medium",
                reason_code="pathway_evidence_review",
            ),
        ))

        self.assertEqual(
            tuple(item.action_id for item in ordered),
            (
                "blocker-now-medium",
                "blocker-next-low",
                "long-lead-next",
                "rank-gap-now",
                "evidence-gap-next",
                "pathway-gap-next",
                "ordinary-now-high",
            ),
        )

    def test_sequential_priority_never_bypasses_an_undated_dependency(self):
        """Catches a newly-ready blocker appearing before its prerequisite."""

        from scripts.action_plan import ActionItem, order_actions

        def action(action_id, *, blocking=False, depends_on=()):
            return ActionItem.create(
                action_id=action_id,
                title=action_id,
                completion_criteria=("done",),
                phase="现在",
                deadline=None,
                urgency="normal",
                strategic_value="medium",
                effort="low",
                blocking=blocking,
                depends_on=depends_on,
                school_ids=(),
                pathway_ids=(),
                reason_code="dependency_ordering_probe",
                reason="reason",
                consequence="consequence",
                evidence_status=EvidenceStatus.OFFICIAL,
                source_ids=("source",),
            )

        ordered = order_actions((
            action("a-prerequisite"),
            action("blocked-followup", blocking=True, depends_on=("a-prerequisite",)),
            action("z-independent"),
        ))
        self.assertEqual(
            tuple(item.action_id for item in ordered),
            ("a-prerequisite", "blocked-followup", "z-independent"),
        )

    def test_max_length_policy_ids_produce_bounded_stable_unique_action_ids(self):
        """Catches prefixing a legal 128-character policy ID past ActionItem limits."""

        from scripts.action_plan import build_action_plan

        def pathway(policy_id, *, pending=False):
            return {
                "policy_id": policy_id,
                "status": "pending_verification" if pending else "formal",
                "timeline": ("2026-08-01",),
                "preparation_actions": ("准备材料",),
                "missing_constraints": (("资格待核验",) if pending else ()),
                "investment_decision": "观察" if pending else "重点准备",
                "evidence_status": (
                    EvidenceStatus.PARTIAL if pending else EvidenceStatus.OFFICIAL
                ),
                "source_ids": (f"source-{policy_id[-1]}",),
            }

        long_a = "p" * 127 + "a"
        long_b = "p" * 127 + "b"
        long_c = "p" * 127 + "c"
        first = build_action_plan(
            profile(),
            SimpleNamespace(status=EvidenceStatus.OFFICIAL),
            (),
            (pathway(long_a), pathway(long_b), pathway(long_c, pending=True)),
            EvidenceStatus.OFFICIAL,
        )
        second = build_action_plan(
            profile(),
            SimpleNamespace(status=EvidenceStatus.OFFICIAL),
            (),
            (pathway(long_a), pathway(long_b), pathway(long_c, pending=True)),
            EvidenceStatus.OFFICIAL,
        )

        generated = tuple(
            item for item in first if item.pathway_ids in {
                (long_a,), (long_b,), (long_c,),
            }
        )
        self.assertEqual(len(generated), 8)
        self.assertTrue(all(len(item.action_id) <= 128 for item in generated))
        self.assertEqual(
            len({item.action_id for item in generated}),
            len(generated),
        )
        self.assertEqual(
            tuple(item.action_id for item in first),
            tuple(item.action_id for item in second),
        )

        short = build_action_plan(
            profile(),
            SimpleNamespace(status=EvidenceStatus.OFFICIAL),
            (),
            (pathway("short"),),
            EvidenceStatus.OFFICIAL,
        )
        self.assertIn(
            "long-lead-readiness:short",
            {item.action_id for item in short},
        )

    def test_generated_dependencies_are_internal_and_topologically_ordered(self):
        from scripts.action_plan import build_action_plan
        from tests.test_generate_report_evidence import pathway_result

        plan = build_action_plan(
            profile(concerns=("强基准备",)),
            rank_scenario=SimpleNamespace(
                status=EvidenceStatus.PARTIAL,
                source_ids=("rank-source",),
            ),
            recommendations=(SimpleNamespace(school_name="示例大学"),),
            pathways=pathway_result().items,
            evidence_status=EvidenceStatus.PARTIAL,
        )

        positions = {item.action_id: index for index, item in enumerate(plan)}
        self.assertEqual(len(positions), len(plan))
        for item in plan:
            with self.subTest(action_id=item.action_id):
                self.assertTrue(set(item.depends_on) <= positions.keys())
                self.assertTrue(
                    all(positions[dependency] < positions[item.action_id]
                        for dependency in item.depends_on)
                )

    def test_items_are_immutable_json_safe_and_keep_per_path_provenance(self):
        from scripts.action_plan import build_action_plan

        plan = build_action_plan(
            profile(concerns=("强基准备",)),
            rank_scenario=None,
            recommendations=(),
            pathways=(
                {
                    "policy_id": "strong-foundation",
                    "status": "formal",
                    "timeline": ("本学期准备材料",),
                    "preparation_actions": ("整理成绩与活动材料",),
                    "missing_constraints": (),
                    "investment_decision": "重点准备",
                    "evidence_status": EvidenceStatus.REFERENCE,
                    "source_ids": ("policy-a", "policy-b", "policy-c"),
                },
                {
                    "policy_id": "strong-foundation-alt",
                    "status": "formal",
                    "timeline": ("本学期准备材料",),
                    "preparation_actions": ("整理成绩与活动材料",),
                    "missing_constraints": (),
                    "investment_decision": "重点准备",
                    "evidence_status": EvidenceStatus.REFERENCE,
                    "source_ids": ("policy-c", "policy-d", "policy-e"),
                },
            ),
            evidence_status=EvidenceStatus.PARTIAL,
        )

        readiness = {
            item.pathway_ids[0]: item
            for item in plan if item.reason_code == "long_lead_readiness"
        }
        self.assertEqual(set(readiness), {"strong-foundation", "strong-foundation-alt"})
        self.assertEqual(readiness["strong-foundation"].phase, "本学期")
        self.assertTrue(readiness["strong-foundation"].completion_criteria)
        self.assertEqual(
            readiness["strong-foundation"].source_ids,
            ("policy-a", "policy-b", "policy-c"),
        )
        self.assertEqual(
            readiness["strong-foundation-alt"].source_ids,
            ("policy-c", "policy-d", "policy-e"),
        )
        item = readiness["strong-foundation"]
        self.assertEqual(item.to_dict()["pathway_ids"], list(item.pathway_ids))
        with self.assertRaises((AttributeError, TypeError)):
            item.title = "changed"

    def test_dependency_cycles_fail_closed_and_unknown_deadlines_are_honest(self):
        from scripts.action_plan import ActionItem, build_action_plan, order_actions

        first = ActionItem.create(
            action_id="first",
            title="first",
            completion_criteria=("done",),
            phase="现在",
            deadline=None,
            urgency="normal",
            strategic_value="medium",
            effort="low",
            blocking=False,
            depends_on=("second",),
            school_ids=(),
            pathway_ids=(),
            reason_code="generic_review",
            reason="reason",
            consequence="consequence",
            evidence_status=EvidenceStatus.MISSING,
            source_ids=(),
        )
        second = ActionItem.create(
            **{**first.to_dict(), "action_id": "second", "depends_on": ["first"]}
        )
        with self.assertRaisesRegex(ValueError, "cycle"):
            order_actions((first, second))

        plan = build_action_plan(
            profile(), rank_scenario=None, recommendations=(), pathways=(),
            evidence_status=EvidenceStatus.MISSING,
        )
        unknown = next(item for item in plan if item.reason_code == "evidence_gap_review")
        self.assertIsNone(unknown.deadline)
        self.assertIn("未提供", unknown.reason)

    def test_report_model_projects_priority_and_phase_timeline_from_actions(self):
        from scripts.report_model import build_report_model
        from tests.test_generate_report_evidence import (
            evidence_snapshot,
            formal_pathway_result,
            rank_estimate,
            recommendations,
            student,
        )
        report_profile = student()
        payload = profile(concerns=("强基准备",)).to_dict()
        payload.pop("mode")
        payload.pop("digest")
        payload.update(
            {
                "province": report_profile.province,
                "subject_mode": report_profile.subject_mode,
                "subject_group": report_profile.subject_group,
                "secondary_subjects": list(report_profile.secondary_subjects),
                "grade": report_profile.grade,
                "rank_observations": [],
                "best_rank": None,
                "usual_rank": None,
            }
        )

        report = build_report_model(
            report_profile,
            recommendations(),
            rank_estimate(),
            formal_pathway_result(),
            evidence_snapshot(),
            planning_profile=PlanningProfile.create(payload),
        )

        self.assertGreaterEqual(len(report.priority_actions), 3)
        self.assertLessEqual(len(report.priority_actions), 7)
        self.assertEqual(
            tuple(item.action_id for item in report.priority_actions),
            tuple(item.action_id for item in report.action_items[: len(report.priority_actions)]),
        )
        self.assertTrue(report.action_timeline)
        self.assertEqual(report.to_dict()["action_items"][0]["action_id"], report.action_items[0].action_id)
