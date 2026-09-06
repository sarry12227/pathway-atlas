from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest

from scripts.adapters import (
    CellStatus,
    ColumnMapping,
    ExtractedCoverage,
    ExtractedRow,
    ExtractedTable,
)
from scripts.adapters.html_table import extract_html_table
from scripts.contracts import EvidenceStatus, SourceCandidate, SourceTier
from scripts.decision_policy import DecisionPolicySnapshot
from scripts.planning_profile import PlanningProfile, load_planning_profile
from scripts.query_plan import QueryPlan, build_query_plan, load_province_catalog


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
                    "exam_date": "2026-06-01",
                    "scope": "school",
                    "score": 610,
                    "max_score": 750,
                    "rank": 120,
                    "cohort_size": 1000,
                }
            ],
            "best_rank": 80,
            "usual_rank": 140,
            "awards": [],
            "activities": [],
            "target_schools": [],
            "target_school_reasons": [],
            "target_majors": ["历史学"],
            "target_major_reasons": ["长期兴趣"],
            "target_regions": ["武汉"],
            "excluded_regions": [],
            "future_plan": "继续深造",
            "concerns": ["院校定位"],
            "desired_outcomes": ["院校范围", "多元路径"],
            "eligibility_facts": ["接受异地就读"],
        }
    )


def plan(student: PlanningProfile) -> QueryPlan:
    return build_query_plan(
        student,
        load_province_catalog(),
        DecisionPolicySnapshot.load_default(),
    )


def canonical_digest(value) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def unresolved_official_score_profile() -> PlanningProfile:
    payload = profile().to_dict()
    payload.pop("mode")
    payload.pop("digest")
    payload["rank_observations"] = [
        {
            **payload["rank_observations"][0],
            "scope": "province_official",
            "score": 610,
            "rank": None,
            "cohort_size": None,
            "source": "official_score",
        }
    ]
    return PlanningProfile.create(payload)


def candidate(
    source_id: str = "official-score",
    *,
    tier: SourceTier = SourceTier.A,
    publisher: str = "湖北省教育考试院",
    host: str = "www.hbea.edu.cn",
) -> SourceCandidate:
    return SourceCandidate(
        source_id=source_id,
        url=f"https://{host}/{source_id}.html",
        publisher=publisher,
        tier=tier,
        published_at="2025-06-25",
        retrieved_at="2026-06-26T00:00:00Z",
        content_hash="sha256:" + hashlib.sha256(source_id.encode()).hexdigest(),
        citation_root=f"https://{host}/",
        summary="合成位次资料",
    )


def score_table():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary, "score.html")
        source.write_text(
            "<table><caption>一分一段表</caption>"
            "<tr><th>分数</th><th>位次</th><th>累计人数</th></tr>"
            "<tr><td>610</td><td>18000</td><td>18000</td></tr>"
            "<tr><td>100</td><td>200000</td><td>200000</td></tr></table>",
            encoding="utf-8",
        )
        return extract_html_table(
            source.resolve(),
            table_index=1,
            expected_caption="一分一段表",
            mapping=ColumnMapping(
                {"score": "分数", "rank": "位次", "cumulative_count": "累计人数"},
                roles={"score": "score", "rank": "rank", "cumulative_count": "rank"},
                score_scale=(0, 750),
            ),
        )


class RankEvidenceBridgeTest(unittest.TestCase):
    def test_real_two_column_score_table_derives_rank_from_cumulative_count(self):
        from scripts.adapters.rank_bridge import bridge_rank_evidence, validate_rank_evidence_bridge
        student = unresolved_official_score_profile()
        query_plan = plan(student)
        task = next(t for t in query_plan.tasks if t.kind == "score_table")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "table.html"
            path.write_text(
                '<table><caption>一分一段表</caption><tr><th>分数</th><th>累计人数</th></tr>'
                '<tr><td>610</td><td>18000</td></tr><tr><td>609</td><td>18500</td></tr></table>',
                encoding="utf-8",
            )
            table = extract_html_table(path, table_index=1, expected_caption="一分一段表",
                mapping=ColumnMapping({"score": "分数", "cumulative_count": "累计人数"},
                    roles={"cumulative_count": "rank"}, score_scale=(0, 750)))
            bridge = bridge_rank_evidence(profile=student, plan=query_plan, task=task,
                table=table, extracted_row=table.rows[0], candidates=(candidate(),),
                coverage_status=EvidenceStatus.OFFICIAL)
        self.assertEqual(bridge.fact.value["rank"], 18000)
        self.assertEqual(bridge.fact.value["cumulative_count"], 18000)
        self.assertIs(validate_rank_evidence_bridge(bridge, profile=student, plan=query_plan), bridge)

    def test_validator_replays_an_equal_task_from_the_canonical_plan(self):
        from scripts.adapters.rank_bridge import (
            bridge_rank_evidence,
            validate_rank_evidence_bridge,
        )
        from scripts.query_plan import validate_query_plan_payload

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()
        bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=task,
            table=table,
            extracted_row=table.rows[0],
            candidates=(candidate(),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        canonical_plan = validate_query_plan_payload(query_plan.to_dict())

        self.assertIsNot(bridge.task, canonical_plan.tasks[0])
        self.assertIs(
            validate_rank_evidence_bridge(bridge, student, canonical_plan),
            bridge,
        )

    def test_validator_rejects_a_mutated_bridge_task_against_the_canonical_plan(self):
        from copy import copy

        from scripts.adapters.rank_bridge import (
            RankBridgeError,
            bridge_rank_evidence,
            validate_rank_evidence_bridge,
        )
        from scripts.query_plan import validate_query_plan_payload

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()
        canonical_plan = validate_query_plan_payload(query_plan.to_dict())
        mutations = {
            "year": 2024,
            "kind": "batch_admission",
            "target_name": "伪造批次",
            "query_variants": (
                "湖北 2025 历史 湖北省教育考试院 伪造一分一段表",
            ),
            "source_policy_id": "forged-source-policy",
            "source_policy_version": "9.9",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                bridge = bridge_rank_evidence(
                    profile=student,
                    plan=query_plan,
                    task=task,
                    table=table,
                    extracted_row=table.rows[0],
                    candidates=(candidate(),),
                    coverage_status=EvidenceStatus.OFFICIAL,
                )
                detached = copy(bridge.task)
                object.__setattr__(detached, field, value)
                object.__setattr__(bridge, "task", detached)
                with self.assertRaises(RankBridgeError):
                    validate_rank_evidence_bridge(bridge, student, canonical_plan)

    def test_validator_rejects_coordinated_visible_coverage_digest_rehash(self):
        from scripts.adapters.rank_bridge import (
            RankBridgeError,
            bridge_rank_evidence,
            validate_rank_evidence_bridge,
        )

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()
        transitions = (
            (EvidenceStatus.OFFICIAL, EvidenceStatus.CORROBORATED),
            (EvidenceStatus.CORROBORATED, EvidenceStatus.REFERENCE),
            (EvidenceStatus.REFERENCE, EvidenceStatus.OFFICIAL),
        )
        for original, forged_status in transitions:
            with self.subTest(original=original, forged=forged_status):
                bridge = bridge_rank_evidence(
                    profile=student,
                    plan=query_plan,
                    task=task,
                    table=table,
                    extracted_row=table.rows[0],
                    candidates=(candidate(),),
                    coverage_status=original,
                )
                forged_value = json.loads(bridge._fact_value_json)
                forged_value["coverage_status"] = forged_status.value
                object.__setattr__(
                    bridge,
                    "_fact_value_json",
                    json.dumps(
                        forged_value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                )
                object.__setattr__(bridge, "coverage_status", forged_status)
                forged_provenance = canonical_digest(
                    {
                        "task_id": bridge.task.task_id,
                        "year": bridge.task.year,
                        "source_ids": list(bridge.source_ids),
                        "evidence_status": bridge.evidence_status.value,
                        "coverage_status": forged_status.value,
                        "evidence_method": bridge.evidence_method,
                        "extraction_method": bridge.extraction_method,
                        "locator_hash": forged_value["input_projection"]["locator_hash"],
                    }
                )
                object.__setattr__(bridge, "provenance_digest", forged_provenance)
                object.__setattr__(
                    bridge,
                    "bridge_digest",
                    canonical_digest(
                        {
                            "profile_digest": bridge.profile_digest,
                            "query_plan_digest": bridge.query_plan_digest,
                            "task_id": bridge.task.task_id,
                            "fact_id": bridge.fact_id,
                            "row_hash": bridge.row_hash,
                            "artifact_digest": bridge.artifact_digest,
                            "provenance_digest": forged_provenance,
                        }
                    ),
                )

                with self.assertRaises(RankBridgeError):
                    validate_rank_evidence_bridge(bridge, student, query_plan)

    def test_unrelated_score_table_row_cannot_authorize_fallback_stop(self):
        from scripts.adapters.rank_bridge import bridge_rank_evidence
        from scripts.planning_session import build_task_evidence_outcome

        student = unresolved_official_score_profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()
        wrong = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=task,
            table=table,
            extracted_row=table.rows[1],
            candidates=(candidate(),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        wrong_receipt = build_task_evidence_outcome(
            student, query_plan, task, (wrong,)
        )

        self.assertEqual(wrong.fact.value["score"], 100)
        self.assertEqual(wrong.evidence_status, EvidenceStatus.PARTIAL)
        self.assertFalse(wrong_receipt.usable)

        matching = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=task,
            table=table,
            extracted_row=table.rows[0],
            candidates=(candidate(),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )
        matching_receipt = build_task_evidence_outcome(
            student, query_plan, task, (matching,)
        )
        self.assertEqual(matching.fact.value["score"], 610)
        self.assertEqual(matching.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertTrue(matching_receipt.usable)

    def test_exact_official_score_row_becomes_digest_bound_channel(self):
        from scripts.adapters.rank_bridge import bridge_rank_evidence

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()

        bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=task,
            table=table,
            extracted_row=table.rows[0],
            candidates=(candidate(),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )

        self.assertEqual(bridge.fact.field, "rank_channel:score-table-2025")
        self.assertEqual(bridge.fact.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(bridge.fact.value["kind"], "official_score_table")
        self.assertEqual(bridge.fact.value["profile_digest"], student.digest)
        self.assertEqual(bridge.fact.value["query_task_id"], task.task_id)
        self.assertEqual(bridge.fact.value["score"], 610)
        projection = bridge.fact.value["input_projection"]
        self.assertEqual(projection["task_id"], task.task_id)
        self.assertEqual(projection["row"]["values"], table.rows[0].to_dict()["values"])
        self.assertEqual(projection["coverage"], table.coverage.to_dict())
        self.assertEqual(projection["sources"][0]["source_id"], "official-score")
        self.assertRegex(
            bridge.fact.value["content_hash"], r"^sha256:[0-9a-f]{64}$"
        )
        self.assertRegex(bridge.artifact_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(bridge.provenance_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            json.loads(json.dumps(bridge.to_dict(), ensure_ascii=False)),
            bridge.to_dict(),
        )

    def test_joint_exam_requires_cohort_and_emits_bounded_percentiles(self):
        from scripts.adapters.rank_bridge import bridge_rank_evidence

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "joy_report" and item.year == 2025
        )
        row = ExtractedRow(
            values={
                "scope": "joint_exam",
                "rank_scope": "city_joint",
                "exam_date": "2025-06-01",
                "lower_rank": 80,
                "central_rank": 120,
                "upper_rank": 160,
                "cohort_size": 1000,
            },
            cell_status={name: CellStatus.EXACT for name in (
                "scope", "rank_scope", "exam_date", "lower_rank",
                "central_rank", "upper_rank", "cohort_size"
            )},
            location="table[1]/tbody/tr[1]",
            confidence=1,
        )
        table = ExtractedTable(
            table_id="table[1]",
            caption="联考位次区间",
            sheet=None,
            rows=(row,),
            coverage=ExtractedCoverage(lower_rank=1, upper_rank=1000),
            warnings=(),
            extraction_method="html-table",
        )

        bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=task,
            table=table,
            extracted_row=row,
            candidates=(candidate("joint-official"),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )

        self.assertEqual(
            bridge.fact.field,
            "rank_channel:joint-exam-2025-06-01-city_joint",
        )
        self.assertEqual(bridge.fact.value["kind"], "joint_exam")
        self.assertEqual(
            (
                bridge.fact.value["lower_percentile"],
                bridge.fact.value["central_percentile"],
                bridge.fact.value["upper_percentile"],
            ),
            (0.08, 0.12, 0.16),
        )
        self.assertEqual(bridge.fact.value["cohort_size"], 1000)
        self.assertEqual(bridge.fact.value["rank_scope"], "city_joint")
        self.assertEqual(bridge.fact.value["exam_date"], "2025-06-01")

    def test_school_result_requires_matching_school_class_and_coverage(self):
        from scripts.adapters.rank_bridge import bridge_rank_evidence

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "joy_report" and item.year == 2025
        )
        values = {
            "scope": "school_anchor",
            "school_name": student.high_school,
            "class_level": student.class_level,
            "school_rank": 120,
            "province_rank": 18000,
            "school_score": 610,
            "max_score": 750,
            "cohort_size": 1000,
        }
        row = ExtractedRow(
            values=values,
            cell_status={name: CellStatus.EXACT for name in values},
            location="table[1]/tbody/tr[1]",
            confidence=1,
        )
        table = ExtractedTable(
            table_id="table[1]",
            caption="学校成绩锚点",
            sheet=None,
            rows=(row,),
            coverage=ExtractedCoverage(lower_rank=1, upper_rank=1000),
            warnings=(),
            extraction_method="html-table",
        )

        bridge = bridge_rank_evidence(
            profile=student,
            plan=query_plan,
            task=task,
            table=table,
            extracted_row=row,
            candidates=(candidate("school-official"),),
            coverage_status=EvidenceStatus.OFFICIAL,
        )

        self.assertEqual(bridge.fact.field, "rank_anchor:school-anchor-2025")
        self.assertEqual(bridge.fact.value["school_name"], student.high_school)
        self.assertEqual(bridge.fact.value["scope_type"], "named_program")
        self.assertEqual(bridge.fact.value["scope_value"], student.class_level)
        self.assertEqual(bridge.fact.value["coverage_max_school_rank"], 1000)

    def test_score_scale_and_canonical_four_year_task_are_fail_closed(self):
        from scripts.adapters.rank_bridge import RankBridgeError, bridge_rank_evidence

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        base = score_table()
        over_scale_row = ExtractedRow(
            values={"score": 760, "rank": 1, "cumulative_count": 1},
            cell_status={
                "score": CellStatus.EXACT,
                "rank": CellStatus.EXACT,
                "cumulative_count": CellStatus.EXACT,
            },
            location="table[1]/tbody/tr[1]",
            confidence=1,
        )
        over_scale_table = replace(
            base,
            rows=(over_scale_row,),
            coverage=ExtractedCoverage(
                lower_score=760,
                upper_score=760,
                lower_rank=1,
                upper_rank=1,
            ),
        )
        arguments = {
            "profile": student,
            "plan": query_plan,
            "task": task,
            "table": over_scale_table,
            "extracted_row": over_scale_row,
            "candidates": (candidate(),),
            "coverage_status": EvidenceStatus.OFFICIAL,
        }
        with self.assertRaises(RankBridgeError):
            bridge_rank_evidence(**arguments)

        valid_table = score_table()
        object.__setattr__(task, "year", 2024)
        with self.assertRaises(RankBridgeError):
            bridge_rank_evidence(
                **{
                    **arguments,
                    "table": valid_table,
                    "extracted_row": valid_table.rows[0],
                }
            )

    def test_persisted_projection_is_minimal_path_neutral_and_privacy_safe(self):
        from scripts.adapters.rank_bridge import RankBridgeError, bridge_rank_evidence

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = replace(score_table(), caption="历史类一分一段表", sheet="位次工作表")
        source = candidate()
        common = {
            "profile": student,
            "plan": query_plan,
            "task": task,
            "table": table,
            "extracted_row": table.rows[0],
            "candidates": (source,),
            "coverage_status": EvidenceStatus.OFFICIAL,
        }

        bridge = bridge_rank_evidence(**common)

        projection = bridge.fact.value["input_projection"]
        self.assertEqual(
            set(projection),
            {
                "task_id",
                "row",
                "coverage",
                "extraction_method",
                "locator_hash",
                "sources",
            },
        )
        self.assertEqual(set(projection["row"]), {"values"})
        self.assertEqual(
            set(projection["sources"][0]),
            {
                "source_id",
                "tier",
                "publisher_hash",
                "site_hash",
                "url_hash",
                "citation_root_hash",
                "citation_site_hash",
                "content_hash",
            },
        )
        serialized = json.dumps(bridge.fact.to_dict(), ensure_ascii=False)
        for absent in (
            "历史类一分一段表",
            "位次工作表",
            source.url,
            source.citation_root,
        ):
            self.assertNotIn(absent, serialized)
        from scripts.research_snapshot import build_research_snapshot

        research = build_research_snapshot(
            student,
            query_plan,
            (bridge,),
            DecisionPolicySnapshot.load_default(),
        )
        research_json = json.dumps(research.to_dict(), ensure_ascii=False)
        self.assertNotIn(r"C:\Users", research_json)
        self.assertNotIn("../../", research_json)
        self.assertNotIn(r"\\server\share", research_json)

        unsafe_tables = (
            replace(table, caption=r"C:\Users\student\score.xlsx"),
            replace(table, sheet="../../private/score.xlsx"),
        )
        for unsafe_table in unsafe_tables:
            with self.subTest(table=unsafe_table.to_dict()):
                with self.assertRaises(RankBridgeError):
                    bridge_rank_evidence(
                        **{
                            **common,
                            "table": unsafe_table,
                            "extracted_row": unsafe_table.rows[0],
                        }
                    )

        unsafe_sources = (
            replace(source, summary="token=super-secret-value"),
            replace(source, publisher=r"C:\Users\student\publisher.txt"),
            replace(
                source,
                url="https://source.example.cn/C:/Users/student/score.html",
            ),
        )
        for unsafe_source in unsafe_sources:
            with self.subTest(source=unsafe_source.to_dict()):
                with self.assertRaises(RankBridgeError):
                    bridge_rank_evidence(
                        **{**common, "candidates": (unsafe_source,)}
                    )

        unsafe_row = replace(table.rows[0])
        object.__setattr__(unsafe_row, "location", r"\\server\share\score.xlsx")
        unsafe_locator_table = replace(table, rows=(unsafe_row, *table.rows[1:]))
        with self.assertRaises(RankBridgeError):
            bridge_rank_evidence(
                **{
                    **common,
                    "table": unsafe_locator_table,
                    "extracted_row": unsafe_row,
                }
            )

    def test_source_policy_derives_a_b_c_status_and_rejects_one_b(self):
        from scripts.adapters.rank_bridge import RankBridgeError, bridge_rank_evidence

        student = profile()
        query_plan = plan(student)
        task = next(
            item for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()
        common = {
            "profile": student,
            "plan": query_plan,
            "task": task,
            "table": table,
            "extracted_row": table.rows[0],
            "coverage_status": EvidenceStatus.OFFICIAL,
        }
        source_sets = (
            ((candidate(),), EvidenceStatus.OFFICIAL),
            (
                (
                    candidate("b-one", tier=SourceTier.B, publisher="独立乙一", host="b1.example.cn"),
                    candidate("b-two", tier=SourceTier.B, publisher="独立乙二", host="b2.example.cn"),
                ),
                EvidenceStatus.CORROBORATED,
            ),
            (
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
            ),
        )
        for sources, expected in source_sets:
            with self.subTest(status=expected):
                self.assertIs(
                    bridge_rank_evidence(**common, candidates=sources).fact.status,
                    expected,
                )
        for source in (
            candidate(
                "b-only",
                tier=SourceTier.B,
                publisher="单一乙",
                host="b.example.cn",
            ),
            candidate(
                "c-only",
                tier=SourceTier.C,
                publisher="单一丙",
                host="c.example.cn",
            ),
        ):
            with self.subTest(rejected_tier=source.tier):
                with self.assertRaises(RankBridgeError):
                    bridge_rank_evidence(**common, candidates=(source,))

    def test_factory_rejects_caller_authority_detached_masked_and_mismatched_rows(self):
        from scripts.adapters.rank_bridge import (
            RankBridgeError,
            RankEvidenceBridge,
            bridge_rank_evidence,
        )

        student = profile()
        query_plan = plan(student)
        task = next(
            item for item in query_plan.tasks
            if item.kind == "score_table" and item.year == 2025
        )
        table = score_table()
        common = {
            "profile": student,
            "plan": query_plan,
            "task": task,
            "table": table,
            "extracted_row": table.rows[0],
            "candidates": (candidate(),),
            "coverage_status": EvidenceStatus.OFFICIAL,
        }
        with self.assertRaises(TypeError):
            RankEvidenceBridge()
        bridge = bridge_rank_evidence(**common)
        with self.assertRaises(FrozenInstanceError):
            bridge.fact_id = "forged"
        mutated_fact = bridge.fact
        mutated_fact.value["score"] = 1
        mutated_fact.value["input_projection"]["row"]["values"]["score"] = 1
        mutated_fact.value["input_projection"]["sources"][0]["source_id"] = "forged"
        self.assertEqual(bridge.fact.value["score"], 610)
        self.assertEqual(
            bridge.fact.value["input_projection"]["row"]["values"]["score"],
            610,
        )
        self.assertEqual(
            bridge.fact.value["input_projection"]["sources"][0]["source_id"],
            "official-score",
        )
        source = common["candidates"][0]
        object.__setattr__(source, "summary", "篡改来源")
        self.assertEqual(bridge.to_dict()["sources"][0]["summary"], "合成位次资料")
        with self.assertRaises(TypeError):
            bridge_rank_evidence(**common, evidence_status=EvidenceStatus.OFFICIAL)
        with self.assertRaises(TypeError):
            bridge_rank_evidence(**common, source_ids=("official-score",))
        with self.assertRaises(RankBridgeError):
            bridge_rank_evidence(**{**common, "extracted_row": replace(table.rows[0])})
        masked = replace(
            table.rows[0],
            cell_status={**table.rows[0].cell_status, "rank": CellStatus.MASKED},
        )
        with self.assertRaises(RankBridgeError):
            bridge_rank_evidence(
                **{
                    **common,
                    "table": replace(table, rows=(masked,)),
                    "extracted_row": masked,
                }
            )


if __name__ == "__main__":
    unittest.main()
