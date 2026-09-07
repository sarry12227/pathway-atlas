"""Grounded public prose inputs for pathway and other host adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import unittest

from scripts.adapters import CellStatus
from scripts.adapters.pathway_extraction import (
    PathwayExtractionError,
    extract_pathway_policy,
    replay_pathway_policy_projection,
)
from scripts.adapters.pathway_bridge import (
    PathwayBridgeError,
    bridge_pathway_observations,
    bridge_pathway_policy_evidence,
    validate_pathway_evidence_observation,
)
from scripts.adapters.public_text import (
    PublicTextAdapterError,
    PublicTextField,
    bind_public_text,
)
from scripts.contracts import EvidenceStatus
from scripts.path_recommend import evaluate_pathways
from scripts.report_model import _project_pathway, pathway_field_evidence_lines
from tests.test_school_recommend_generic import exact_rank
from tests.test_pathway_evidence_bridge import (
    POLICY_FIELDS,
    candidate,
    plan,
    policy_values,
    profile,
    task_for,
)


def prose_document(*, missing: tuple[str, ...] = (), suffix: str = ""):
    values = policy_values()
    text_parts: list[str] = []
    quotes: dict[str, str] = {}
    for field in POLICY_FIELDS:
        if field in missing:
            continue
        value = values[field]
        display = "、".join(value) if isinstance(value, list) else str(value)
        quote = f"{field}：{display}"
        quotes[field] = quote
        text_parts.append(quote)
    text = "\n".join(text_parts) + suffix
    fields = {
        field: (
            PublicTextField.missing()
            if field in missing
            else PublicTextField(value=values[field], quote=quotes[field])
        )
        for field in POLICY_FIELDS
    }
    source = candidate()
    return bind_public_text(
        source_id=source.source_id,
        url=source.url,
        text=text,
        fields=fields,
    )


class PublicTextAdapterTest(unittest.TestCase):
    def test_number_binding_cannot_select_digits_inside_a_different_number(self):
        for text in ("学费15000元", "差额-1500元", "学费1500.5元"):
            with self.subTest(text=text), self.assertRaises(PublicTextAdapterError):
                bind_public_text(source_id="official-page", url="https://example.edu.cn/fees",
                    text=text, fields={"fee": PublicTextField(value=1500, quote=text)})

    def test_real_public_page_may_contain_registration_links(self):
        text = "2026年招生。报名入口：https://admission.example.edu.cn/apply。咨询电话见官网。"
        document = bind_public_text(source_id="official-page",
            url="https://admission.example.edu.cn/policy", text=text,
            fields={"year": PublicTextField(value=2026, quote="2026年招生")})
        self.assertEqual(document.text, text)
        self.assertEqual(document.fields["year"].value, 2026)

    def test_binds_arbitrary_typed_values_to_exact_public_quotes(self):
        text = "学校性质为公办。2026年招生，专业包括历史学和法学。"
        year_start = text.index("2026年")
        document = bind_public_text(
            source_id="official-page",
            url="https://policy.example.cn/admission.html#notice",
            text=text,
            fields={
                "is_public": PublicTextField(value=True, quote="公办"),
                "year": PublicTextField(
                    value=2026,
                    quote="2026年",
                    start=year_start,
                    end=year_start + len("2026年"),
                ),
                "majors": PublicTextField(
                    value=["历史学", "法学"], quote="专业包括历史学和法学"
                ),
                "tuition": PublicTextField.missing(),
            },
        )

        self.assertEqual(document.source_id, "official-page")
        self.assertEqual(document.url, "https://policy.example.cn/admission.html")
        self.assertEqual(document.text, text)
        self.assertEqual(document.fields["year"].value, 2026)
        self.assertIs(document.fields["tuition"].status, CellStatus.EXACT)
        self.assertIsNone(document.fields["tuition"].value)
        self.assertEqual(document.fields["year"].quote, "2026年")

    def test_rejects_mismatched_or_ambiguous_quote_and_span(self):
        with self.assertRaisesRegex(PublicTextAdapterError, "span.*quote"):
            bind_public_text(
                source_id="official-page",
                url="https://policy.example.cn/admission.html",
                text="2026年招生",
                fields={
                    "year": PublicTextField(
                        value=2026, quote="2025年", start=0, end=5
                    )
                },
            )
        with self.assertRaisesRegex(PublicTextAdapterError, "ambiguous"):
            bind_public_text(
                source_id="official-page",
                url="https://policy.example.cn/admission.html",
                text="公办；公办",
                fields={"is_public": PublicTextField(value=True, quote="公办")},
            )


class PublicTextPathwayTest(unittest.TestCase):
    def test_partial_multi_major_prose_binds_the_display_without_changing_raw_evidence(self):
        # Synthetic public prose reproduces an unsorted multi-major notice.
        majors = ("信息与计算科学", "生物科学", "化学", "历史学", "哲学")
        student = profile()
        query_plan = plan(student)
        source = candidate()
        for raw_majors in (majors, majors[::-1], tuple(sorted(majors))):
            with self.subTest(raw_majors=raw_majors):
                quote = "招生专业包括" + "、".join(raw_majors)
                document = bind_public_text(
                    source_id=source.source_id,
                    url=source.url,
                    text="合成测试高校2026年招生简章。" + quote,
                    fields={
                        "institution": PublicTextField(
                            value="合成测试高校", quote="合成测试高校"
                        ),
                        "year": PublicTextField(value=2026, quote="2026年"),
                        "professional_options": PublicTextField(
                            value=list(raw_majors), quote=quote
                        ),
                    },
                )
                projection = extract_pathway_policy(
                    profile=student, plan=query_plan, task=task_for(query_plan),
                    extraction=document,
                    field_map={name: name for name in document.fields},
                    candidates=(source,),
                )
                observations = bridge_pathway_observations(
                    (bridge_pathway_policy_evidence(projection),),
                    profile=student, plan=query_plan,
                )
                observation = next(item for item in observations if item.title == "强基计划")
                raw_projection = deepcopy(projection.to_dict())
                raw_observation = deepcopy(observation.to_dict())
                self.assertEqual(observation.professional_options, raw_majors)

                result = evaluate_pathways(
                    student, (), rank_scenario=exact_rank(),
                    query_plan=query_plan, observations=observations,
                )

                item = next(item for item in result.items if item.title == "强基计划")
                self.assertEqual(item.professional_options, tuple(sorted(majors)))
                self.assertEqual(item.status, "pending_verification")
                self.assertEqual(item.investment_decision, "观察")
                self.assertIsNone(item.target_rank)
                trail = next(record for record in item.field_evidence
                             if record.field == "professional_options")
                self.assertIs(trail.status, EvidenceStatus.OFFICIAL)
                self.assertEqual(trail.coverage, "complete")
                retained = next(record for record in observation.field_provenance
                                if record.field == "professional_options")
                self.assertEqual(trail.source_ids, retained.source_ids)
                self.assertEqual(trail.locators, retained.locators)
                self.assertEqual(_project_pathway(item).professional_options,
                                 tuple(sorted(majors)))
                self.assertEqual(projection.to_dict(), raw_projection)
                self.assertEqual(observation.to_dict(), raw_observation)
                validate_pathway_evidence_observation(observation, student, query_plan)
                with self.assertRaisesRegex(ValueError, "professional_options field evidence value digest disagrees"):
                    replace(item, professional_options=("伪造专业",))

    def project(self, document):
        student = profile()
        query_plan = plan(student)
        return extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task_for(query_plan),
            extraction=document,
            field_map={name: name for name in POLICY_FIELDS},
            candidates=(candidate(),),
        )

    def test_complete_prose_reaches_an_accepted_replayable_policy(self):
        projection = self.project(prose_document())

        self.assertIs(projection.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(projection.coverage_status, "complete")
        self.assertEqual(projection.professional_options, ("示例专业",))
        self.assertEqual(
            {method for item in projection.field_provenance for method in item.extraction_methods},
            {"host-public-text"},
        )
        replayed = replay_pathway_policy_projection(projection.to_dict())
        self.assertEqual(replayed.to_dict(), projection.to_dict())

    def test_absent_optional_fields_remain_missing_and_make_policy_partial(self):
        projection = self.project(
            prose_document(missing=("professional_options", "fees_and_subsidies"))
        )
        provenance = {item.field: item for item in projection.field_provenance}

        self.assertIs(projection.evidence_status, EvidenceStatus.PARTIAL)
        self.assertEqual(projection.coverage_status, "partial")
        self.assertIsNone(projection.professional_options)
        self.assertIsNone(projection.fees_and_subsidies)
        self.assertIs(provenance["professional_options"].status, EvidenceStatus.MISSING)
        self.assertIn("professional_options:missing", projection.warnings)

    def test_partial_field_map_defaults_every_omitted_policy_field_to_missing(self):
        source = candidate()
        text = "院校：示例高校。省份：湖北。模式：3+1+2。年度：2026年。"
        document = bind_public_text(
            source_id=source.source_id,
            url=source.url,
            text=text,
            fields={
                "school": PublicTextField(value="示例高校", quote="示例高校"),
                "region": PublicTextField(value="湖北", quote="湖北"),
                "mode": PublicTextField(value="3+1+2", quote="3+1+2"),
                "policy_year": PublicTextField(value=2026, quote="2026年"),
            },
        )
        student = profile()
        query_plan = plan(student)
        projection = extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task_for(query_plan),
            extraction=document,
            field_map={
                "institution": "school",
                "province": "region",
                "subject_mode": "mode",
                "year": "policy_year",
            },
            candidates=(source,),
        )
        provenance = {item.field: item for item in projection.field_provenance}

        self.assertIs(projection.evidence_status, EvidenceStatus.PARTIAL)
        self.assertIs(
            provenance["eligibility_requirements"].status,
            EvidenceStatus.MISSING,
        )
        self.assertIn("eligibility_requirements:missing", projection.warnings)

    def test_partial_official_prose_retains_known_fields_with_citations(self):
        source = candidate(
            source_id="hust-strong-foundation-2026",
            publisher="华中科技大学",
            host="zs.hust.edu.cn",
        )
        text = (
            "华中科技大学2026年强基计划招生简章\n"
            "招生专业包括哲学，考生应以报名系统公布信息为准。"
        )
        document = bind_public_text(
            source_id=source.source_id,
            url=source.url,
            text=text,
            fields={
                "institution": PublicTextField(
                    value="华中科技大学", quote="华中科技大学"
                ),
                "year": PublicTextField(value=2026, quote="2026年"),
                "professional_options": PublicTextField(
                    value=["哲学"], quote="招生专业包括哲学"
                ),
            },
        )
        student = profile()
        query_plan = plan(student)
        projection = extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task_for(query_plan),
            extraction=document,
            field_map={
                "institution": "institution",
                "year": "year",
                "professional_options": "professional_options",
            },
            candidates=(source,),
        )
        observations = bridge_pathway_observations(
            (bridge_pathway_policy_evidence(projection),),
            profile=student,
            plan=query_plan,
        )
        observation = next(
            item for item in observations if item.title == "强基计划"
        )
        result = evaluate_pathways(
            student,
            (),
            rank_scenario=exact_rank(),
            query_plan=query_plan,
            observations=observations,
        )

        self.assertIs(projection.evidence_status, EvidenceStatus.PARTIAL)
        item = next(item for item in result.items if item.title == "强基计划")
        self.assertEqual(item.institution, "华中科技大学")
        self.assertEqual(item.professional_options, ("哲学",))
        self.assertEqual(item.status, "pending_verification")
        self.assertEqual(item.eligibility, "pending_verification")
        self.assertEqual(item.investment_decision, "观察")
        self.assertEqual(item.qualification_status, "待核验")
        evidence = {record.field: record for record in item.field_evidence}
        for field in ("institution", "professional_options"):
            with self.subTest(field=field):
                self.assertIs(evidence[field].status, EvidenceStatus.OFFICIAL)
                self.assertEqual(evidence[field].coverage, "complete")
                self.assertEqual(evidence[field].source_ids, (source.source_id,))
                self.assertEqual(
                    evidence[field].extraction_methods, ("host-public-text",)
                )
                self.assertTrue(
                    all(
                        locator.startswith("text[")
                        for locator in evidence[field].locators
                    )
                )

        report_item = _project_pathway(item)
        self.assertEqual(report_item.institution, "华中科技大学")
        self.assertEqual(report_item.professional_options, ("哲学",))
        audit = "\n".join(pathway_field_evidence_lines(report_item))
        self.assertIn(
            "字段：institution（院校）；证据状态：官方；覆盖：完整", audit
        )
        self.assertIn(
            "字段：professional_options（专业选项）；证据状态：官方；覆盖：完整",
            audit,
        )
        self.assertIn(source.source_id, audit)
        self.assertIn("text[", audit)

        tampered = deepcopy(observation)
        object.__setattr__(tampered, "institution", "其他高校")
        with self.assertRaises(PathwayBridgeError):
            validate_pathway_evidence_observation(tampered, student, query_plan)

    def test_replay_rejects_raw_text_or_field_span_changes(self):
        projection = self.project(prose_document())
        changed_text = deepcopy(projection.to_dict())
        changed_text["input_projection"]["documents"][0]["text"] += "\n后来修改"
        changed_span = deepcopy(projection.to_dict())
        changed_span["input_projection"]["documents"][0]["fields"]["year"]["start"] += 1

        for changed in (changed_text, changed_span):
            with self.subTest(change=changed):
                with self.assertRaises(PathwayExtractionError):
                    replay_pathway_policy_projection(changed)

        revised = self.project(prose_document(suffix="\n后来修改"))
        self.assertNotEqual(revised.digest, projection.digest)
        self.assertNotEqual(revised.input_digest, projection.input_digest)

    def test_pathway_rejects_a_prose_document_from_a_different_source_url(self):
        document = prose_document()
        different_source = candidate(host="other-policy.example.cn")
        student = profile()
        query_plan = plan(student)

        with self.assertRaisesRegex(PathwayExtractionError, "source provenance"):
            extract_pathway_policy(
                profile=student,
                plan=query_plan,
                task=task_for(query_plan),
                extraction=document,
                field_map={name: name for name in POLICY_FIELDS},
                candidates=(different_source,),
            )


if __name__ == "__main__":
    unittest.main()
