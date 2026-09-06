from __future__ import annotations

from collections.abc import Iterator, Mapping
import contextlib
from dataclasses import FrozenInstanceError, replace
from enum import Enum
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import traceback
import unittest
import urllib.request
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile

import scripts.adapters.spreadsheet as spreadsheet_adapter
from scripts.adapters import (
    CellStatus,
    ColumnMapping,
    ExtractedCoverage,
    ExtractedRow,
    ExtractedTable,
    MappingError,
    StructuredFileError,
    StructuredValidationError,
)
from scripts.adapters.html_table import HtmlStructureError, extract_html_table
from scripts.adapters.spreadsheet import (
    SpreadsheetDependencyError,
    extract_spreadsheet,
)
from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceFact,
    EvidenceStatus,
    SourceCandidate,
    SourceTier,
)
from scripts.evidence import EvidenceStore
from scripts.validate_evidence import validate_bundle_snapshot


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "replay" / "structured"
HTML_FIXTURE = (FIXTURES / "score-table.html").resolve()
XLSX_FIXTURE = (FIXTURES / "admission.xlsx").resolve()


class _RepeatedCanonicalMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        return "分数"

    def __iter__(self) -> Iterator[str]:
        return iter(("score",))

    def __len__(self) -> int:
        return 1

    def items(self):
        return (("score", "分数"), ("score", "总分"))


class ContractTest(unittest.TestCase):
    def test_public_locator_contract_is_shared_by_direct_adapter_constructors(self):
        from scripts.adapters import PublicLocatorError, validate_public_locator

        safe = (
            "物理类!A2:F2",
            "sheet[C3:F3]",
            "page[1]/image[page-1]/bbox[1,2,3,4]",
            "ordinary-safe-id-2026",
            "fact[138001380000]",
        )
        for locator in safe:
            with self.subTest(safe_locator=locator):
                self.assertEqual(locator, validate_public_locator(locator))
        unsafe = (
            "C:\\private\\scores.xlsx",
            "sheet[C:relative-sheet]",
            "sheet:C:relative-sheet",
            "//server/share/scores.xlsx",
            "source[/home/user/scores.html]",
            "sheet[/opt/data/scores.xlsx]",
            "source[https://private.example.test/item]",
            "sheet[%TEMP%]",
            "sheet[$HOME]",
            "sheet[${HOME}]",
            "student[name@example.test]",
            "student-138-0013-8000",
            "student 138 0013 8000",
            "student １３８　００１３　８０００",
            "office-010-12345678",
            "row-C:private.txt",
            "row-Ｃ：private.txt",
        )
        row = ExtractedRow(
            values={"score": 650},
            cell_status={"score": CellStatus.EXACT},
            location="table[1]/tbody/tr[1]",
            confidence=1,
        )
        table = ExtractedTable(
            table_id="table[1]",
            caption="合成表",
            sheet=None,
            rows=(row,),
            coverage=ExtractedCoverage(lower_score=650, upper_score=650),
            warnings=(),
            extraction_method="html-table",
        )
        for locator in unsafe:
            with self.subTest(locator=locator):
                with self.assertRaises(PublicLocatorError) as raised:
                    validate_public_locator(locator)
                self.assertNotIn(locator, str(raised.exception))
                with self.assertRaises(PublicLocatorError) as direct_row_error:
                    ExtractedRow(
                        values={"score": 650},
                        cell_status={"score": CellStatus.EXACT},
                        location=locator,
                        confidence=1,
                    )
                self.assertNotIn(locator, str(direct_row_error.exception))
                with self.assertRaises(PublicLocatorError) as direct_table_error:
                    ExtractedTable(
                        table_id=locator,
                        caption="合成表",
                        sheet=None,
                        rows=(row,),
                        coverage=ExtractedCoverage(
                            lower_score=650, upper_score=650
                        ),
                        warnings=(),
                        extraction_method="html-table",
                    )
                self.assertNotIn(locator, str(direct_table_error.exception))
                with self.assertRaises(PublicLocatorError) as row_error:
                    replace(row, location=locator)
                self.assertNotIn(locator, str(row_error.exception))
                with self.assertRaises(PublicLocatorError) as table_error:
                    replace(table, table_id=locator)
                self.assertNotIn(locator, str(table_error.exception))

    def test_coverage_normalizes_mathematical_integers_and_rejects_invalid_bounds(self):
        coverage = ExtractedCoverage(630.0, 650, 100.0, 300)
        self.assertEqual(
            coverage.to_dict(),
            {"lower_score": 630, "upper_score": 650, "lower_rank": 100, "upper_rank": 300},
        )
        for value in (True, 1.5, math.nan, math.inf):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    ExtractedCoverage(lower_score=value)
        with self.assertRaises(ValueError):
            ExtractedCoverage(lower_score=651, upper_score=650)
        with self.assertRaises(ValueError):
            ExtractedCoverage(lower_rank=301, upper_rank=300)
        with self.assertRaises(TypeError):
            ExtractedCoverage(min_score=630)

    def test_rows_are_deep_snapshots_and_json_safe(self):
        raw_values = {"score": 650, "meta": {"tags": ["synthetic"]}}
        raw_status = {"score": "exact", "meta": CellStatus.EXACT}
        raw_warnings = ["kept-in-order"]
        row = ExtractedRow(
            values=raw_values,
            cell_status=raw_status,
            location="table[1]/tbody/tr[1]",
            confidence=1,
            warnings=raw_warnings,
        )
        raw_values["score"] = 1
        raw_values["meta"]["tags"].append("mutated")
        raw_status["score"] = "invalid"
        raw_warnings.append("mutated")
        self.assertEqual(row.values["score"], 650)
        self.assertEqual(row.values["meta"]["tags"], ("synthetic",))
        self.assertEqual(row.cell_status["score"], CellStatus.EXACT)
        self.assertEqual(row.warnings, ("kept-in-order",))
        with self.assertRaises(TypeError):
            row.values["score"] = 2
        with self.assertRaises(FrozenInstanceError):
            row.location = "changed"
        json.dumps(row.to_dict(), allow_nan=False, ensure_ascii=False)

    def test_direct_construction_and_replace_revalidate_every_invariant(self):
        row = ExtractedRow(
            values={"score": 650},
            cell_status={"score": "exact"},
            location="table[1]/tbody/tr[1]",
            confidence=1.0,
        )
        with self.assertRaises(ValueError):
            replace(row, cell_status={"score": "official"})
        with self.assertRaises(ValueError):
            replace(row, cell_status={"other": "exact"})
        with self.assertRaises(ValueError):
            replace(row, confidence=math.nan)
        with self.assertRaises(TypeError):
            replace(row, values={"score": object()})

    def test_table_snapshots_rows_and_never_serializes_a_local_path(self):
        rows = [
            ExtractedRow(
                values={"score": 650},
                cell_status={"score": "exact"},
                location="table[1]/tbody/tr[1]",
                confidence=1,
            )
        ]
        table = ExtractedTable(
            table_id="table[1]",
            caption="合成表",
            sheet=None,
            rows=rows,
            coverage=ExtractedCoverage(lower_score=650, upper_score=650),
            warnings=["synthetic"],
            extraction_method="html-table",
        )
        rows.clear()
        self.assertEqual(len(table.rows), 1)
        serialized = json.dumps(table.to_dict(), allow_nan=False, ensure_ascii=False)
        self.assertNotIn(str(ROOT), serialized)
        with self.assertRaises(ValueError):
            replace(table, extraction_method="")

    def test_mapping_is_an_immutable_deep_snapshot_with_explicit_roles_and_scale(self):
        aliases = ["最低分", "投档分"]
        columns = {"min_score": aliases, "min_rank": "最低位次"}
        roles = {"min_score": "score", "min_rank": "rank"}
        mapping = ColumnMapping(columns, roles=roles, score_scale=[0, 750])
        aliases.append("changed")
        columns["other"] = "其他"
        roles["min_score"] = "rank"
        self.assertEqual(mapping["min_score"], ("最低分", "投档分"))
        self.assertEqual(tuple(mapping), ("min_score", "min_rank"))
        self.assertEqual(mapping.roles["min_score"], "score")
        self.assertEqual(mapping.score_scale, (0, 750))
        with self.assertRaises(TypeError):
            mapping.columns["other"] = ("其他",)

    def test_mapping_rejects_empty_duplicate_whitespace_and_repeated_fields(self):
        invalid = (
            {"score": []},
            {" score": "分数"},
            {"score": " 分数"},
            {"score": ("分数", "分数")},
            {"score": "值", "rank": "值"},
            _RepeatedCanonicalMapping(),
        )
        for columns in invalid:
            with self.subTest(columns=columns):
                with self.assertRaises((TypeError, ValueError)):
                    ColumnMapping(columns)
        with self.assertRaises((TypeError, ValueError)):
            ColumnMapping({"score": "分数"}, score_scale={"lower": 0, "upper": 750})

    def test_enum_values_are_recursively_frozen_and_must_resolve_to_json_safe_data(self):
        class MutablePayload(Enum):
            SYNTHETIC = ["original", {"score": 650}]

        class UnsafePayload(Enum):
            OBJECT = object()

        row = ExtractedRow(
            values={"payload": MutablePayload.SYNTHETIC},
            cell_status={"payload": CellStatus.EXACT},
            location="table[1]/tbody/tr[1]",
            confidence=1,
        )
        MutablePayload.SYNTHETIC.value.append("mutated")
        self.assertEqual(row.values["payload"], ("original", {"score": 650}))
        serialized = row.to_dict()
        self.assertEqual(serialized["values"]["payload"], ["original", {"score": 650}])
        json.dumps(serialized, allow_nan=False, ensure_ascii=False)
        with self.assertRaises(TypeError):
            ExtractedRow(
                values={"payload": UnsafePayload.OBJECT},
                cell_status={"payload": CellStatus.EXACT},
                location="table[1]/tbody/tr[1]",
                confidence=1,
            )


class HtmlAdapterTest(unittest.TestCase):
    @staticmethod
    def mapping() -> ColumnMapping:
        return ColumnMapping(
            {"score": "分数", "rank": ("累计位次", "位次"), "note": "说明"},
            roles={"score": "score", "rank": "rank"},
            score_scale=(0, 750),
        )

    def _extract(self, **overrides) -> ExtractedTable:
        arguments = {
            "table_index": 1,
            "expected_caption": "合成分数位次表",
            "mapping": self.mapping(),
        }
        arguments.update(overrides)
        return extract_html_table(HTML_FIXTURE, **arguments)

    def _write_html(self, directory: Path, text: str, name: str = "input.html") -> Path:
        path = (directory / name).resolve()
        path.write_text(text, encoding="utf-8")
        return path

    def test_explicit_table_caption_mapping_location_values_and_coverage(self):
        table = self._extract()
        self.assertEqual(table.table_id, "table[1]")
        self.assertEqual(table.caption, "合成分数位次表")
        self.assertIsNone(table.sheet)
        self.assertEqual(table.extraction_method, "html-table")
        self.assertEqual(table.rows[0].location, "table[1]/tbody/tr[1]")
        self.assertEqual(table.rows[0].values, {"score": 650, "rank": 100, "note": "纯合成甲"})
        self.assertEqual(table.rows[0].cell_status["score"], CellStatus.EXACT)
        self.assertEqual(
            table.coverage,
            ExtractedCoverage(lower_score=630, upper_score=650, lower_rank=100, upper_rank=300),
        )
        self.assertEqual(len(table.rows), 3, "script payload must not become a table or row")

    def test_wrong_index_caption_and_unrelated_table_fail_without_auto_selection(self):
        with self.assertRaises(StructuredValidationError):
            self._extract(table_index=3)
        with self.assertRaises(StructuredValidationError):
            self._extract(expected_caption="别的标题")
        with self.assertRaises(MappingError):
            self._extract(table_index=2, expected_caption="无关表")

    def test_missing_ambiguous_duplicate_and_whitespace_only_headers_are_rejected(self):
        with self.assertRaises(MappingError):
            self._extract(mapping={"score": "不存在"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases = {
                "duplicate.html": "<table><caption>x</caption><tr><th>分数</th><th>分数</th></tr></table>",
                "ambiguous.html": "<table><caption>x</caption><tr><th>分数</th><th>总分</th></tr></table>",
                "whitespace.html": "<table><caption>x</caption><tr><th> 分数 </th></tr></table>",
            }
            mappings = {
                "duplicate.html": {"score": "分数"},
                "ambiguous.html": {"score": ("分数", "总分")},
                "whitespace.html": {"score": "分数"},
            }
            for name, source in cases.items():
                path = self._write_html(directory, source, name)
                with self.subTest(name=name):
                    with self.assertRaises(MappingError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping=mappings[name])

    def test_nonempty_data_before_explicit_header_fails_closed(self):
        from scripts.adapters.html_table import HtmlStructureError

        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_html(
                Path(temporary),
                "<table><caption>x</caption>"
                "<tr><td>650</td><td>100</td></tr>"
                "<tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>640</td><td>200</td></tr></table>",
                "pre-header-data.html",
            )
            with self.assertRaises(HtmlStructureError):
                extract_html_table(
                    path,
                    table_index=1,
                    expected_caption="x",
                    mapping=ColumnMapping(
                        {"score": "分数", "rank": "位次"},
                        roles={"score": "score", "rank": "rank"},
                    ),
                )

    def test_malformed_table_fails_and_truncated_or_empty_rows_warn(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            malformed = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>名称</th></tr><tr><td>未闭合",
                "malformed.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(malformed, table_index=1, expected_caption="x", mapping={"name": "名称"})

            truncated = self._write_html(
                directory,
                "<table><caption>x</caption><thead><tr><th>名称</th><th>备注</th></tr></thead>"
                "<tbody><tr><td>合成行</td></tr><tr><td></td><td></td></tr></tbody></table>",
                "truncated.html",
            )
            table = extract_html_table(
                truncated,
                table_index=1,
                expected_caption="x",
                mapping={"name": "名称", "note": "备注"},
            )
            self.assertEqual(table.rows[0].cell_status["note"], CellStatus.EMPTY)
            self.assertIn("truncated-row", table.rows[0].warnings)
            self.assertEqual(table.rows[1].cell_status["name"], CellStatus.EMPTY)
            self.assertIn("empty-required-cell:name", table.rows[1].warnings)

            empty = self._write_html(
                directory,
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead><tbody></tbody></table>",
                "empty.html",
            )
            empty_table = extract_html_table(empty, table_index=1, expected_caption="x", mapping={"name": "名称"})
            self.assertEqual(empty_table.rows, ())
            self.assertIn("empty-table", empty_table.warnings)

    def test_numeric_scale_and_monotonicity_fail_closed_without_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mapping = ColumnMapping(
                {"score": "分数", "rank": "位次"},
                roles={"score": "score", "rank": "rank"},
                score_scale=(0, 750),
            )
            nonmonotonic = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>100</td></tr><tr><td>640</td><td>200</td></tr>"
                "<tr><td>645</td><td>300</td></tr></table>",
                "nonmonotonic.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(nonmonotonic, table_index=1, expected_caption="x", mapping=mapping)
            outside = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>751</td><td>1</td></tr></table>",
                "outside.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(outside, table_index=1, expected_caption="x", mapping=mapping)
            invalid_rank = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>1.5</td></tr></table>",
                "rank.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(invalid_rank, table_index=1, expected_caption="x", mapping=mapping)
            for name, score in (("nan.html", "NaN"), ("inf.html", "Infinity")):
                invalid = self._write_html(
                    directory,
                    "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                    f"<tr><td>{score}</td><td>1</td></tr></table>",
                    name,
                )
                with self.subTest(name=name):
                    with self.assertRaises(StructuredValidationError):
                        extract_html_table(invalid, table_index=1, expected_caption="x", mapping=mapping)
            duplicate = self._write_html(
                directory,
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>1</td></tr><tr><td>650</td><td>1</td></tr></table>",
                "duplicate-row.html",
            )
            with self.assertRaises(StructuredValidationError):
                extract_html_table(duplicate, table_index=1, expected_caption="x", mapping=mapping)


class _AdmissionEvidenceBridgeContractMixin:
    @staticmethod
    def _candidate():
        return SourceCandidate(
            source_id="html-source",
            url="https://www.hljea.org.cn/synthetic-admission.html",
            publisher="黑龙江省招生考试院",
            tier=SourceTier.A,
            published_at="2026-06-25",
            retrieved_at="2026-08-24T00:00:00Z",
            content_hash="sha256:" + "a" * 64,
            citation_root="https://www.hljea.org.cn/",
            summary="合成普通批投档表",
        )

    @staticmethod
    def _inputs():
        from scripts.contracts import RecommendationProfile
        from scripts.province_registry import discover_provinces
        from scripts.query_plan import _build_query_plan_legacy, load_province_catalog
        from scripts.validate_data import ValidatedAdmissionRow

        configs = discover_provinces(ROOT / "tests" / "fixtures" / "provinces")
        config = replace(configs["演示甲省"], province="黑龙江")
        profile = RecommendationProfile(
            rank=1100,
            target_province="黑龙江",
            subject_group="物理",
            secondary_subjects=frozenset({"化学", "地理"}),
        )
        plan = _build_query_plan_legacy(
            profile, config, 2026, catalog=load_province_catalog()
        )
        task = next(
            item
            for item in plan.tasks
            if item.kind == "batch_admission"
            and item.year == 2026
            and item.target_name == "普通批"
        )
        dataset_row = ValidatedAdmissionRow.from_mapping(
            {
                "year": 2026,
                "province": "黑龙江",
                "subject_group": "物理",
                "school_code": "SYN312A",
                "school_name": "虚构甲大学",
                "program_group": "第01组",
                "major_group_name": "计算机类",
                "min_score": 645,
                "min_rank": 1100,
                "remarks": "",
            }
        )
        adapter_row = ExtractedRow(
            values={
                "school_code": "SYN312A",
                "school_name": "虚构甲大学",
                "program_group": "第01组",
                "min_score": 645,
                "min_rank": 1100,
            },
            cell_status={
                "school_code": CellStatus.EXACT,
                "school_name": CellStatus.EXACT,
                "program_group": CellStatus.EXACT,
                "min_score": CellStatus.EXACT,
                "min_rank": CellStatus.EXACT,
            },
            location="table[1]/tbody/tr[1]",
            confidence=1,
        )
        table = ExtractedTable(
            table_id="table[1]",
            caption="普通批投档",
            sheet=None,
            rows=(adapter_row,),
            coverage=ExtractedCoverage(
                lower_score=645,
                upper_score=645,
                lower_rank=1100,
                upper_rank=1100,
            ),
            warnings=(),
            extraction_method="html-table",
        )
        return task, dataset_row, adapter_row, table

    def _bridge(self, *, coverage_status=EvidenceStatus.PARTIAL):
        from scripts.adapters.admission_bridge import bridge_admission_evidence

        task, dataset_row, adapter_row, table = self._inputs()
        return bridge_admission_evidence(
            table=table,
            adapter_row=adapter_row,
            task=task,
            dataset_row=dataset_row,
            fact_id="html-admission-row",
            candidates=(self._candidate(),),
            coverage_status=coverage_status,
        )

    def test_validator_rejects_coverage_escalation_even_after_visible_digest_rehash(self):
        from scripts.adapters.admission_bridge import (
            AdmissionBridgeError,
            validate_admission_evidence_bridge,
        )

        bridged = self._bridge(coverage_status=EvidenceStatus.PARTIAL)
        object.__setattr__(bridged, "coverage_status", EvidenceStatus.OFFICIAL)

        # An attacker can recompute every public digest.  The factory-owned
        # origin must remain the authority rather than the visible enum/hash.
        visible = bridged.to_dict()
        for name in ("origin_digest", "bridge_digest"):
            self.assertTrue(hasattr(bridged, name), f"missing {name}")
            body = {key: value for key, value in visible.items() if key != name}
            forged = "sha256:" + hashlib.sha256(
                json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            object.__setattr__(bridged, name, forged)

        with self.assertRaises(AdmissionBridgeError):
            validate_admission_evidence_bridge(bridged)

    def test_validator_replays_and_binds_the_complete_query_task_projection(self):
        from copy import copy

        from scripts.adapters.admission_bridge import (
            AdmissionBridgeError,
            validate_admission_evidence_bridge,
        )

        mutations = {
            "year": 2025,
            "kind": "score_table",
            "target_name": "伪造批次",
            "query_variants": ("黑龙江 2026 物理 黑龙江省招生考试院 伪造普通批",),
            "source_policy_id": "forged-source-policy",
            "source_policy_version": "9.9",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                bridged = self._bridge()
                detached = copy(bridged.task)
                object.__setattr__(detached, field, value)
                object.__setattr__(bridged, "task", detached)
                with self.assertRaises(AdmissionBridgeError):
                    validate_admission_evidence_bridge(bridged)

        bridged = self._bridge()
        self.assertEqual(bridged.to_dict()["task"], bridged.task.to_dict())

    def test_planning_receipt_cannot_promote_mutated_partial_coverage(self):
        from copy import copy

        from scripts.adapters.admission_bridge import (
            AdmissionBridgeError,
            bridge_admission_evidence,
        )
        from scripts.planning_session import (
            PlanningSessionInputError,
            build_task_evidence_outcome,
        )
        from scripts.validate_data import ValidatedAdmissionRow
        from tests.test_pathway_evidence_bridge import profile, plan

        student = profile()
        query_plan = plan(student)
        task = next(
            item
            for item in query_plan.tasks
            if item.kind == "batch_admission"
            and item.target_name == "普通批"
        )
        _legacy_task, dataset_row, adapter_row, table = self._inputs()
        contextual_row = ValidatedAdmissionRow.from_mapping(
            {
                **dataset_row.to_dict(),
                "year": task.year,
                "province": task.province,
                "subject_group": task.subject_group,
            }
        )
        bridge = bridge_admission_evidence(
            table=table,
            adapter_row=adapter_row,
            task=copy(task),
            dataset_row=contextual_row,
            fact_id="receipt-admission-row",
            candidates=(self._candidate(),),
            coverage_status=EvidenceStatus.PARTIAL,
        )
        object.__setattr__(bridge, "coverage_status", EvidenceStatus.OFFICIAL)

        with self.assertRaisesRegex(
            PlanningSessionInputError,
            "admission evidence bridge failed canonical replay",
        ):
            build_task_evidence_outcome(
                student, query_plan, task, (bridge,)
            )

    def test_bridge_reuses_public_whole_row_hash_and_keeps_coverage_status_separate(self):
        from scripts.adapters.admission_bridge import (
            bridge_admission_evidence,
            validate_admission_evidence_bridge,
        )
        from scripts.validate_data import admission_row_hash

        task, dataset_row, adapter_row, table = self._inputs()
        candidate = self._candidate()
        with mock.patch(
            "scripts.adapters.admission_bridge.admission_row_hash",
            wraps=admission_row_hash,
        ) as hasher:
            bridged = bridge_admission_evidence(
                table=table,
                adapter_row=adapter_row,
                task=task,
                dataset_row=dataset_row,
                fact_id="html-admission-row",
                candidates=(candidate,),
                coverage_status=EvidenceStatus.PARTIAL,
            )
        hasher.assert_called_once_with(dataset_row)
        self.assertIs(validate_admission_evidence_bridge(bridged), bridged)
        self.assertEqual(bridged.admission_row_hash, admission_row_hash(dataset_row))
        self.assertEqual(bridged.coverage_status, EvidenceStatus.PARTIAL)
        self.assertEqual(bridged.fact.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(bridged.fact.value["coverage_status"], "partial")
        self.assertEqual(
            bridged.fact.value["row_hash"], bridged.admission_row_hash
        )
        self.assertEqual(
            bridged.fact.value["dataset_row"], dataset_row.to_dict()
        )
        with self.assertRaises(FrozenInstanceError):
            bridged.admission_row_hash = "sha256:" + "0" * 64
        with self.assertRaises(TypeError):
            replace(bridged, admission_row_hash="sha256:" + "0" * 64)
        detached_value = bridged.fact.value
        detached_value["dataset_row"]["school_name"] = "篡改大学"
        self.assertEqual(
            bridged.fact.value["dataset_row"], dataset_row.to_dict()
        )
        object.__setattr__(candidate, "summary", "篡改来源")
        self.assertEqual(
            bridged.to_dict()["sources"][0]["summary"], "合成普通批投档表"
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(
                Path(temporary).resolve(), CapabilityReport(CapabilityTier.OFFLINE)
            )
            store.add_candidate(candidate)
            bridged.persist(store)
            store.finalize()
            validation = validate_bundle_snapshot(store.session_path)
        self.assertEqual(validation.issues, ())
        self.assertIsNotNone(validation.snapshot)
        fact = validation.snapshot.facts[0].to_dict()
        self.assertEqual(fact["value"]["row_hash"], admission_row_hash(dataset_row))
        self.assertEqual(fact["value"]["dataset_row"], dataset_row.to_dict())
        self.assertEqual(fact["value"]["coverage_status"], "partial")
        self.assertEqual(fact["status"], "official")

    def test_optional_school_decision_metadata_must_be_exactly_observed_by_adapter(self):
        from scripts.adapters.admission_bridge import (
            AdmissionBridgeError,
            bridge_admission_evidence,
            validate_admission_evidence_bridge,
        )
        from scripts.validate_data import ValidatedAdmissionRow

        task, dataset_row, adapter_row, table = self._inputs()
        metadata = {
            "city_location": "武汉",
            "school_province": "湖北",
            "majors_in_group": ("人工智能", "计算机科学与技术"),
            "institution_type": "public",
            "affordable_for": ("limited", "moderate"),
            "adjustment_required": False,
        }
        authenticated_row = ValidatedAdmissionRow.from_mapping(
            {**dataset_row.to_dict(), **metadata}
        )
        arguments = {
            "table": table,
            "adapter_row": adapter_row,
            "task": task,
            "dataset_row": authenticated_row,
            "fact_id": "observed-admission-row",
            "candidates": (self._candidate(),),
            "coverage_status": EvidenceStatus.PARTIAL,
        }

        with self.assertRaises(AdmissionBridgeError):
            bridge_admission_evidence(**arguments)

        observed_adapter = replace(
            adapter_row,
            values={**dict(adapter_row.values), **metadata},
            cell_status={
                **dict(adapter_row.cell_status),
                **{name: CellStatus.EXACT for name in metadata},
            },
        )
        observed_table = replace(table, rows=(observed_adapter,))
        bridged = bridge_admission_evidence(
            **{
                **arguments,
                "table": observed_table,
                "adapter_row": observed_adapter,
            }
        )
        self.assertIs(validate_admission_evidence_bridge(bridged), bridged)
        self.assertEqual(
            {name: bridged.fact.value["dataset_row"][name] for name in metadata},
            metadata,
        )
        self.assertEqual(bridged.fact.status, EvidenceStatus.OFFICIAL)

        mismatched_adapter = replace(
            observed_adapter,
            values={**dict(observed_adapter.values), "city_location": "上海"},
        )
        with self.assertRaises(AdmissionBridgeError):
            bridge_admission_evidence(
                **{
                    **arguments,
                    "table": replace(table, rows=(mismatched_adapter,)),
                    "adapter_row": mismatched_adapter,
                }
            )

    def test_bridge_rejects_detached_nonexact_or_context_mismatched_rows(self):
        from scripts.adapters.admission_bridge import (
            AdmissionBridgeError,
            bridge_admission_evidence,
        )
        from scripts.contracts import RecommendationProfile
        from scripts.province_registry import discover_provinces
        from scripts.query_plan import _build_query_plan_legacy, load_province_catalog

        task, dataset_row, adapter_row, table = self._inputs()
        arguments = {
            "table": table,
            "adapter_row": adapter_row,
            "task": task,
            "dataset_row": dataset_row,
            "fact_id": "admission-row",
            "candidates": (self._candidate(),),
            "coverage_status": EvidenceStatus.PARTIAL,
        }
        detached = replace(adapter_row)
        uncertain = replace(
            adapter_row,
            cell_status={**adapter_row.cell_status, "min_rank": CellStatus.UNCERTAIN},
        )
        config = replace(
            discover_provinces(ROOT / "tests" / "fixtures" / "provinces")[
                "演示甲省"
            ],
            province="黑龙江",
        )
        wrong_task = next(
            item
            for item in _build_query_plan_legacy(
                RecommendationProfile(
                    rank=1100,
                    target_province="黑龙江",
                    subject_group="物理",
                    secondary_subjects=frozenset({"化学", "地理"}),
                ),
                config,
                2026,
                catalog=load_province_catalog(),
            ).tasks
            if item.kind == "score_table"
        )
        for changes in (
            {"adapter_row": detached},
            {"table": replace(table, rows=(uncertain,)), "adapter_row": uncertain},
            {"task": wrong_task},
        ):
            with self.subTest(changes=tuple(changes)), self.assertRaises(
                AdmissionBridgeError
            ):
                bridge_admission_evidence(**{**arguments, **changes})


class AdmissionEvidenceBridgeContractTest(
    _AdmissionEvidenceBridgeContractMixin, unittest.TestCase
):
    def test_package_and_flat_imports_are_lazy_and_perform_no_io(self):
        cases = (
            (ROOT, "scripts.adapters.admission_bridge"),
            (ROOT / "scripts", "adapters.admission_bridge"),
        )
        for search_root, module_name in cases:
            with self.subTest(module=module_name):
                code = f"""
import pathlib, socket, sys
sys.path.insert(0, {str(search_root)!r})
def blocked(*args, **kwargs):
    raise AssertionError('I/O during import')
pathlib.Path.open = blocked
pathlib.Path.read_bytes = blocked
pathlib.Path.read_text = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
__import__({module_name!r})
print('ok')
"""
                completed = subprocess.run(
                    [sys.executable, "-I", "-S", "-c", code],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(completed.stdout.strip(), "ok")


class HtmlAdapterStructureTest(unittest.TestCase):
    @staticmethod
    def _write_html(directory: Path, text: str, name: str = "input.html") -> Path:
        path = (directory / name).resolve()
        path.write_text(text, encoding="utf-8")
        return path

    def test_masked_numeric_cells_remain_nonexact_and_do_not_fabricate_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_html(
                Path(temporary),
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td>650以上</td><td>前100</td></tr></table>",
            )
            table = extract_html_table(
                path,
                table_index=1,
                expected_caption="x",
                mapping=ColumnMapping(
                    {"score": "分数", "rank": "位次"},
                    roles={"score": "score", "rank": "rank"},
                    score_scale=(0, 750),
                ),
            )
            self.assertIsNone(table.rows[0].values["score"])
            self.assertEqual(table.rows[0].cell_status["score"], CellStatus.MASKED)
            self.assertEqual(table.coverage, ExtractedCoverage())

    def test_rowspan_or_colspan_never_falls_through_to_physical_column_indexes(self):
        cases = {
            "header-colspan.html": (
                "<table><caption>x</caption><tr><th colspan='2'>分数</th><th>位次</th></tr>"
                "<tr><td>650</td><td>100</td><td>注</td></tr></table>"
            ),
            "data-rowspan.html": (
                "<table><caption>x</caption><tr><th>分数</th><th>位次</th></tr>"
                "<tr><td rowspan='2'>650</td><td>100</td></tr>"
                "<tr><td>200</td></tr></table>"
            ),
        }
        mapping = ColumnMapping(
            {"score": "分数", "rank": "位次"},
            roles={"score": "score", "rank": "rank"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    with self.assertRaises(HtmlStructureError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping=mapping)

    def test_selected_table_strict_grammar_rejects_review_malformed_pocs(self):
        cases = {
            "stray-close.html": (
                "<table><caption>x</caption><tr><th>名称</th></td></tr>"
                "<tr><td>合成</td></tr></table>",
                "x",
            ),
            "duplicate-caption.html": (
                "<table><caption>x</caption><caption>y</caption>"
                "<tr><th>名称</th></tr><tr><td>合成</td></tr></table>",
                "xy",
            ),
            "unclosed-caption.html": (
                "<table><caption>x<tr><th>名称</th></tr><tr><td>合成</td></tr></table>",
                "x",
            ),
            "unclosed-table.html": (
                "<table><caption>x</caption><tr><th>名称</th></tr><tr><td>合成</td></tr>",
                "x",
            ),
            "thead-inside-tr.html": (
                "<table><caption>x</caption><tr><thead><th>名称</th></thead></tr>"
                "<tr><td>合成</td></tr></table>",
                "x",
            ),
            "tr-inside-caption.html": (
                "<table><caption>x<tr><th>名称</th></tr></caption>"
                "<tr><td>合成</td></tr></table>",
                "x",
            ),
            "tbody-inside-tr.html": (
                "<table><caption>x</caption><tr><tbody><th>名称</th></tbody></tr>"
                "<tr><td>合成</td></tr></table>",
                "x",
            ),
            "td-outside-tr.html": (
                "<table><caption>x</caption><tbody><td>游离</td>"
                "<tr><th>名称</th></tr><tr><td>合成</td></tr></tbody></table>",
                "x",
            ),
            "th-outside-tr.html": (
                "<table><caption>x</caption><thead><th>游离</th>"
                "<tr><th>名称</th></tr></thead><tbody><tr><td>合成</td></tr></tbody></table>",
                "x",
            ),
            "wrapped-tr.html": (
                "<table><caption>x</caption><div><tr><th>名称</th></tr></div>"
                "<tr><td>合成</td></tr></table>",
                "x",
            ),
            "wrapped-cell.html": (
                "<table><caption>x</caption><tr><div><th>名称</th></div></tr>"
                "<tr><td>合成</td></tr></table>",
                "x",
            ),
            "mismatched-inline-close.html": (
                "<table><caption>x</caption><tr><th>名称</th></tr>"
                "<tr><td><span>合成</em></span></td></tr></table>",
                "x",
            ),
            "unclosed-inline.html": (
                "<table><caption>x</caption><tr><th>名称</th></tr>"
                "<tr><td><span>合成</td></tr></table>",
                "x",
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, (source, caption) in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    with self.assertRaises(HtmlStructureError):
                        extract_html_table(path, table_index=1, expected_caption=caption, mapping={"name": "名称"})

    def test_selected_table_strict_grammar_keeps_legal_controls_exact(self):
        cases = {
            "direct.html": (
                "<table><caption><span>x</span></caption><tr><th>名称</th></tr>"
                "<tr><td><span>合成甲</span></td></tr></table>"
            ),
            "sectioned.html": (
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead>"
                "<tbody><tr><td>合成甲</td></tr></tbody></table>"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    table = extract_html_table(path, table_index=1, expected_caption="x", mapping={"name": "名称"})
                    self.assertEqual(table.rows[0].values["name"], "合成甲")
                    self.assertEqual(table.rows[0].cell_status["name"], CellStatus.EXACT)
                    self.assertEqual(table.rows[0].confidence, 1.0)

    def test_duplicate_casefolded_attributes_fail_closed_before_last_value_can_win(self):
        cases = {
            "duplicate-rowspan.html": (
                "<table><caption>x</caption><tr><th>名称</th></tr>"
                "<tr><td rowspan='2' ROWSPAN='1'>合成</td></tr></table>"
            ),
            "duplicate-colspan.html": (
                "<table><caption>x</caption><tr><th colspan='2' COLSPAN='1'>名称</th></tr>"
                "<tr><td>合成</td></tr></table>"
            ),
            "duplicate-generic-cell-attribute.html": (
                "<table><caption>x</caption><tr><th>名称</th></tr>"
                "<tr><td data-kind='a' DATA-KIND='b'>合成</td></tr></table>"
            ),
            "duplicate-table-attribute.html": (
                "<table id='a' ID='b'><caption>x</caption><tr><th>名称</th></tr>"
                "<tr><td>合成</td></tr></table>"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    with self.assertRaises(HtmlStructureError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping={"name": "名称"})

    def test_nonwhitespace_text_at_table_section_or_row_level_is_malformed(self):
        cases = {
            "table-text.html": (
                "<table><caption>x</caption>leak<thead><tr><th>名称</th></tr></thead>"
                "<tbody><tr><td>合成甲</td></tr></tbody></table>"
            ),
            "thead-text.html": (
                "<table><caption>x</caption><thead>leak<tr><th>名称</th></tr></thead>"
                "<tbody><tr><td>合成甲</td></tr></tbody></table>"
            ),
            "tbody-text.html": (
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead>"
                "<tbody>leak<tr><td>合成甲</td></tr></tbody></table>"
            ),
            "tfoot-text.html": (
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead>"
                "<tbody><tr><td>合成甲</td></tr></tbody>"
                "<tfoot>leak<tr><td>合成乙</td></tr></tfoot></table>"
            ),
            "row-text.html": (
                "<table><caption>x</caption><thead><tr>leak<th>名称</th></tr></thead>"
                "<tbody><tr><td>合成甲</td></tr></tbody></table>"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    with self.assertRaises(HtmlStructureError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping={"name": "名称"})

    def test_structure_whitespace_and_ignored_payloads_keep_cell_and_caption_controls(self):
        source = (
            "<table>\n  <caption><span>x</span></caption>\n"
            "<script>ignored table payload</script>\n"
            "<thead>\n<tr>\n<th>名称</th>\n</tr>\n</thead>\n"
            "<tbody>\n<style>ignored body payload</style>\n"
            "<tr>\n<td><span>合成甲</span></td>\n</tr>\n</tbody>\n</table>"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_html(Path(temporary), source, "whitespace-control.html")
            table = extract_html_table(path, table_index=1, expected_caption="x", mapping={"name": "名称"})
        self.assertEqual(table.rows[0].values["name"], "合成甲")
        self.assertEqual(table.rows[0].cell_status["name"], CellStatus.EXACT)

    def test_balanced_colgroup_and_void_col_before_table_sections_are_supported(self):
        cases = {
            "plain-col.html": (
                "<table><caption>x</caption><colgroup><col><col span='2'></colgroup>"
                "<thead><tr><th>名称</th></tr></thead>"
                "<tbody><tr><td>合成甲</td></tr></tbody></table>"
            ),
            "self-closing-col.html": (
                "<table><caption>x</caption>"
                "<colgroup class='layout'>"
                "<col span='1' class='name' data-purpose='layout' aria-label='name' />"
                "</colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "group-span.html": (
                "<table><caption>x</caption><colgroup span='1'></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    try:
                        table = extract_html_table(
                            path,
                            table_index=1,
                            expected_caption="x",
                            mapping={"name": "名称"},
                        )
                    except HtmlStructureError:
                        self.fail("balanced colgroup/col markup was rejected")
                    self.assertEqual(table.rows[0].values["name"], "合成甲")
                    self.assertEqual(table.rows[0].cell_status["name"], CellStatus.EXACT)

    def test_colgroup_rejects_wrong_parent_order_unbalanced_col_and_invalid_attributes(self):
        cases = {
            "col-outside-group.html": (
                "<table><caption>x</caption><col><tr><th>名称</th></tr>"
                "<tr><td>合成甲</td></tr></table>"
            ),
            "group-after-head.html": (
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead>"
                "<colgroup><col></colgroup><tbody><tr><td>合成甲</td></tr></tbody></table>"
            ),
            "group-inside-body.html": (
                "<table><caption>x</caption><thead><tr><th>名称</th></tr></thead>"
                "<tbody><colgroup><col></colgroup><tr><td>合成甲</td></tr></tbody></table>"
            ),
            "nested-group.html": (
                "<table><caption>x</caption><colgroup><colgroup><col></colgroup></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "explicit-col-close.html": (
                "<table><caption>x</caption><colgroup><col></col></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "unclosed-group.html": (
                "<table><caption>x</caption><colgroup><col>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "cell-only-col-attribute.html": (
                "<table><caption>x</caption><colgroup><col rowspan='1'></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "zero-col-span.html": (
                "<table><caption>x</caption><colgroup><col span='0'></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "oversized-col-span.html": (
                "<table><caption>x</caption><colgroup><col span='1001'></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "group-span-with-child.html": (
                "<table><caption>x</caption><colgroup span='1'><col></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "unknown-col-attribute.html": (
                "<table><caption>x</caption><colgroup><col rank='1'></colgroup>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    with self.assertRaises(HtmlStructureError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping={"name": "名称"})

    def test_self_closing_colgroup_is_not_synthesized_as_a_balanced_group(self):
        cases = {
            "empty-self-closing-group.html": (
                "<table><caption>x</caption><colgroup/>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
            "spanned-self-closing-group.html": (
                "<table><caption>x</caption><colgroup span='2'/>"
                "<tr><th>名称</th></tr><tr><td>合成甲</td></tr></table>"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in cases.items():
                with self.subTest(name=name):
                    path = self._write_html(directory, source, name)
                    with self.assertRaises(HtmlStructureError):
                        extract_html_table(path, table_index=1, expected_caption="x", mapping={"name": "名称"})


class FileBoundaryTest(unittest.TestCase):
    def test_html_requires_absolute_exact_suffix_regular_bounded_non_url_file(self):
        with self.assertRaises(StructuredFileError):
            extract_html_table("tests/fixture.html", table_index=1, expected_caption="x", mapping={"x": "x"})
        for value in ("https://example.test/table.html", "file:///tmp/table.html"):
            with self.subTest(value=value):
                with self.assertRaises(StructuredFileError):
                    extract_html_table(value, table_index=1, expected_caption="x", mapping={"x": "x"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            with self.assertRaises(StructuredFileError):
                extract_html_table(directory, table_index=1, expected_caption="x", mapping={"x": "x"})
            wrong = directory / "input.HTML"
            wrong.write_text("<table></table>", encoding="utf-8")
            with self.assertRaises(StructuredFileError):
                extract_html_table(wrong, table_index=1, expected_caption="x", mapping={"x": "x"})
            oversized = directory / "large.html"
            oversized.write_bytes(b"x" * 32)
            with mock.patch("scripts.adapters.MAX_FILE_BYTES", 16):
                with self.assertRaises(StructuredFileError):
                    extract_html_table(oversized, table_index=1, expected_caption="x", mapping={"x": "x"})

    def test_parent_traversal_and_same_name_replacement_fail_closed_without_path_leak(self):
        traversal = str(HTML_FIXTURE.parent / ".." / "structured" / HTML_FIXTURE.name)
        with self.assertRaises(StructuredFileError):
            extract_html_table(traversal, table_index=1, expected_caption="x", mapping={"x": "x"})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            target = directory / "race.html"
            target.write_text("<table><caption>x</caption><tr><th>x</th></tr></table>", encoding="utf-8")
            replacement = directory / "replacement.html"
            replacement.write_text("<table><caption>y</caption><tr><th>y</th></tr></table>", encoding="utf-8")
            original_open = Path.open

            def replacing_open(candidate, *args, **kwargs):
                if candidate == target:
                    os.replace(replacement, target)
                return original_open(candidate, *args, **kwargs)

            with mock.patch.object(Path, "open", replacing_open):
                with self.assertRaises(StructuredFileError) as raised:
                    extract_html_table(target, table_index=1, expected_caption="x", mapping={"x": "x"})
            self.assertNotIn(str(directory), str(raised.exception))

    def test_symlink_or_reparse_input_is_rejected_when_platform_can_create_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            link = directory / "linked.html"
            try:
                os.symlink(HTML_FIXTURE, link)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {type(error).__name__}")
            with self.assertRaises(StructuredFileError):
                extract_html_table(link, table_index=1, expected_caption="x", mapping={"x": "x"})

    def test_resolved_path_drift_from_parent_reparse_fails_closed(self):
        redirected = HTML_FIXTURE.parent.parent / "redirected" / HTML_FIXTURE.name
        with mock.patch.object(Path, "resolve", return_value=redirected):
            with self.assertRaises(StructuredFileError):
                extract_html_table(
                    HTML_FIXTURE,
                    table_index=1,
                    expected_caption="合成分数位次表",
                    mapping=HtmlAdapterTest.mapping(),
                )

    def test_nonlocal_windows_namespaces_are_rejected_before_any_filesystem_lookup(self):
        paths = (
            r"\\server\share\table.html",
            "//server/share/table.html",
            r"\\?\C:\table.html",
            r"\\.\C:\table.html",
            r"\\?\UNC\server\share\table.html",
            r"C:relative\table.html",
        )
        blocked = AssertionError("filesystem lookup reached")
        with mock.patch.object(Path, "resolve", side_effect=blocked), mock.patch.object(
            Path, "stat", side_effect=blocked
        ), mock.patch.object(Path, "lstat", side_effect=blocked), mock.patch.object(
            Path, "open", side_effect=blocked
        ):
            for value in paths:
                with self.subTest(value=value):
                    with self.assertRaises(StructuredFileError):
                        extract_html_table(
                            value,
                            table_index=1,
                            expected_caption="x",
                            mapping={"x": "x"},
                        )

    def test_controlled_file_errors_strip_oserror_causes_filenames_and_input_paths(self):
        secret = str((Path.cwd() / "private-source-name.html").absolute())
        operating_system_error = OSError(5, "denied", secret)
        with mock.patch.object(Path, "resolve", side_effect=operating_system_error):
            with self.assertRaises(StructuredFileError) as raised:
                extract_html_table(
                    secret,
                    table_index=1,
                    expected_caption="x",
                    mapping={"x": "x"},
                )
        error = raised.exception
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        self.assertIsNone(getattr(error, "filename", None))
        self.assertNotIn(secret, "".join(traceback.format_exception(error)))

    def test_controlled_file_read_errors_also_strip_causes_and_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = (Path(temporary) / "private-read-name.html").resolve()
            source.write_text("synthetic", encoding="utf-8")
            operating_system_error = OSError(5, "denied", str(source))
            with mock.patch.object(Path, "open", side_effect=operating_system_error):
                with self.assertRaises(StructuredFileError) as raised:
                    extract_html_table(
                        source,
                        table_index=1,
                        expected_caption="x",
                        mapping={"x": "x"},
                    )
            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
            self.assertIsNone(getattr(error, "filename", None))
            self.assertNotIn(str(source), "".join(traceback.format_exception(error)))

    def test_pathlike_conversion_errors_are_controlled_before_a_path_object_exists(self):
        secret = "private-pathlike-name.html"

        class FailingPathLike(os.PathLike):
            def __fspath__(self):
                raise OSError(5, "denied", secret)

        try:
            extract_html_table(
                FailingPathLike(),
                table_index=1,
                expected_caption="x",
                mapping={"x": "x"},
            )
        except Exception as error:
            self.assertIsInstance(error, StructuredFileError)
            caught = error
        else:
            self.fail("failing path-like conversion did not fail closed")
        self.assertIsNone(caught.__cause__)
        self.assertTrue(caught.__suppress_context__)
        self.assertNotIn(secret, "".join(traceback.format_exception(caught)))


class AdmissionAdapterEndToEndTest(unittest.TestCase):
    @staticmethod
    def _mapping() -> ColumnMapping:
        return ColumnMapping(
            {
                "school_code": "院校代码",
                "school_name": "院校名称",
                "program_group": "专业组",
                "min_score": "最低分",
                "min_rank": "最低位次",
            },
            roles={"min_score": "score", "min_rank": "rank"},
            score_scale=(0, 750),
        )

    def _html_table(self, workspace: Path):
        path = (workspace / "admission.html").resolve()
        path.write_text(
            "<table><caption>普通批投档</caption><tr>"
            "<th>院校代码</th><th>院校名称</th><th>专业组</th>"
            "<th>最低分</th><th>最低位次</th></tr>"
            "<tr><td>SYN312A</td><td>虚构甲大学</td><td>第01组</td>"
            "<td>605</td><td>20000</td></tr></table>",
            encoding="utf-8",
        )
        return extract_html_table(
            path,
            table_index=1,
            expected_caption="普通批投档",
            mapping=self._mapping(),
        )

    def _xlsx_table(self, workspace: Path):
        from openpyxl import Workbook

        path = (workspace / "admission.xlsx").resolve()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "普通批"
        sheet.append(("院校代码", "院校名称", "专业组", "最低分", "最低位次"))
        sheet.append(("SYN312A", "虚构甲大学", "第01组", 605, 20000))
        workbook.save(path)
        workbook.close()
        return extract_spreadsheet(path, sheet="普通批", mapping=self._mapping())

    def _ocr_table(self, workspace: Path):
        from scripts.adapters.ocr_rows import normalize_ocr_rows

        labels = ("院校代码", "院校名称", "专业组", "最低分", "最低位次")
        rows = (
            ("SYN312A", "虚构甲大学", "第01组", 605, 20000),
            ("SYN312B", "虚构乙大学", "第02组", 600, 21000),
        )
        payload_rows = []
        for row_index, values in enumerate(rows):
            top = row_index * 50
            cells = []
            for column_index, (label, value) in enumerate(zip(labels, values)):
                left = column_index * 100
                cells.append(
                    {
                        "label": label,
                        "bbox": [left, top, left + 90, top + 40],
                        "raw_text": str(value),
                        "normalized_value": value,
                        "confidence": 0.99,
                        "verified": True,
                    }
                )
            payload_rows.append(
                {
                    "page_number": 1,
                    "image_id": "page-1",
                    "bbox": [0, top, 500, top + 40],
                    "cropped": False,
                    "cells": cells,
                }
            )
        payload = {
            "schema_version": 1,
            "document_id": "ocr-admission",
            "total_pages": 1,
            "covered_pages": [1],
            "images": [{"page_number": 1, "image_id": "page-1"}],
            "rows": payload_rows,
            "anchors": [
                {
                    "row_index": 1,
                    "label": "院校代码",
                    "bbox": [0, 0, 90, 40],
                    "raw_text": "SYN312A",
                    "normalized_value": "SYN312A",
                },
                {
                    "row_index": 2,
                    "label": "最低位次",
                    "bbox": [400, 50, 490, 90],
                    "raw_text": "21000",
                    "normalized_value": 21000,
                },
            ],
        }
        path = (workspace / "admission-ocr.json").resolve()
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return normalize_ocr_rows(
            path,
            self._mapping(),
            score_scale=(0, 750),
            min_exact_confidence=0.95,
        )

    def test_html_xlsx_and_ocr_reach_one_shared_markdown_docx_model(self):
        from scripts import docx_export
        from scripts.adapters.admission_bridge import bridge_admission_evidence
        from scripts.decision_policy import DecisionPolicySnapshot
        from scripts.generate_report import build_pathway_atlas_model
        from scripts.report_model import render_markdown
        from scripts.research_snapshot import build_research_snapshot
        from scripts.validate_data import ValidatedAdmissionRow, admission_row_hash
        from tests.test_docx_semantic_parity import typed_atlas_artifacts
        from tests.test_research_snapshot import bridges

        builders = {
            "html": self._html_table,
            "xlsx": self._xlsx_table,
            "ocr": self._ocr_table,
        }
        for index, (adapter_name, build_table) in enumerate(builders.items()):
            with self.subTest(adapter=adapter_name), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary).resolve()
                planning, query_plan, _rank_bridge, _unused_admission_bridge = bridges()
                task = next(
                    item for item in query_plan.tasks
                    if item.kind == "batch_admission" and item.target_name == "普通批"
                    and item.year == 2025
                )
                dataset_row = ValidatedAdmissionRow.from_mapping(
                    {
                        "year": 2025,
                        "province": planning.province,
                        "subject_group": query_plan.subject_group,
                        "school_code": "SYN312A",
                        "school_name": "虚构甲大学",
                        "program_group": "第01组",
                        "min_score": 605,
                        "min_rank": 20000,
                        "remarks": "",
                    }
                )
                table = build_table(workspace)
                adapter_row = next(
                    row
                    for row in table.rows
                    if row.values["school_code"] == "SYN312A"
                )
                source_id = f"{adapter_name}-admission-source"
                candidate = SourceCandidate(
                    source_id=source_id,
                    url=f"https://www.hljea.org.cn/{adapter_name}-admission",
                    publisher="黑龙江省招生考试院",
                    tier=SourceTier.A,
                    published_at="2025-06-25",
                    retrieved_at="2026-06-26T00:00:00Z",
                    content_hash="sha256:" + chr(ord("a") + index) * 64,
                    citation_root="https://www.hljea.org.cn/",
                    summary=f"{adapter_name} 合成普通批投档表",
                )
                bridged = bridge_admission_evidence(
                    table=table,
                    adapter_row=adapter_row,
                    task=task,
                    dataset_row=dataset_row,
                    fact_id=f"{adapter_name}-admission-row",
                    candidates=(candidate,),
                    coverage_status=EvidenceStatus.PARTIAL,
                )
                self.assertEqual(
                    bridged.admission_row_hash, admission_row_hash(dataset_row)
                )
                self.assertEqual(bridged.fact.status, EvidenceStatus.OFFICIAL)
                self.assertEqual(
                    bridged.fact.value["coverage_status"], "partial"
                )

                with typed_atlas_artifacts(
                    admission_bridge=bridged, admission_candidates=(candidate,)
                ) as (typed_profile, typed_plan, bundle, _profile_path):
                    self.assertEqual(typed_profile.digest, planning.digest)
                    self.assertEqual(typed_plan, query_plan)
                    reviewed = DecisionPolicySnapshot.load_default()
                    research = build_research_snapshot(
                        typed_profile, typed_plan, bundle, reviewed
                    )
                    report = build_pathway_atlas_model(
                        typed_profile,
                        research,
                        bundle,
                        typed_plan,
                        decision_policy=reviewed,
                    )
                    markdown = render_markdown(report)
                    docx_path = docx_export.export_docx(
                        report, workspace / docx_export.PUBLIC_DOCX_BASENAME
                    )
                    with zipfile.ZipFile(docx_path) as archive:
                        document_xml = archive.read("word/document.xml").decode("utf-8")

                for literal in (
                    "虚构甲大学", source_id, "二、当前最需要做的事",
                    "八、证据披露",
                ):
                    self.assertIn(literal, markdown)
                    self.assertIn(literal, document_xml)


class SpreadsheetAdapterTest(unittest.TestCase):
    @staticmethod
    def mapping() -> ColumnMapping:
        return ColumnMapping(
            {
                "school": "院校",
                "group": "专业组",
                "min_score": "最低分",
                "min_rank": "最低位次",
                "plan_count": "计划数",
                "note": "备注",
            },
            roles={"min_score": "score", "min_rank": "rank"},
            score_scale=(0, 750),
        )

    def _mutated_fixture(self, directory: Path, name: str, mutate) -> Path:
        output = (directory / name).resolve()
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        ET.register_namespace("x", namespace)
        with zipfile.ZipFile(XLSX_FIXTURE) as source, zipfile.ZipFile(output, "w") as destination:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    root = ET.fromstring(payload)
                    mutate(root, namespace)
                    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                destination.writestr(item, payload)
        return output

    def test_exact_sheet_header_location_formula_merge_empty_and_coverage(self):
        table = extract_spreadsheet(XLSX_FIXTURE, sheet="物理类", mapping=self.mapping())
        self.assertEqual(table.table_id, "sheet:物理类")
        self.assertEqual(table.sheet, "物理类")
        self.assertIsNone(table.caption)
        self.assertEqual(table.extraction_method, "xlsx-worksheet")
        self.assertEqual(table.rows[0].location, "物理类!A2:F2")
        self.assertEqual(table.rows[0].values["school"], "虚构大学甲")
        self.assertEqual(table.rows[0].values["min_score"], 620)
        self.assertEqual(table.rows[0].cell_status["min_score"], CellStatus.EXACT)
        self.assertEqual(table.rows[1].cell_status["min_score"], CellStatus.FORMULA)
        self.assertIn("formula-cell:min_score", table.rows[1].warnings)
        self.assertEqual(table.rows[2].cell_status["group"], CellStatus.MERGED)
        self.assertEqual(table.rows[2].cell_status["plan_count"], CellStatus.EMPTY)
        self.assertIn("merged-cell:group", table.rows[2].warnings)
        self.assertIn("empty-required-cell:plan_count", table.rows[2].warnings)
        self.assertEqual(
            table.coverage,
            ExtractedCoverage(lower_score=620, upper_score=620, lower_rank=1200, upper_rank=1200),
        )
        self.assertIn("coverage-excludes-nonexact-rows", table.warnings)

    def test_real_xlsx_extraction_method_persists_as_validated_fact_provenance(self):
        table = extract_spreadsheet(XLSX_FIXTURE, sheet="物理类", mapping=self.mapping())
        row = table.rows[0]
        with tempfile.TemporaryDirectory() as temporary:
            store = EvidenceStore.create(
                Path(temporary).resolve(),
                CapabilityReport(tier=CapabilityTier.STANDARD),
            )
            for index in range(1, 4):
                store.add_candidate(
                    SourceCandidate(
                        source_id=f"xlsx-s{index}",
                        url=f"https://xlsx-{index}.example.test/article",
                        publisher=f"Synthetic XLSX Publisher {index}",
                        tier=SourceTier.C,
                        published_at=None,
                        retrieved_at="2026-08-24T00:00:00Z",
                        content_hash=f"sha256:xlsx-{index}",
                        citation_root=f"https://xlsx-{index}.example.test/original",
                        summary="Synthetic worksheet source",
                    )
                )
            store.add_fact(
                EvidenceFact(
                    fact_id="xlsx-admission-row-1",
                    field="admission_record:xlsx-row-1",
                    value=row.to_dict()["values"],
                    unit=None,
                    status=EvidenceStatus.REFERENCE,
                    source_ids=("xlsx-s1", "xlsx-s2", "xlsx-s3"),
                    method="three-source-consensus",
                    notes="",
                ),
                year=2026,
                extraction_method=table.extraction_method,
                locator=row.location,
            )
            store.finalize()

            validation = validate_bundle_snapshot(store.session_path)

            self.assertEqual(validation.issues, ())
            self.assertIsNotNone(validation.snapshot)
            provenance = [
                json.loads(line)
                for line in (store.session_path / "context.jsonl").read_text("utf-8").splitlines()
            ]
            self.assertEqual(provenance[0]["extraction_method"], "xlsx-worksheet")
            self.assertEqual(provenance[0]["locator"], "物理类!A2:F2")

    def test_exact_sheet_selection_never_falls_back_to_unrelated_sheet(self):
        with self.assertRaises(StructuredValidationError):
            extract_spreadsheet(XLSX_FIXTURE, sheet="不存在", mapping=self.mapping())
        with self.assertRaises(MappingError):
            extract_spreadsheet(XLSX_FIXTURE, sheet="说明", mapping=self.mapping())

    def test_duplicate_and_whitespace_only_xlsx_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def duplicate(root, namespace):
                cell = root.find(f".//{{{namespace}}}c[@r='B1']/{{{namespace}}}v")
                cell.text = "院校"

            def whitespace(root, namespace):
                cell = root.find(f".//{{{namespace}}}c[@r='C1']/{{{namespace}}}v")
                cell.text = " 最低分 "

            for name, mutation in (("duplicate.xlsx", duplicate), ("whitespace.xlsx", whitespace)):
                path = self._mutated_fixture(directory, name, mutation)
                with self.subTest(name=name):
                    with self.assertRaises(MappingError):
                        extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())

    def test_hidden_truncated_and_formula_only_structures_remain_nonexact_with_warnings(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def hide_header(root, namespace):
                root.find(f".//{{{namespace}}}row[@r='1']").set("hidden", "1")

            hidden_header = self._mutated_fixture(directory, "hidden-header.xlsx", hide_header)
            hidden_header_table = extract_spreadsheet(hidden_header, sheet="物理类", mapping=self.mapping())
            self.assertIn("hidden-header-row", hidden_header_table.warnings)
            self.assertTrue(
                all(status is CellStatus.UNCERTAIN for status in hidden_header_table.rows[0].cell_status.values())
            )
            self.assertEqual(hidden_header_table.coverage, ExtractedCoverage())
            self.assertIn("coverage-score-unavailable", hidden_header_table.warnings)

            def hide_data(root, namespace):
                root.find(f".//{{{namespace}}}row[@r='2']").set("hidden", "1")

            hidden_data = self._mutated_fixture(directory, "hidden-data.xlsx", hide_data)
            hidden_table = extract_spreadsheet(hidden_data, sheet="物理类", mapping=self.mapping())
            self.assertEqual(hidden_table.rows[0].cell_status["min_score"], CellStatus.UNCERTAIN)
            self.assertIn("hidden-row", hidden_table.rows[0].warnings)
            self.assertEqual(hidden_table.coverage, ExtractedCoverage())

            def hide_mapped_column(root, namespace):
                columns = root.find(f"{{{namespace}}}cols")
                ET.SubElement(
                    columns,
                    f"{{{namespace}}}col",
                    {"min": "3", "max": "3", "hidden": "1"},
                )

            hidden_column = self._mutated_fixture(directory, "hidden-column.xlsx", hide_mapped_column)
            hidden_column_table = extract_spreadsheet(hidden_column, sheet="物理类", mapping=self.mapping())
            self.assertTrue(
                all(status is CellStatus.UNCERTAIN for status in hidden_column_table.rows[0].cell_status.values())
            )
            self.assertEqual(hidden_column_table.coverage, ExtractedCoverage())
            self.assertIn("coverage-score-unavailable", hidden_column_table.warnings)

            def truncate_exact_row(root, namespace):
                row2 = root.find(f".//{{{namespace}}}row[@r='2']")
                row2.remove(row2.find(f"{{{namespace}}}c[@r='F2']"))

            truncated = self._mutated_fixture(directory, "truncated-exact-row.xlsx", truncate_exact_row)
            truncated_table = extract_spreadsheet(truncated, sheet="物理类", mapping=self.mapping())
            self.assertIn("truncated-row", truncated_table.rows[0].warnings)
            self.assertEqual(truncated_table.rows[0].cell_status["note"], CellStatus.EMPTY)
            self.assertTrue(
                all(
                    status is CellStatus.UNCERTAIN
                    for field, status in truncated_table.rows[0].cell_status.items()
                    if field != "note"
                )
            )
            self.assertEqual(truncated_table.coverage, ExtractedCoverage())
            self.assertIn("coverage-score-unavailable", truncated_table.warnings)

            def truncate_and_add_formula(root, namespace):
                sheet_data = root.find(f"{{{namespace}}}sheetData")
                row4 = sheet_data.find(f"{{{namespace}}}row[@r='4']")
                row4.remove(row4.find(f"{{{namespace}}}c[@r='F4']"))
                row5 = ET.SubElement(sheet_data, f"{{{namespace}}}row", {"r": "5"})
                cell = ET.SubElement(row5, f"{{{namespace}}}c", {"r": "C5", "t": "n"})
                ET.SubElement(cell, f"{{{namespace}}}f").text = "C2-20"
                ET.SubElement(cell, f"{{{namespace}}}v").text = "600"

            altered = self._mutated_fixture(directory, "truncated-formula-only.xlsx", truncate_and_add_formula)
            altered_table = extract_spreadsheet(altered, sheet="物理类", mapping=self.mapping())
            self.assertIn("truncated-row", altered_table.rows[2].warnings)
            self.assertEqual(altered_table.rows[3].location, "物理类!A5:F5")
            self.assertEqual(altered_table.rows[3].cell_status["min_score"], CellStatus.FORMULA)
            self.assertIn("formula-cell:min_score", altered_table.rows[3].warnings)

    def test_large_merge_and_hidden_ranges_use_bounded_interval_membership(self):
        real_range = range

        def reject_large_expansion(*arguments):
            result = real_range(*arguments)
            if len(result) > 20_000:
                raise AssertionError("worksheet range was expanded cell-by-cell")
            return result

        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            spreadsheet_adapter, "range", reject_large_expansion, create=True
        ):
            directory = Path(temporary)
            for name, reference, inside in (
                ("tall-merge.xlsx", "A1:A100000", (100_000, 1)),
                ("full-sheet-merge.xlsx", "A1:XFD1048576", (1_048_576, 16_384)),
            ):
                def large_ranges(root, namespace, reference=reference):
                    merges = root.find(f"{{{namespace}}}mergeCells")
                    merges.clear()
                    ET.SubElement(merges, f"{{{namespace}}}mergeCell", {"ref": reference})
                    columns = root.find(f"{{{namespace}}}cols")
                    ET.SubElement(
                        columns,
                        f"{{{namespace}}}col",
                        {"min": "1", "max": "16384", "hidden": "1"},
                    )

                with self.subTest(name=name):
                    path = self._mutated_fixture(directory, name, large_ranges)
                    structure = spreadsheet_adapter._worksheet_structure(path.read_bytes(), "物理类")
                    self.assertEqual(len(structure.merged_ranges), 1)
                    self.assertEqual(len(structure.hidden_column_ranges), 1)
                    self.assertTrue(structure.is_merged(*inside))
                    self.assertTrue(structure.is_hidden_column(16_384))
                    self.assertFalse(structure.is_hidden_column(16_385))

    def test_xlsx_ranges_reject_reverse_bounds_out_of_excel_grid_and_excessive_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            references = {
                "reverse.xlsx": ["B2:A1"],
                "column-overflow.xlsx": ["XFE1:XFE2"],
                "row-overflow.xlsx": ["A1048577:A1048578"],
                "too-many.xlsx": [f"A{index}:A{index}" for index in range(1, 4098)],
            }
            for name, ranges in references.items():
                def invalid_ranges(root, namespace, ranges=ranges):
                    merges = root.find(f"{{{namespace}}}mergeCells")
                    merges.clear()
                    for reference in ranges:
                        ET.SubElement(merges, f"{{{namespace}}}mergeCell", {"ref": reference})

                with self.subTest(name=name):
                    path = self._mutated_fixture(directory, name, invalid_ranges)
                    with self.assertRaises(StructuredValidationError):
                        spreadsheet_adapter._worksheet_structure(path.read_bytes(), "物理类")

    def test_xlsx_numeric_bool_nonintegral_rank_scale_and_monotonicity_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def bool_score(root, namespace):
                cell = root.find(f".//{{{namespace}}}c[@r='C2']")
                cell.set("t", "b")
                cell.find(f"{{{namespace}}}v").text = "1"

            def nonintegral_rank(root, namespace):
                root.find(f".//{{{namespace}}}c[@r='D2']/{{{namespace}}}v").text = "1.5"

            def outside_scale(root, namespace):
                root.find(f".//{{{namespace}}}c[@r='C2']/{{{namespace}}}v").text = "751"

            for name, mutation in (
                ("bool.xlsx", bool_score),
                ("rank.xlsx", nonintegral_rank),
                ("scale.xlsx", outside_scale),
            ):
                path = self._mutated_fixture(directory, name, mutation)
                with self.subTest(name=name):
                    with self.assertRaises(StructuredValidationError):
                        extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())

            def nonmonotonic(root, namespace):
                row3_score = root.find(f".//{{{namespace}}}c[@r='C3']")
                formula = row3_score.find(f"{{{namespace}}}f")
                row3_score.remove(formula)
                row3_score.find(f"{{{namespace}}}v").text = "621"

            path = self._mutated_fixture(directory, "nonmonotonic.xlsx", nonmonotonic)
            with self.assertRaises(StructuredValidationError):
                extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())

    def test_formula_merged_anchor_keeps_formula_precedence_and_both_warnings(self):
        with tempfile.TemporaryDirectory() as temporary:
            def merge_formula_anchor(root, namespace):
                merges = root.find(f"{{{namespace}}}mergeCells")
                ET.SubElement(merges, f"{{{namespace}}}mergeCell", {"ref": "C3:D3"})

            path = self._mutated_fixture(Path(temporary), "formula-merged.xlsx", merge_formula_anchor)
            table = extract_spreadsheet(path, sheet="物理类", mapping=self.mapping())
            formula_row = table.rows[1]
            self.assertEqual(formula_row.cell_status["min_score"], CellStatus.FORMULA)
            self.assertIn("formula-cell:min_score", formula_row.warnings)
            self.assertIn("merged-cell:min_score", formula_row.warnings)

    def test_fixture_binary_independently_contains_expected_structure_formula_merge_and_style(self):
        with zipfile.ZipFile(XLSX_FIXTURE) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet_names = [item.attrib["name"] for item in workbook.findall("m:sheets/m:sheet", ns)]
            self.assertEqual(sheet_names, ["物理类", "说明"])
            worksheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            formulas = [item.text for item in worksheet.findall(".//m:f", ns)]
            merges = [item.attrib["ref"] for item in worksheet.findall("m:mergeCells/m:mergeCell", ns)]
            styled = [item for item in worksheet.findall(".//m:c", ns) if int(item.attrib.get("s", "0")) > 0]
            self.assertIn("C2-10", formulas)
            self.assertIn("A4:B4", merges)
            self.assertTrue(styled)
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            self.assertGreater(int(styles.find("m:fonts", ns).attrib["count"]), 1)
            self.assertGreater(int(styles.find("m:fills", ns).attrib["count"]), 1)

    def test_missing_dependency_is_a_controlled_runtime_error_not_import_failure(self):
        code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from scripts.adapters import ColumnMapping
from scripts.adapters.spreadsheet import SpreadsheetDependencyError, extract_spreadsheet
try:
    extract_spreadsheet({str(XLSX_FIXTURE)!r}, sheet='物理类', mapping=ColumnMapping({{'school': '院校'}}))
except SpreadsheetDependencyError as error:
    assert {str(ROOT)!r} not in str(error)
    print(type(error).__name__)
else:
    raise SystemExit('dependency failure did not fail closed')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.strip(), "SpreadsheetDependencyError")

    def test_package_and_flat_imports_are_lazy_and_perform_no_file_or_network_io(self):
        code = f"""
import pathlib, socket, sys
sys.path.insert(0, {str(ROOT)!r})
def blocked(*args, **kwargs):
    raise AssertionError('I/O during import')
pathlib.Path.open = blocked
pathlib.Path.read_bytes = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
import scripts.adapters.html_table
import scripts.adapters.spreadsheet
import scripts.adapters.pathway_extraction
import scripts.adapters.pathway_bridge
sys.modules.pop('adapters', None)
sys.path.insert(0, {str(ROOT / 'scripts')!r})
import adapters.html_table
import adapters.spreadsheet
import adapters.pathway_extraction
import adapters.pathway_bridge
print('ok')
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")

    def test_xlsx_requires_exact_safe_file_and_rechecks_same_name_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            wrong = directory / "fixture.XLSX"
            wrong.write_bytes(XLSX_FIXTURE.read_bytes())
            with self.assertRaises(StructuredFileError):
                extract_spreadsheet(wrong, sheet="物理类", mapping=self.mapping())

            target = directory / "race.xlsx"
            target.write_bytes(XLSX_FIXTURE.read_bytes())
            replacement = directory / "replacement.xlsx"
            replacement.write_bytes(XLSX_FIXTURE.read_bytes() + b"replacement")
            original_open = Path.open

            def replacing_open(candidate, *args, **kwargs):
                if candidate == target:
                    os.replace(replacement, target)
                return original_open(candidate, *args, **kwargs)

            with mock.patch.object(Path, "open", replacing_open):
                with self.assertRaises(StructuredFileError) as raised:
                    extract_spreadsheet(target, sheet="物理类", mapping=self.mapping())
            self.assertNotIn(str(directory), str(raised.exception))

    def test_network_sentinels_are_never_touched(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), mock.patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("network")
        ), mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")), mock.patch.object(
            http.client.HTTPConnection, "connect", side_effect=AssertionError("network")
        ):
            self.assertEqual(len(extract_html_table(
                HTML_FIXTURE,
                table_index=1,
                expected_caption="合成分数位次表",
                mapping=HtmlAdapterTest.mapping(),
            ).rows), 3)
            self.assertEqual(len(extract_spreadsheet(XLSX_FIXTURE, sheet="物理类", mapping=self.mapping()).rows), 3)


class PathwayStructuredProjectionTest(unittest.TestCase):
    def test_html_output_and_candidate_generator_reach_typed_pathway_projection(self):
        from scripts.adapters.pathway_extraction import extract_pathway_policy
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            plan,
            policy_values,
            profile,
            task_for,
        )

        values = policy_values()
        headers = "".join(f"<th>{name}</th>" for name in POLICY_FIELDS)
        cells = "".join(f"<td>{values[name]}</td>" for name in POLICY_FIELDS)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "pathway.html").resolve()
            source.write_text(
                f"<table><caption>合成路径政策</caption><tr>{headers}</tr><tr>{cells}</tr></table>",
                encoding="utf-8",
            )
            table = extract_html_table(
                source,
                table_index=1,
                expected_caption="合成路径政策",
                mapping=ColumnMapping({name: name for name in POLICY_FIELDS}),
            )

        student = profile()
        query_plan = plan(student)
        task = task_for(query_plan)
        projection = extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task,
            extraction=table,
            field_map={name: name for name in POLICY_FIELDS},
            candidates=(item for item in (candidate(),)),
        )
        self.assertIs(projection.evidence_status, EvidenceStatus.OFFICIAL)
        self.assertEqual(projection.institution, "示例高校")
        self.assertEqual(
            {method for item in projection.field_provenance for method in item.extraction_methods},
            {"html-table"},
        )

    def test_xlsx_adapter_contract_output_is_accepted_without_path_material(self):
        from scripts.adapters.pathway_extraction import extract_pathway_policy
        from tests.test_pathway_evidence_bridge import (
            POLICY_FIELDS,
            candidate,
            plan,
            policy_table,
            profile,
            task_for,
        )

        student = profile()
        query_plan = plan(student)
        task = task_for(query_plan)
        table = replace(policy_table(), extraction_method="xlsx-worksheet")
        projection = extract_pathway_policy(
            profile=student,
            plan=query_plan,
            task=task,
            extraction=table,
            field_map={name: name for name in POLICY_FIELDS},
            candidates=(candidate(),),
        )
        serialized = json.dumps(projection.to_dict(), ensure_ascii=False)
        self.assertIn("xlsx-worksheet", serialized)
        self.assertNotIn(str(ROOT), serialized)


if __name__ == "__main__":
    unittest.main()
