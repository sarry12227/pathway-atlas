from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest

from scripts.adapters import CellStatus, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceStatus,
    SourceCandidate,
    SourceTier,
)
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.evidence import EvidenceStore
from scripts.planning_profile import PlanningProfile, load_planning_profile
from scripts.query_plan import QueryPlan, build_query_plan, load_province_catalog
from scripts.validate_evidence import validate_bundle_snapshot


POLICY_FIELDS = (
    "institution",
    "province",
    "subject_mode",
    "year",
    "eligibility_requirements",
    "grade_requirements",
    "subject_requirements",
    "award_requirements",
    "activity_requirements",
    "disqualifying_facts",
    "professional_options",
    "training_arrangements",
    "transition_rules",
    "outcomes",
    "service_employment_obligations",
    "penalty_exit_rules",
    "fees_and_subsidies",
    "dates_and_deadlines",
    "application_materials",
    "preparation_actions",
)


def profile() -> PlanningProfile:
    return load_planning_profile(
        {
            "schema_version": "2.0",
            "gender": "不便回答",
            "province": "湖北",
            "city": "武汉",
            "high_school": "武汉市示例中学",
            "grade": "高二",
            "exam_year": 2028,
            "class_level": "重点班",
            "subject_mode": "3+1+2",
            "subject_group": "历史",
            "secondary_subjects": ["地理", "政治"],
            "score_basis": "原始分",
            "rank_observations": [
                {
                    "exam_date": "2027-06-01",
                    "scope": "school",
                    "score": 610,
                    "max_score": 750,
                    "rank": 120,
                    "cohort_size": 1000,
                }
            ],
            "best_rank": 80,
            "usual_rank": 140,
            "awards": ["示例学科奖项"],
            "activities": ["示例研究活动"],
            "target_schools": [],
            "target_school_reasons": [],
            "target_majors": ["历史学"],
            "target_major_reasons": ["长期兴趣"],
            "target_regions": ["武汉"],
            "excluded_regions": [],
            "future_plan": "继续深造",
            "concerns": ["院校定位"],
            "desired_outcomes": ["院校范围", "多元路径"],
            "eligibility_facts": ["完成高考报名"],
        }
    )


def plan(student: PlanningProfile) -> QueryPlan:
    return build_query_plan(
        student,
        load_province_catalog(),
        DecisionPolicySnapshot.load_default(),
    )


def task_for(query_plan: QueryPlan, target: str = "强基计划", year: int | None = None):
    selected_year = query_plan.research_year if year is None else year
    return next(
        item
        for item in query_plan.tasks
        if item.target_name == target and item.year == selected_year
    )


def candidate(
    source_id: str = "official-pathway",
    *,
    tier: SourceTier = SourceTier.A,
    publisher: str = "示例公开发布机关",
    host: str = "policy.example.cn",
    citation_root: str | None = None,
) -> SourceCandidate:
    return SourceCandidate(
        source_id=source_id,
        url=f"https://{host}/{source_id}.html",
        publisher=publisher,
        tier=tier,
        published_at="2026-06-01",
        retrieved_at="2026-06-02T00:00:00Z",
        content_hash="sha256:" + hashlib.sha256(source_id.encode()).hexdigest(),
        citation_root=citation_root or f"https://{host}/",
        summary="合成路径政策资料",
    )


def policy_values(*, year: int = 2026, **changes) -> dict[str, object]:
    values: dict[str, object] = {
        "institution": "示例高校",
        "province": "湖北",
        "subject_mode": "3+1+2",
        "year": year,
        "eligibility_requirements": "完成高考报名",
        "grade_requirements": "高三在读",
        "subject_requirements": "符合公开选科要求",
        "award_requirements": "公开简章未要求奖项",
        "activity_requirements": "公开简章未要求特定活动",
        "disqualifying_facts": "不符合公开报名条件",
        "professional_options": "示例专业",
        "training_arrangements": "校内培养",
        "transition_rules": "按公开规则考核转段",
        "outcomes": "完成培养后按公开规则毕业",
        "service_employment_obligations": "无额外服务期",
        "penalty_exit_rules": "可按公开规则退出",
        "fees_and_subsidies": "按公开标准执行",
        "dates_and_deadlines": "报名前复核当年简章",
        "application_materials": "整理公开要求的申请材料",
        "preparation_actions": "跟踪官方报名通知",
    }
    values.update(changes)
    return values


def policy_table(
    *,
    year: int = 2026,
    extraction_method: str = "html-table",
    statuses: dict[str, CellStatus] | None = None,
    **changes,
) -> ExtractedTable:
    values = policy_values(year=year, **changes)
    cell_status = {name: CellStatus.EXACT for name in values}
    cell_status.update(statuses or {})
    row = ExtractedRow(
        values=values,
        cell_status=cell_status,
        location="table[1]/tbody/tr[1]",
        confidence=1,
    )
    return ExtractedTable(
        table_id="table[1]",
        caption="合成路径政策",
        sheet=None,
        rows=(row,),
        coverage=ExtractedCoverage(),
        warnings=(),
        extraction_method=extraction_method,
    )


def project(
    *,
    student: PlanningProfile | None = None,
    query_plan: QueryPlan | None = None,
    task=None,
    extraction=None,
    field_map=None,
    candidates=None,
):
    from scripts.adapters.pathway_extraction import extract_pathway_policy

    student = student or profile()
    query_plan = query_plan or plan(student)
    task = task or task_for(query_plan)
    return extract_pathway_policy(
        profile=student,
        plan=query_plan,
        task=task,
        extraction=extraction or policy_table(year=task.year),
        field_map=field_map or {name: name for name in POLICY_FIELDS},
        candidates=candidates or (candidate(),),
    )


def replay_arguments(projection) -> dict[str, object]:
    return {
        "province": projection.province,
        "subject_mode": projection.subject_mode,
        "target_year": projection.target_year,
        "expected_profile_digest": projection.profile_digest,
        "expected_query_plan_digest": projection.query_plan_digest,
    }


def persist_projection(projection, *sources: SourceCandidate):
    temporary, bridge, validation, _bundle = persist_projection_bundle(
        projection, *sources
    )
    return temporary, bridge, validation


def persist_projection_bundle(projection, *sources: SourceCandidate):
    from scripts.adapters.pathway_bridge import bridge_pathway_policy_evidence

    bridge = bridge_pathway_policy_evidence(projection)
    temporary = tempfile.TemporaryDirectory()
    store = EvidenceStore.create(
        Path(temporary.name).resolve(),
        CapabilityReport(CapabilityTier.OFFLINE),
    )
    for source in sources:
        store.add_candidate(source)
    bridge.persist(store)
    store.finalize()
    validation = validate_bundle_snapshot(store.session_path)
    return temporary, bridge, validation, store.session_path


def rewrite_bundle_manifest(bundle: Path) -> None:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    capability = json.loads((bundle / "capability.json").read_text(encoding="utf-8"))
    rejections = (bundle / "rejections.jsonl").read_text(encoding="utf-8").splitlines()
    store = object.__new__(EvidenceStore)
    store._capability = capability
    store._rejections = {str(index): None for index in range(len(rejections))}
    records = {
        name: (bundle / name).read_text(encoding="utf-8")
        for name in (
            "capability.json",
            "candidates.jsonl",
            "context.jsonl",
            "normalized/facts.jsonl",
            "rejections.jsonl",
        )
    }
    manifest["manifest_hash"] = EvidenceStore._manifest_hash(store, records)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


class PathwayEvidenceBridgeTest(unittest.TestCase):
    def test_active_pathway_tasks_project_missing_observations_without_fake_sources(self):
        from scripts.adapters.pathway_bridge import bridge_pathway_observations

        student = profile()
        query_plan = plan(student)
        observations = bridge_pathway_observations(
            (),
            profile=student,
            plan=query_plan,
        )

        expected_titles = {
            task.target_name
            for task in query_plan.tasks
            if task.target_name is not None
            and task.kind in {
                "strong_foundation",
                "comprehensive_evaluation",
                "hk_macao_admission",
                "special_pathway",
            }
        }
        self.assertEqual({item.title for item in observations}, expected_titles)
        self.assertTrue(observations)
        for item in observations:
            with self.subTest(title=item.title):
                self.assertIs(item.evidence_status, EvidenceStatus.MISSING)
                self.assertEqual(item.source_ids, ())
                self.assertTrue(item.missing_constraints)
                self.assertTrue(item.preparation_actions)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = EvidenceStore.create(
            Path(temporary.name).resolve(),
            CapabilityReport(CapabilityTier.OFFLINE),
        )
        store.finalize()
        replayed = bridge_pathway_observations(
            store.session_path,
            profile=student,
            plan=query_plan,
        )
        self.assertEqual(
            tuple(item.to_dict() for item in replayed),
            tuple(item.to_dict() for item in observations),
        )

    def test_conflict_observation_keeps_real_sources_and_accepted_policy_wins(self):
        from scripts.adapters.pathway_bridge import (
            bridge_pathway_observations,
            bridge_pathway_policy_evidence,
        )

        student = profile()
        query_plan = plan(student)
        sources = (
            candidate(
                "conflict-b1",
                tier=SourceTier.B,
                publisher="冲突乙一",
                host="conflict-b1.example.cn",
            ),
            candidate(
                "conflict-b2",
                tier=SourceTier.B,
                publisher="冲突乙二",
                host="conflict-b2.example.cn",
            ),
        )
        conflict = project(
            student=student,
            query_plan=query_plan,
            extraction=(
                policy_table(),
                policy_table(professional_options="相互冲突的专业范围"),
            ),
            field_map=tuple(
                {name: name for name in POLICY_FIELDS} for _ in sources
            ),
            candidates=sources,
        )
        observations = bridge_pathway_observations(
            (bridge_pathway_policy_evidence(conflict),),
            profile=student,
            plan=query_plan,
        )
        strong_foundation = next(
            item for item in observations if item.title == "强基计划"
        )
        self.assertIs(strong_foundation.evidence_status, EvidenceStatus.CONFLICT)
        self.assertEqual(strong_foundation.source_ids, ("conflict-b1", "conflict-b2"))
        self.assertTrue(
            any("冲突" in constraint for constraint in strong_foundation.missing_constraints)
        )
        temporary, _bridge, validation, bundle = persist_projection_bundle(
            conflict, *sources
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(validation.issues, ())
        replayed = bridge_pathway_observations(
            bundle,
            profile=student,
            plan=query_plan,
        )
        replayed_strong_foundation = next(
            item for item in replayed if item.title == "强基计划"
        )
        self.assertEqual(
            replayed_strong_foundation.to_dict(), strong_foundation.to_dict()
        )

        accepted = project(student=student, query_plan=query_plan)
        accepted_observations = bridge_pathway_observations(
            (bridge_pathway_policy_evidence(accepted),),
            profile=student,
            plan=query_plan,
        )
        self.assertNotIn("强基计划", {item.title for item in accepted_observations})

    def test_excluded_pathway_never_projects_an_observation(self):
        from scripts.adapters.pathway_bridge import bridge_pathway_observations

        value = profile().to_dict()
        value.pop("mode")
        value.pop("digest")
        value["pathway_preferences"]["strong_foundation"] = "not_interested"
        student = load_planning_profile(value)
        query_plan = plan(student)

        observations = bridge_pathway_observations(
            (),
            profile=student,
            plan=query_plan,
        )

        self.assertNotIn("强基计划", {item.title for item in observations})

    def test_full_evaluation_replays_projection_before_using_policy_values(self):
        from scripts.adapters.pathway_bridge import (
            bridge_pathway_policies,
            bridge_pathway_policy_evidence,
        )
        from scripts.path_recommend import evaluate_pathways
        from tests.test_school_recommend_generic import exact_rank

        student = profile()
        query_plan = plan(student)
        projection = project(student=student, query_plan=query_plan)
        policy = bridge_pathway_policies(
            (bridge_pathway_policy_evidence(projection),),
            **replay_arguments(projection),
        )[0]
        forged = replace(policy, professional_options=("FAKE-MAJOR",))

        with self.assertRaisesRegex(ValueError, "authenticated projection"):
            evaluate_pathways(
                student,
                (forged,),
                rank_scenario=exact_rank(),
                query_plan=query_plan,
            )

    def test_full_evaluation_rejects_a_manually_rebuilt_policy_with_stolen_trails(self):
        from scripts.adapters.pathway_bridge import (
            bridge_pathway_policies,
            bridge_pathway_policy_evidence,
        )
        from scripts.path_recommend import PathwayPolicy, evaluate_pathways
        from tests.test_school_recommend_generic import exact_rank

        student = profile()
        query_plan = plan(student)
        projection = project(student=student, query_plan=query_plan)
        authenticated = bridge_pathway_policies(
            (bridge_pathway_policy_evidence(projection),),
            **replay_arguments(projection),
        )[0]
        manual = PathwayPolicy(
            policy_id=authenticated.policy_id,
            pathway_type=authenticated.pathway_type,
            title=authenticated.title,
            institution=authenticated.institution,
            province=authenticated.province,
            subject_mode=authenticated.subject_mode,
            valid_year=authenticated.valid_year,
            eligibility_requirements=authenticated.eligibility_requirements,
            disqualifying_facts=authenticated.disqualifying_facts,
            professional_options=authenticated.professional_options,
            training_arrangements=authenticated.training_arrangements,
            transition_rules=authenticated.transition_rules,
            outcomes=authenticated.outcomes,
            service_employment_obligations=authenticated.service_employment_obligations,
            penalty_exit_rules=authenticated.penalty_exit_rules,
            fees_and_subsidies=authenticated.fees_and_subsidies,
            policy_source_ids=authenticated.policy_source_ids,
            evidence_status=authenticated.evidence_status,
            calculation_basis=authenticated.calculation_basis,
            target_year=authenticated.target_year,
            data_year=authenticated.data_year,
            fallback_distance=authenticated.fallback_distance,
            year_basis=authenticated.year_basis,
            timeline=authenticated.timeline,
            preparation_actions=authenticated.preparation_actions,
            grade_requirements=authenticated.grade_requirements,
            subject_requirements=authenticated.subject_requirements,
            award_requirements=authenticated.award_requirements,
            activity_requirements=authenticated.activity_requirements,
            application_materials=authenticated.application_materials,
            profile_digest=authenticated.profile_digest,
            query_plan_digest=authenticated.query_plan_digest,
            field_evidence=authenticated.field_evidence,
        )

        with self.assertRaisesRegex(ValueError, "authenticated projection"):
            evaluate_pathways(
                student,
                (manual,),
                rank_scenario=exact_rank(),
                query_plan=query_plan,
            )

    def test_domain_policy_carries_every_authenticated_profile_decision_field(self):
        from scripts.adapters.pathway_bridge import (
            bridge_pathway_policies,
            bridge_pathway_policy_evidence,
        )

        projection = project()
        policy = bridge_pathway_policies(
            (bridge_pathway_policy_evidence(projection),),
            **replay_arguments(projection),
        )[0]

        self.assertEqual(policy.grade_requirements, projection.grade_requirements)
        self.assertEqual(policy.subject_requirements, projection.subject_requirements)
        self.assertEqual(policy.award_requirements, projection.award_requirements)
        self.assertEqual(policy.activity_requirements, projection.activity_requirements)
        self.assertEqual(policy.application_materials, projection.application_materials)
        trails = {item.field: item for item in policy.field_evidence}
        self.assertEqual(
            set(trails),
            {
                "institution", "province", "subject_mode", "data_year",
                "eligibility_requirements", "grade_requirements",
                "subject_requirements", "award_requirements",
                "activity_requirements", "disqualifying_facts",
                "professional_options", "training_arrangements",
                "transition_rules", "outcomes",
                "service_employment_obligations", "penalty_exit_rules",
                "fees_and_subsidies", "timeline",
                "application_materials", "preparation_actions",
            },
        )
        self.assertEqual(trails["data_year"].upstream_fields, ("year",))
        self.assertEqual(
            trails["timeline"].upstream_fields,
            ("dates_and_deadlines",),
        )
        for field, trail in trails.items():
            with self.subTest(field=field):
                self.assertTrue(trail.source_ids)
                self.assertTrue(trail.locators)
                self.assertTrue(trail.extraction_methods)
                self.assertTrue(trail.evidence_method)

    def test_persisted_consumer_accepts_only_a_fresh_bundle_path(self):
        from scripts.adapters.pathway_bridge import bridge_pathway_policies

        source = candidate()
        projection = project(candidates=(source,))
        temporary, _bridge, validation, bundle = persist_projection_bundle(
            projection, source
        )
        self.addCleanup(temporary.cleanup)

        with self.assertRaises(TypeError):
            bridge_pathway_policies(
                validation.snapshot,
                **replay_arguments(projection),
            )
        policies = bridge_pathway_policies(
            bundle,
            **replay_arguments(projection),
        )
        self.assertEqual(tuple(item.policy_id for item in policies), (projection.policy_id,))

    def test_persisted_consumer_revalidates_the_bundle_on_every_call(self):
        from scripts.adapters.pathway_bridge import PathwayBridgeError, bridge_pathway_policies

        source = candidate()
        projection = project(candidates=(source,))
        temporary, _bridge, _validation, bundle = persist_projection_bundle(
            projection, source
        )
        self.addCleanup(temporary.cleanup)
        arguments = replay_arguments(projection)

        self.assertEqual(len(bridge_pathway_policies(bundle, **arguments)), 1)
        (bundle / "normalized" / "facts.jsonl").write_text(
            "{}\n", encoding="utf-8", newline="\n"
        )
        with self.assertRaises(PathwayBridgeError) as raised:
            bridge_pathway_policies(bundle, **arguments)
        self.assertNotIn(str(bundle), str(raised.exception))

    def test_typed_direct_bridge_is_factory_revalidated_before_consumption(self):
        from scripts.adapters.pathway_bridge import (
            PathwayBridgeError,
            bridge_pathway_policies,
            bridge_pathway_policy_evidence,
        )

        projection = project()
        bridge = bridge_pathway_policy_evidence(projection)
        arguments = replay_arguments(projection)
        self.assertEqual(len(bridge_pathway_policies((bridge,), **arguments)), 1)

        with self.assertRaises(PathwayBridgeError):
            bridge_pathway_policies(
                (bridge,),
                province=projection.province,
                subject_mode=projection.subject_mode,
                target_year=projection.target_year,
            )
        for digest_name in ("expected_profile_digest", "expected_query_plan_digest"):
            mismatched = {
                **arguments,
                digest_name: "sha256:" + "0" * 64,
            }
            with self.subTest(digest_name=digest_name), self.assertRaises(
                PathwayBridgeError
            ):
                bridge_pathway_policies((bridge,), **mismatched)

        object.__setattr__(bridge, "evidence_method", "forged")
        with self.assertRaises(PathwayBridgeError):
            bridge_pathway_policies((bridge,), **arguments)

    def test_persisted_replay_binds_the_validated_candidate_url_identity(self):
        from scripts.adapters.pathway_bridge import PathwayBridgeError, bridge_pathway_policies

        source = candidate()
        projection = project(candidates=(source,))
        temporary, _bridge, _validation, bundle = persist_projection_bundle(
            projection, source
        )
        self.addCleanup(temporary.cleanup)
        candidate_path = bundle / "candidates.jsonl"
        record = json.loads(candidate_path.read_text(encoding="utf-8"))
        record["url"] = "https://replacement.example.cn/policy.html"
        candidate_path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        rewrite_bundle_manifest(bundle)

        with self.assertRaises(PathwayBridgeError):
            bridge_pathway_policies(
                bundle,
                **replay_arguments(projection),
            )

    def test_persisted_replay_binds_citation_fact_sources_and_context(self):
        from scripts.adapters.pathway_bridge import PathwayBridgeError, bridge_pathway_policies

        def exercise(mutator) -> None:
            sources = (
                candidate("official-one", host="one.example.cn"),
                candidate("official-two", host="two.example.cn"),
            )
            projection = project(
                extraction=(policy_table(), policy_table()),
                field_map=tuple(
                    {name: name for name in POLICY_FIELDS} for _ in sources
                ),
                candidates=sources,
            )
            temporary, _bridge, _validation, bundle = persist_projection_bundle(
                projection, *sources
            )
            self.addCleanup(temporary.cleanup)
            mutator(bundle, projection)
            rewrite_bundle_manifest(bundle)
            with self.assertRaises(PathwayBridgeError):
                bridge_pathway_policies(
                    bundle,
                    **replay_arguments(projection),
                )

        def change_citation(bundle: Path, _projection) -> None:
            path = bundle / "candidates.jsonl"
            records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
            records[0]["citation_root"] = "https://citation.example.cn/"
            path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                    for item in records
                ),
                "utf-8",
                newline="\n",
            )

        def narrow_fact_sources(bundle: Path, projection) -> None:
            replacement_source_id = next(
                item
                for item in ("official-one", "official-two")
                if item not in projection.source_ids
            )
            fact_path = bundle / "normalized" / "facts.jsonl"
            fact = json.loads(fact_path.read_text("utf-8"))
            fact["source_ids"] = [replacement_source_id]
            fact_path.write_text(
                json.dumps(fact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                "utf-8",
                newline="\n",
            )
            context_path = bundle / "context.jsonl"
            context = json.loads(context_path.read_text("utf-8"))
            context["source_ids"] = [replacement_source_id]
            context_path.write_text(
                json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                "utf-8",
                newline="\n",
            )

        def change_context_year(bundle: Path, _projection) -> None:
            path = bundle / "context.jsonl"
            context = json.loads(path.read_text("utf-8"))
            context["year"] = 2027
            path.write_text(
                json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                "utf-8",
                newline="\n",
            )

        def change_context_method(bundle: Path, _projection) -> None:
            path = bundle / "context.jsonl"
            context = json.loads(path.read_text("utf-8"))
            context["extraction_method"] = "manual-structured"
            path.write_text(
                json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                "utf-8",
                newline="\n",
            )

        def change_context_locator(bundle: Path, _projection) -> None:
            path = bundle / "context.jsonl"
            context = json.loads(path.read_text("utf-8"))
            context["locator"] = "table[1]/tbody/tr[1]"
            path.write_text(
                json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                "utf-8",
                newline="\n",
            )

        for name, mutator in (
            ("citation", change_citation),
            ("fact-source-coordination", narrow_fact_sources),
            ("context-year", change_context_year),
            ("context-method", change_context_method),
            ("context-locator", change_context_locator),
        ):
            with self.subTest(name=name):
                exercise(mutator)

    def test_schema_declares_the_typed_extraction_and_replay_seam(self):
        schema = json.loads(
            Path("schemas/pathway-policy.schema.json").read_text(encoding="utf-8")
        )
        extraction = schema["x-pathway-extraction"]
        self.assertEqual(
            extraction["projection"],
            "scripts.adapters.pathway_extraction.PathwayPolicyProjection",
        )
        self.assertEqual(
            extraction["factory"],
            "scripts.adapters.pathway_extraction.extract_pathway_policy",
        )
        self.assertEqual(
            extraction["evidence_bridge"],
            "scripts.adapters.pathway_bridge.bridge_pathway_policy_evidence",
        )
        self.assertEqual(extraction["required_fields"], list(POLICY_FIELDS))
        self.assertEqual(
            extraction["adapter_outputs"],
            ["html-table", "xlsx-worksheet", "pdfplumber-text", "host-ocr-rows"],
        )

    def test_factory_projection_is_the_only_path_to_decisive_policy(self):
        from scripts.adapters.pathway_bridge import bridge_pathway_policies

        student = profile()
        query_plan = plan(student)
        source = candidate()
        projection = project(student=student, query_plan=query_plan, candidates=(source,))
        temporary, bridge, validation, bundle = persist_projection_bundle(
            projection, source
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(bridge.fact.field, f"pathway_policy:{projection.policy_id}")
        self.assertEqual(bridge.fact.value["projection_hash"], projection.digest)
        self.assertEqual(bridge.fact.value["profile_digest"], student.digest)
        self.assertEqual(bridge.fact.value["query_task_id"], task_for(query_plan).task_id)
        self.assertEqual(bridge.fact.value["coverage_status"], "complete")
        self.assertEqual(bridge.fact.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(bridge.fact.source_ids, (source.source_id,))
        self.assertEqual(validation.issues, ())
        self.assertIsNotNone(validation.snapshot)
        policies = bridge_pathway_policies(
            bundle,
            **replay_arguments(projection),
        )
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0].policy_id, projection.policy_id)
        self.assertEqual(policies[0].professional_options, ("示例专业",))

    def test_factory_only_frozen_json_and_path_neutral_contracts(self):
        from scripts.adapters.pathway_extraction import FieldProvenance, PathwayPolicyProjection
        from scripts.adapters.pathway_bridge import PathwayPolicyEvidenceBridge

        for constructor in (FieldProvenance, PathwayPolicyProjection, PathwayPolicyEvidenceBridge):
            with self.subTest(constructor=constructor), self.assertRaises(TypeError):
                constructor()
        projection = project()
        with self.assertRaises(FrozenInstanceError):
            projection.title = "篡改"
        self.assertEqual(
            json.loads(json.dumps(projection.to_dict(), ensure_ascii=False)),
            projection.to_dict(),
        )
        with self.assertRaises((TypeError, ValueError)):
            project(extraction=policy_table(institution="C:\\private\\policy.pdf"))

    def test_bridge_revalidates_direct_and_replace_forgery(self):
        from scripts.adapters.pathway_bridge import (
            PathwayBridgeError,
            bridge_pathway_policy_evidence,
            validate_pathway_policy_evidence_bridge,
        )

        bridge = bridge_pathway_policy_evidence(project())
        self.assertIs(validate_pathway_policy_evidence_bridge(bridge), bridge)
        with self.assertRaises(TypeError):
            replace(bridge, evidence_status=EvidenceStatus.REFERENCE)
        object.__setattr__(bridge, "source_ids", ("forged-source",))
        with self.assertRaises(PathwayBridgeError):
            validate_pathway_policy_evidence_bridge(bridge)

    def test_caller_cannot_assert_policy_status_sources_or_detached_context(self):
        from scripts.adapters.pathway_extraction import PathwayExtractionError, extract_pathway_policy

        student = profile()
        query_plan = plan(student)
        task = task_for(query_plan)
        common = {
            "profile": student,
            "plan": query_plan,
            "task": task,
            "extraction": policy_table(),
            "field_map": {name: name for name in POLICY_FIELDS},
            "candidates": (candidate(),),
        }
        for extra in (
            {"policy_id": "forged"},
            {"evidence_status": EvidenceStatus.OFFICIAL},
            {"source_ids": ("official-pathway",)},
        ):
            with self.subTest(extra=extra), self.assertRaises(TypeError):
                extract_pathway_policy(**common, **extra)
        with self.assertRaises(PathwayExtractionError):
            extract_pathway_policy(**{**common, "task": replace(task)})
        with self.assertRaises(PathwayExtractionError):
            extract_pathway_policy(
                **{**common, "candidates": (candidate(publisher=""),)}
            )
        for changed in (
            {"province": "湖南"},
            {"subject_mode": "3+3"},
            {"year": 2027},
        ):
            with self.subTest(changed=changed), self.assertRaises(PathwayExtractionError):
                extract_pathway_policy(
                    **{**common, "extraction": policy_table(**changed)}
                )

    def test_profile_pathway_trace_must_match_the_canonical_plan(self):
        from scripts.adapters.pathway_extraction import PathwayExtractionError

        student_a = profile()
        query_plan = plan(student_a)
        profile_b_value = student_a.to_dict()
        profile_b_value.pop("mode")
        profile_b_value.pop("digest")
        profile_b_value["pathway_preferences"]["comprehensive_evaluation"] = (
            "not_interested"
        )
        student_b = load_planning_profile(profile_b_value)

        with self.assertRaises(PathwayExtractionError):
            project(student=student_b, query_plan=query_plan, task=task_for(query_plan))

    def test_incomplete_masked_and_ambiguous_fields_stay_non_decisive(self):
        from scripts.adapters.pathway_bridge import bridge_pathway_policies

        for field in POLICY_FIELDS:
            with self.subTest(missing_critical_field=field):
                projection = project(extraction=policy_table(**{field: None}))
                provenance = {item.field: item for item in projection.field_provenance}
                self.assertIs(projection.evidence_status, EvidenceStatus.PARTIAL)
                self.assertIs(provenance[field].status, EvidenceStatus.MISSING)

        cases = {
            "missing": policy_table(professional_options=None),
            "masked": policy_table(
                professional_options=None,
                statuses={"professional_options": CellStatus.MASKED},
            ),
            "uncertain": policy_table(
                statuses={"professional_options": CellStatus.UNCERTAIN},
            ),
        }
        expected_field_status = {
            "missing": EvidenceStatus.MISSING,
            "masked": EvidenceStatus.MASKED,
            "uncertain": EvidenceStatus.PARTIAL,
        }
        for label, extraction in cases.items():
            with self.subTest(label=label):
                source = candidate(f"official-{label}")
                projection = project(extraction=extraction, candidates=(source,))
                self.assertNotEqual(projection.coverage_status, "complete")
                self.assertNotIn(
                    projection.evidence_status,
                    {EvidenceStatus.OFFICIAL, EvidenceStatus.CORROBORATED, EvidenceStatus.REFERENCE},
                )
                field_provenance = {
                    item.field: item for item in projection.field_provenance
                }
                self.assertIs(
                    field_provenance["professional_options"].status,
                    expected_field_status[label],
                )
                temporary, bridge, _validation, bundle = persist_projection_bundle(
                    projection, source
                )
                self.addCleanup(temporary.cleanup)
                notes = bridge.fact.notes
                self.assertTrue(notes.startswith("pathway-projection-v1:"))
                persisted_value = json.loads(
                    base64.urlsafe_b64decode(notes.split(":", 1)[1]).decode("utf-8")
                )
                self.assertEqual(
                    persisted_value["field_coverage"]["professional_options"],
                    expected_field_status[label].value,
                )
                self.assertEqual(
                    bridge_pathway_policies(
                        bundle,
                        **replay_arguments(projection),
                    ),
                    (),
                )

        all_masked = policy_table(
            statuses={field: CellStatus.MASKED for field in POLICY_FIELDS},
            **{field: None for field in POLICY_FIELDS},
        )
        source = candidate("official-all-masked")
        projection = project(extraction=all_masked, candidates=(source,))
        self.assertIs(projection.evidence_status, EvidenceStatus.MASKED)
        self.assertEqual(projection.coverage_status, "missing")
        temporary, _bridge, validation = persist_projection(projection, source)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(validation.issues, ())

    def test_mixed_field_evidence_methods_use_the_weakest_accepted_field(self):
        sources = (
            candidate("official-most-fields", host="official.example.cn"),
            candidate(
                "independent-b-one",
                tier=SourceTier.B,
                publisher="独立乙一",
                host="b1.example.cn",
            ),
            candidate(
                "independent-b-two",
                tier=SourceTier.B,
                publisher="独立乙二",
                host="b2.example.cn",
            ),
        )
        projection = project(
            extraction=(
                policy_table(professional_options=None),
                policy_table(),
                policy_table(),
            ),
            field_map=tuple(
                {name: name for name in POLICY_FIELDS} for _ in sources
            ),
            candidates=sources,
        )
        provenance = {item.field: item for item in projection.field_provenance}

        self.assertEqual(projection.coverage_status, "complete")
        self.assertIs(projection.evidence_status, EvidenceStatus.CORROBORATED)
        self.assertEqual(projection.evidence_method, "two-source-consensus")
        self.assertEqual(
            projection.evidence_methods,
            ("tier-a", "two-source-consensus"),
        )
        self.assertEqual(
            provenance["professional_options"].evidence_method,
            "two-source-consensus",
        )
        self.assertEqual(
            provenance["institution"].evidence_method,
            "tier-a",
        )
        temporary, _bridge, validation, bundle = persist_projection_bundle(
            projection, *sources
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(validation.issues, ())
        from scripts.adapters.pathway_bridge import bridge_pathway_policies

        self.assertEqual(
            len(
                bridge_pathway_policies(
                    bundle,
                    **replay_arguments(projection),
                )
            ),
            1,
        )

    def test_source_policy_derives_a_b_c_and_conflicts_without_averaging(self):
        from scripts.adapters.pathway_bridge import bridge_pathway_policies

        source_sets = (
            ((policy_table(),), (candidate(),), EvidenceStatus.OFFICIAL, True),
            (
                (policy_table(), policy_table()),
                (
                    candidate("b-one", tier=SourceTier.B, publisher="独立乙一", host="b1.example.cn"),
                    candidate("b-two", tier=SourceTier.B, publisher="独立乙二", host="b2.example.cn"),
                ),
                EvidenceStatus.CORROBORATED,
                True,
            ),
            (
                (policy_table(), policy_table(), policy_table()),
                tuple(
                    candidate(
                        f"c-{index}",
                        tier=SourceTier.C,
                        publisher=f"独立丙{index}",
                        host=f"c{index}.example.cn",
                    )
                    for index in range(1, 4)
                ),
                EvidenceStatus.REFERENCE,
                True,
            ),
            (
                (policy_table(),),
                (candidate("c-only", tier=SourceTier.C, publisher="单一丙", host="c.example.cn"),),
                EvidenceStatus.PARTIAL,
                False,
            ),
            (
                (policy_table(), policy_table()),
                (
                    candidate("same-b1", tier=SourceTier.B, publisher="SAME PUBLISHER", host="same-b1.example.cn"),
                    candidate("same-b2", tier=SourceTier.B, publisher="same publisher", host="same-b2.example.cn"),
                ),
                EvidenceStatus.PARTIAL,
                False,
            ),
            (
                (policy_table(), policy_table(professional_options="冲突专业")),
                (
                    candidate("conflict-b1", tier=SourceTier.B, publisher="冲突乙一", host="cb1.example.cn"),
                    candidate("conflict-b2", tier=SourceTier.B, publisher="冲突乙二", host="cb2.example.cn"),
                ),
                EvidenceStatus.CONFLICT,
                False,
            ),
        )
        for extractions, sources, expected_status, decisive in source_sets:
            with self.subTest(status=expected_status, decisive=decisive):
                projection = project(
                    extraction=extractions,
                    field_map=tuple({name: name for name in POLICY_FIELDS} for _ in extractions),
                    candidates=sources,
                )
                self.assertIs(projection.evidence_status, expected_status)
                if expected_status is EvidenceStatus.CONFLICT:
                    self.assertIsNone(projection.professional_options)
                temporary, _bridge, validation, bundle = persist_projection_bundle(
                    projection, *sources
                )
                self.addCleanup(temporary.cleanup)
                policies = bridge_pathway_policies(
                    bundle,
                    **replay_arguments(projection),
                )
                self.assertEqual(bool(policies), decisive)

    def test_direct_b_citation_to_a_document_survives_minimal_replay_projection(self):
        official = candidate("official-upstream")
        citing = candidate(
            "direct-b",
            tier=SourceTier.B,
            publisher="独立乙来源",
            host="direct-b.example.cn",
            citation_root=official.url,
        )
        missing_official = policy_table(
            **{field: None for field in POLICY_FIELDS}
        )
        projection = project(
            extraction=(missing_official, policy_table()),
            field_map=tuple(
                {name: name for name in POLICY_FIELDS} for _ in range(2)
            ),
            candidates=(official, citing),
        )

        self.assertIs(projection.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(projection.evidence_method, "direct-a-upstream")
        self.assertEqual(projection.source_ids, (citing.source_id,))

    def test_all_seven_profile_pathway_families_project_without_invented_policy_data(self):
        student = profile()
        query_plan = plan(student)
        representatives = {
            "strong_foundation": ("强基计划", "strong_foundation"),
            "comprehensive_evaluation": ("综合评价", "comprehensive_evaluation"),
            "special_program": ("国家专项", "national_special"),
            "service_oriented": ("公费师范", "public_funded_teacher"),
            "uniformed_service": ("军校", "military"),
            "cross_border": ("港澳招生", "hong_kong_macao"),
            "arts_sports": ("艺体类", "arts_sports"),
        }
        for family, (target, expected_type) in representatives.items():
            task = task_for(query_plan, target)
            projection = project(
                student=student,
                query_plan=query_plan,
                task=task,
                extraction=policy_table(),
            )
            with self.subTest(family=family):
                self.assertEqual(projection.pathway_family, family)
                self.assertEqual(projection.pathway_type, expected_type)
                self.assertEqual(projection.title, target)

    def test_hand_built_and_field_incomplete_persisted_facts_fail_replay(self):
        from scripts.adapters.pathway_bridge import PathwayBridgeError, bridge_pathway_policies

        source = candidate()
        projection = project(candidates=(source,))
        mutations = (
            lambda value: value["value"].__setitem__("projection_hash", "sha256:" + "0" * 64),
            lambda value: value["value"].__setitem__("profile_digest", "sha256:" + "1" * 64),
            lambda value: value["value"].__setitem__("query_plan_digest", "sha256:" + "2" * 64),
            lambda value: value["value"].__setitem__("query_task_id", "forged-task"),
            lambda value: value["value"].__setitem__("query_task_digest", "sha256:" + "3" * 64),
            lambda value: value["value"].__setitem__("extraction_digest", "sha256:" + "4" * 64),
            lambda value: value["value"].__setitem__("provenance_digest", "sha256:" + "5" * 64),
            lambda value: value["value"].__setitem__("input_digest", "sha256:" + "6" * 64),
            lambda value: value["value"].__setitem__("provenance_count", 0),
            lambda value: value["value"].pop("field_provenance"),
            lambda value: value["value"]["input_projection"].pop("sources"),
        )
        for mutate in mutations:
            temporary, bridge, _validation, bundle = persist_projection_bundle(
                projection, source
            )
            self.addCleanup(temporary.cleanup)
            forged = json.loads(json.dumps(bridge.fact.to_dict(), ensure_ascii=False))
            mutate(forged)
            fact_path = bundle / "normalized" / "facts.jsonl"
            fact_path.write_text(
                json.dumps(
                    forged,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                "utf-8",
                newline="\n",
            )
            rewrite_bundle_manifest(bundle)
            with self.subTest(mutate=mutate):
                with self.assertRaises(PathwayBridgeError):
                    bridge_pathway_policies(
                        bundle,
                        **replay_arguments(projection),
                    )


if __name__ == "__main__":
    unittest.main()
