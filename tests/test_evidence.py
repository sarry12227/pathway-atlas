import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceFact,
    EvidenceStatus,
    SourceCandidate,
    SourceTier,
)
from scripts.evidence import (
    EvidencePathError,
    EvidencePrivacyError,
    EvidenceStateError,
    EvidenceStore,
)


class EvidenceStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.report = CapabilityReport(
            tier=CapabilityTier.STANDARD,
            host_capabilities=("browse", "search"),
            available_capabilities=("browse", "search"),
            missing_capabilities=("vision",),
            degradations=("skip-image-tables",),
            python_version="3.10.0",
            optional_modules=("openpyxl",),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def candidate(source_id):
        return SourceCandidate(
            source_id=source_id,
            url=f"https://{source_id}.example.test/source",
            publisher=f"Publisher {source_id}",
            tier=SourceTier.C,
            published_at=None,
            retrieved_at="2026-08-23T00:00:00Z",
            content_hash=f"sha256:{source_id}",
            citation_root=f"https://{source_id}.example.test",
            summary="Synthetic source",
        )

    @staticmethod
    def fact(fact_id, source_ids):
        return EvidenceFact(
            fact_id=fact_id,
            field="min_score",
            value=588,
            unit="分",
            status=EvidenceStatus.REFERENCE,
            source_ids=tuple(source_ids),
            method="three-source-consensus",
            notes="",
        )

    @staticmethod
    def persist_fact(store, fact, *, year=2026, extraction_method="manual-structured", locator=None):
        store.add_fact(
            fact,
            year=year,
            extraction_method=extraction_method,
            locator=locator or f"fact[{fact.fact_id}]",
        )

    def test_add_fact_requires_and_persists_one_canonical_provenance_record(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        fact = self.fact("score-001", ("s1",))

        with self.assertRaises(TypeError):
            store.add_fact(fact)
        self.persist_fact(
            store,
            fact,
            year=2026.0,
            extraction_method="html-table",
            locator="table[score]/row[12]",
        )
        store.finalize()

        contexts = [
            json.loads(line)
            for line in (store.session_path / "context.jsonl").read_text("utf-8").splitlines()
        ]
        self.assertEqual(
            contexts,
            [{
                "kind": "fact-provenance",
                "fact_id": "score-001",
                "source_ids": ["s1"],
                "year": 2026,
                "extraction_method": "html-table",
                "locator": "table[score]/row[12]",
            }],
        )

    def test_fact_provenance_rejects_unknown_or_unsafe_values_without_partial_state(self):
        invalid = (
            {"year": True},
            {"year": 2026.5},
            {"year": "2026"},
            {"extraction_method": "unknown-parser"},
            {"locator": "C:\\private\\scores.xlsx"},
            {"locator": "C:relative-sheet"},
            {"locator": "z:logical-row"},
            {"locator": "sheet[C:relative-sheet]"},
            {"locator": "sheet/C:relative"},
            {"locator": "../private/scores.xlsx"},
            {"locator": "sheet[../scores.xlsx]"},
            {"locator": "//server/share/scores.xlsx"},
            {"locator": "\\\\?\\C:\\private\\scores.xlsx"},
            {"locator": "sheet[C:/private/scores.xlsx]"},
            {"locator": "source[/home/user/scores.html]"},
            {"locator": "sheet[/opt/data/scores.xlsx]"},
            {"locator": "source[https://private.example.test/item]"},
            {"locator": "sheet[%TEMP%]"},
            {"locator": "sheet[$HOME]"},
            {"locator": "sheet[${HOME}]"},
            {"locator": "source[sk-live]"},
            {"locator": "source[ghp_abcdefghijklmnopqrstuvwxyz]"},
            {"locator": "api_key=secret-value"},
            {"locator": "Bearer=secret-value"},
            {"locator": "student[name@example.test]"},
            {"locator": "student-138-0013-8000"},
            {"locator": "student 138 0013 8000"},
            {"locator": "office-010-12345678"},
            {"locator": "row-C:private.txt"},
        )
        for index, overrides in enumerate(invalid):
            with self.subTest(index=index):
                store = EvidenceStore.create(self.root, self.report)
                store.add_candidate(self.candidate("s1"))
                values = {
                    "year": 2026,
                    "extraction_method": "manual-structured",
                    "locator": "record[score-001]",
                    **overrides,
                }
                with self.assertRaises(
                    (TypeError, ValueError, EvidencePrivacyError, EvidencePathError)
                ) as raised:
                    store.add_fact(self.fact("score-001", ("s1",)), **values)
                self.assertNotIn(str(values["locator"]), str(raised.exception))
                self.assertEqual(store._facts, {})
                self.assertEqual(store._contexts, [])

    def test_fact_provenance_accepts_semantic_colon_and_page_image_locators(self):
        safe_locators = (
            "物理类!A2:F2",
            "page[1]/image[page-1]/bbox[10,20,500,60]",
        )
        for index, locator in enumerate(safe_locators):
            with self.subTest(locator=locator):
                store = EvidenceStore.create(self.root, self.report)
                source_id = f"safe-s{index}"
                fact_id = f"safe-fact-{index}"
                store.add_candidate(self.candidate(source_id))
                store.add_fact(
                    self.fact(fact_id, (source_id,)),
                    year=2026,
                    extraction_method="manual-structured",
                    locator=locator,
                )
                store.finalize()

    def test_generic_context_cannot_forge_reserved_fact_provenance(self):
        store = EvidenceStore.create(self.root, self.report)
        with self.assertRaises(EvidenceStateError):
            store.add_context({"kind": "fact-provenance", "fact_id": "forged"})

    def build_bundle(self, order):
        store = EvidenceStore.create(self.root, self.report)
        for source_id in order:
            store.add_candidate(self.candidate(source_id))
        self.persist_fact(store, self.fact("score-001", tuple(reversed(order))))
        store.add_context({"query": {"province": "示例省", "school_name": "示例中学"}})
        return store.finalize()

    def test_manifest_hash_is_stable_for_same_content(self):
        first = self.build_bundle(order=("s1", "s2"))
        second = self.build_bundle(order=("s2", "s1"))

        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(first.manifest_hash, second.manifest_hash)

    def test_finalize_writes_sorted_utf8_jsonl_and_records_capability(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s2"))
        store.add_candidate(self.candidate("s1"))
        self.persist_fact(store, self.fact("score-002", ("s2",)))
        self.persist_fact(store, self.fact("score-001", ("s1",)))
        store.reject_candidate("s3", "same-publisher-or-citation-root")
        store.add_context({"publisher": "Example", "school_name": "Example School"})

        manifest = store.finalize()
        session = store.session_path
        candidate_lines = (session / manifest.candidates_filename).read_text("utf-8").splitlines()
        fact_lines = (session / manifest.facts_filename).read_text("utf-8").splitlines()
        rejection_lines = (session / "rejections.jsonl").read_text("utf-8").splitlines()

        self.assertEqual([json.loads(line)["source_id"] for line in candidate_lines], ["s1", "s2"])
        self.assertEqual([json.loads(line)["fact_id"] for line in fact_lines], ["score-001", "score-002"])
        self.assertEqual(json.loads(rejection_lines[0])["source_id"], "s3")
        self.assertEqual(json.loads((session / "capability.json").read_text("utf-8")), self.report.to_dict())
        self.assertEqual(
            json.loads((session / "manifest.json").read_text("utf-8")), manifest.to_dict()
        )
        self.assertEqual(manifest.facts_filename, "normalized/facts.jsonl")

    def test_pii_keys_are_rejected_recursively_without_echoing_values(self):
        forbidden_keys = (
            "name",
            "student_name",
            "phone",
            "mobile",
            "id_card",
            "address",
            "姓名",
            "学生姓名",
            "手机号",
            "身份证",
            "身份证号",
            "地址",
            "电话",
            "联系电话",
            "手机",
            "联系手机",
            "住址",
            "家庭住址",
        )
        for key in forbidden_keys:
            with self.subTest(key=key):
                store = EvidenceStore.create(self.root, self.report)
                value = "不应回显的个人信息"
                with self.assertRaises(EvidencePrivacyError) as raised:
                    store.add_context({"nested": [{key: value}]})
                self.assertNotIn(value, str(raised.exception))

    def test_fact_field_cannot_be_a_pii_key_or_echo_its_value(self):
        for field in ("student_name", "电话", "家庭住址"):
            with self.subTest(field=field):
                store = EvidenceStore.create(self.root, self.report)
                store.add_candidate(self.candidate("s1"))
                value = "不应回显的个人信息"
                fact = EvidenceFact(
                    fact_id="fact-001",
                    field=field,
                    value=value,
                    unit=None,
                    status=EvidenceStatus.REFERENCE,
                    source_ids=("s1",),
                    method="test",
                    notes="",
                )
                with self.assertRaises(EvidencePrivacyError) as raised:
                    self.persist_fact(store, fact)
                self.assertNotIn(value, str(raised.exception))

    def test_pii_shaped_evidence_identifiers_are_rejected_at_ingestion(self):
        pii_ids = (
            "13800138000",
            "138-0013-8000",
            "138.0013.8000",
            "11010519491231002X",
        )
        for identifier in pii_ids:
            with self.subTest(kind="source_id", identifier=identifier):
                store = EvidenceStore.create(self.root, self.report)
                with self.assertRaises(EvidencePrivacyError):
                    store.add_candidate(self.candidate(identifier))
            with self.subTest(kind="fact_id", identifier=identifier):
                store = EvidenceStore.create(self.root, self.report)
                store.add_candidate(self.candidate("cli-s1"))
                with self.assertRaises(EvidencePrivacyError):
                    self.persist_fact(store, self.fact(identifier, ("cli-s1",)))
            with self.subTest(kind="rejection_id", identifier=identifier):
                store = EvidenceStore.create(self.root, self.report)
                with self.assertRaises(EvidencePrivacyError):
                    store.reject_candidate(identifier, "synthetic rejection")
            with self.subTest(kind="reference_id", identifier=identifier):
                store = EvidenceStore.create(self.root, self.report)
                store.add_candidate(self.candidate("cli-s1"))
                try:
                    self.persist_fact(store, self.fact("school-policy-2026", (identifier,)))
                except Exception as error:  # The assertion below identifies the boundary class.
                    raised = error
                else:
                    raised = None
                self.assertIsInstance(raised, EvidencePrivacyError)

    def test_ordinary_semantic_evidence_identifiers_remain_valid(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("cli-s1"))
        self.persist_fact(store, self.fact("school-policy-2026", ("cli-s1",)))
        store.reject_candidate("cli-rejected", "synthetic rejection")

        manifest = store.finalize()

        self.assertRegex(manifest.session_id, r"^[0-9a-f]{32}$")
        self.assertRegex(manifest.manifest_hash, r"^sha256:[0-9a-f]{64}$")

    def test_finalize_revalidates_every_persisted_identifier_field(self):
        def populated_store():
            store = EvidenceStore.create(self.root, self.report)
            store.add_candidate(self.candidate("cli-s1"))
            self.persist_fact(store, self.fact("school-policy-2026", ("cli-s1",)))
            store.reject_candidate("cli-rejected", "synthetic rejection")
            return store

        mutations = (
            lambda store: store._candidates["cli-s1"].__setitem__(
                "source_id", "13800138000"
            ),
            lambda store: store._facts["school-policy-2026"].__setitem__(
                "fact_id", "11010519491231002X"
            ),
            lambda store: store._facts["school-policy-2026"].__setitem__(
                "source_ids", ["138-0013-8000"]
            ),
            lambda store: store._rejections.__setitem__(
                "138.0013.8000", store._rejections.pop("cli-rejected")
            ),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                store = populated_store()
                mutate(store)
                with self.assertRaises(EvidencePrivacyError):
                    store.finalize()

    def test_ingestion_snapshots_nested_fact_and_context_data(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        fact_value = {"scores": [588]}
        context = {"query": {"terms": ["example"]}}
        fact = EvidenceFact(
            fact_id="fact-001",
            field="min_score",
            value=fact_value,
            unit=None,
            status=EvidenceStatus.REFERENCE,
            source_ids=("s1",),
            method="test",
            notes="",
        )

        self.persist_fact(store, fact)
        store.add_context(context)
        fact_value["scores"].append(999)
        fact_value["student_name"] = "不应写入"
        context["query"]["terms"].append("mutated")
        context["query"]["student_name"] = "不应写入"

        manifest = store.finalize()
        facts = (store.session_path / manifest.facts_filename).read_text("utf-8")
        contexts = (store.session_path / "context.jsonl").read_text("utf-8")
        self.assertIn('"scores":[588]', facts)
        self.assertNotIn("999", facts)
        self.assertNotIn("student_name", facts)
        self.assertIn('"terms":["example"]', contexts)
        self.assertNotIn("mutated", contexts)
        self.assertNotIn("student_name", contexts)

        baseline = EvidenceStore.create(self.root, self.report)
        baseline.add_candidate(self.candidate("s1"))
        self.persist_fact(baseline,
            EvidenceFact(
                fact_id="fact-001",
                field="min_score",
                value={"scores": [588]},
                unit=None,
                status=EvidenceStatus.REFERENCE,
                source_ids=("s1",),
                method="test",
                notes="",
            )
        )
        baseline.add_context({"query": {"terms": ["example"]}})
        self.assertEqual(manifest.manifest_hash, baseline.finalize().manifest_hash)

    def test_safe_non_pii_keys_are_accepted(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_context(
            {
                "school_name": "Example School",
                "publisher": "Example Publisher",
                "source name": "Editorial name",
            }
        )
        store.finalize()

    def test_duplicate_source_and_fact_ids_are_rejected(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        with self.assertRaises(EvidenceStateError):
            store.add_candidate(self.candidate("s1"))
        with self.assertRaises(EvidenceStateError):
            store.reject_candidate("s1", "duplicate")

        self.persist_fact(store, self.fact("score-001", ("s1",)))
        with self.assertRaises(EvidenceStateError):
            self.persist_fact(store, self.fact("score-001", ("s1",)))

    def test_fact_cannot_reference_an_unregistered_source(self):
        store = EvidenceStore.create(self.root, self.report)
        with self.assertRaises(EvidenceStateError):
            self.persist_fact(store, self.fact("score-001", ("unknown",)))

    def test_generated_raw_path_requires_a_registered_safe_source_id(self):
        store = EvidenceStore.create(self.root, self.report)
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("../../outside")
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("unregistered")

        store.add_candidate(self.candidate("s1"))
        raw_path = store.raw_path_for("s1")
        self.assertEqual(raw_path.parent, store.session_path / "raw")
        self.assertTrue(raw_path.is_dir())

    def test_raw_path_rejects_a_symlink_that_escapes_raw_directory(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        outside = self.root / "outside"
        outside.mkdir()
        unsafe_link = store.session_path / "raw" / "s1"
        try:
            unsafe_link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {type(error).__name__}")

        with self.assertRaises(EvidencePathError):
            store.raw_path_for("s1")

    def test_raw_path_rejects_a_directory_swap_before_mkdir(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        outside = self.root / "outside"
        outside.mkdir()
        raw = store.session_path / "raw"

        def swap_raw(stage):
            if stage != "before-raw-mkdir":
                return
            raw.rmdir()
            try:
                raw.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {type(error).__name__}")

        store._operation_hook = swap_raw
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("s1")
        self.assertFalse((outside / "s1").exists())

    def test_raw_path_removes_created_directory_when_raw_moves_before_postcheck(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        raw = store.session_path / "raw"
        moved_raw = self.root / "moved-raw"

        def move_raw(stage):
            if stage == "after-raw-mkdir-before-postcheck":
                raw.rename(moved_raw)

        store._operation_hook = move_raw
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("s1")
        self.assertTrue(moved_raw.is_dir())
        self.assertFalse((moved_raw / "s1").exists())

    def test_raw_path_preserves_replacement_name_when_posix_cleanup_runs(self):
        if os.name == "nt":
            self.skipTest("requires POSIX dir_fd cleanup")
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        raw = store.session_path / "raw"
        moved_raw = self.root / "moved-raw"

        def replace_source_name(stage):
            if stage != "after-raw-mkdir-before-postcheck":
                return
            raw.rename(moved_raw)
            (moved_raw / "s1").rename(moved_raw / "owned-created")
            (moved_raw / "s1").mkdir()

        store._operation_hook = replace_source_name
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("s1")
        self.assertFalse((moved_raw / "owned-created").exists())
        self.assertTrue((moved_raw / "s1").is_dir())

    def test_raw_path_rejects_move_after_final_precheck_before_mkdir(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        raw = store.session_path / "raw"
        moved_raw = self.root / "moved-raw"

        def move_raw(stage):
            if stage == "after-raw-precheck-before-mkdir":
                raw.rename(moved_raw)

        store._operation_hook = move_raw
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("s1")
        self.assertTrue(moved_raw.is_dir())
        self.assertFalse((moved_raw / "s1").exists())

    def test_finalize_rejects_normalized_directory_swap_before_write(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        self.persist_fact(store, self.fact("score-001", ("s1",)))
        outside = self.root / "outside"
        outside.mkdir()
        normalized = store.session_path / "normalized"

        def swap_normalized(stage):
            if stage != "before-open:normalized/facts.jsonl":
                return
            normalized.rmdir()
            try:
                normalized.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {type(error).__name__}")

        store._operation_hook = swap_normalized
        with self.assertRaises(EvidencePathError):
            store.finalize()
        self.assertFalse((store.session_path / "manifest.json").exists())
        self.assertFalse((outside / "facts.jsonl").exists())

    def test_finalize_rejects_normalized_directory_swap_before_replace(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        self.persist_fact(store, self.fact("score-001", ("s1",)))
        outside = self.root / "outside"
        outside.mkdir()
        normalized = store.session_path / "normalized"
        moved_normalized = store.session_path / "normalized-before-swap"

        def swap_normalized(stage):
            if stage != "before-replace:normalized/facts.jsonl":
                return
            normalized.rename(moved_normalized)
            try:
                normalized.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {type(error).__name__}")

        store._operation_hook = swap_normalized
        with self.assertRaises(EvidencePathError):
            store.finalize()
        self.assertFalse((store.session_path / "manifest.json").exists())
        self.assertFalse((outside / "facts.jsonl").exists())

    def test_finalize_removes_manifest_when_session_moves_after_replace(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        moved_session = self.root / "moved-session"

        def move_session(stage):
            if stage == "after-replace-before-postcheck:manifest.json":
                store.session_path.rename(moved_session)

        store._operation_hook = move_session
        with self.assertRaises(EvidencePathError):
            store.finalize()
        self.assertTrue(moved_session.is_dir())
        self.assertFalse((moved_session / "manifest.json").exists())

    def test_finalize_preserves_replacement_manifest_when_posix_cleanup_runs(self):
        if os.name == "nt":
            self.skipTest("requires POSIX dir_fd cleanup")
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        moved_session = self.root / "moved-session"

        def replace_manifest_name(stage):
            if stage != "after-replace-before-postcheck:manifest.json":
                return
            store.session_path.rename(moved_session)
            (moved_session / "manifest.json").rename(moved_session / "owned-created")
            (moved_session / "manifest.json").write_text("unrelated", encoding="utf-8")

        store._operation_hook = replace_manifest_name
        with self.assertRaises(EvidencePathError):
            store.finalize()
        self.assertFalse((moved_session / "owned-created").exists())
        self.assertEqual((moved_session / "manifest.json").read_text("utf-8"), "unrelated")
        self.assertIsNone(store._manifest)

    def test_finalize_rejects_move_after_final_precheck_before_replace(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        moved_session = self.root / "moved-session"

        def move_session(stage):
            if stage == "after-final-precheck-before-replace:manifest.json":
                store.session_path.rename(moved_session)

        store._operation_hook = move_session
        with self.assertRaises(EvidencePathError):
            store.finalize()
        self.assertTrue(moved_session.is_dir())
        self.assertFalse((moved_session / "manifest.json").exists())
        self.assertFalse(list(moved_session.glob(".manifest.json.*.tmp")))
        self.assertIsNone(store._manifest)

    def test_writes_after_finalize_fail_closed(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        store.finalize()

        with self.assertRaises(EvidenceStateError):
            store.add_context({"query": "later"})
        with self.assertRaises(EvidenceStateError):
            store.add_candidate(self.candidate("s2"))
        with self.assertRaises(EvidenceStateError):
            self.persist_fact(store, self.fact("score-001", ("s1",)))
        with self.assertRaises(EvidenceStateError):
            store.reject_candidate("s3", "later")

    def test_manifest_is_not_published_when_atomic_replace_fails(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        real_replace = os.replace
        seen_replace_kwargs = []

        def fail_manifest_replace(source, destination, **kwargs):
            seen_replace_kwargs.append(kwargs)
            if Path(destination).name == "manifest.json":
                raise OSError("synthetic manifest rename failure")
            return real_replace(source, destination, **kwargs)

        with self.assertRaises(OSError):
            fail_manifest_replace(
                "temporary-name",
                "manifest.json",
                src_dir_fd=11,
                dst_dir_fd=11,
            )
        self.assertEqual(seen_replace_kwargs, [{"src_dir_fd": 11, "dst_dir_fd": 11}])

        with patch("scripts.evidence.os.replace", side_effect=fail_manifest_replace):
            with self.assertRaises(EvidencePathError):
                store.finalize()

        self.assertFalse((store.session_path / "manifest.json").exists())
        self.assertIsNotNone(store.finalize())

    def test_create_requires_an_existing_absolute_local_directory(self):
        with self.assertRaises(EvidencePathError):
            EvidenceStore.create(Path("relative-workspace"), self.report)
        with self.assertRaises(EvidencePathError):
            EvidenceStore.create(self.root / "missing", self.report)
        file_root = self.root / "not-a-directory"
        file_root.write_text("x", encoding="utf-8")
        with self.assertRaises(EvidencePathError):
            EvidenceStore.create(file_root, self.report)
        blocked_evidence_parent = self.root / "evidence"
        blocked_evidence_parent.write_text("x", encoding="utf-8")
        with self.assertRaises(EvidencePathError):
            EvidenceStore.create(self.root, self.report)


if __name__ == "__main__":
    unittest.main()
