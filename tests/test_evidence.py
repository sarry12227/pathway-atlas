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

    def build_bundle(self, order):
        store = EvidenceStore.create(self.root, self.report)
        for source_id in order:
            store.add_candidate(self.candidate(source_id))
        store.add_fact(self.fact("score-001", tuple(reversed(order))))
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
        store.add_fact(self.fact("score-002", ("s2",)))
        store.add_fact(self.fact("score-001", ("s1",)))
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
                    store.add_fact(fact)
                self.assertNotIn(value, str(raised.exception))

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

        store.add_fact(fact)
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
        baseline.add_fact(
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

        store.add_fact(self.fact("score-001", ("s1",)))
        with self.assertRaises(EvidenceStateError):
            store.add_fact(self.fact("score-001", ("s1",)))

    def test_fact_cannot_reference_an_unregistered_source(self):
        store = EvidenceStore.create(self.root, self.report)
        with self.assertRaises(EvidenceStateError):
            store.add_fact(self.fact("score-001", ("unknown",)))

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

    def test_finalize_rejects_normalized_directory_swap_before_write(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        store.add_fact(self.fact("score-001", ("s1",)))
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
        store.add_fact(self.fact("score-001", ("s1",)))
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

    def test_writes_after_finalize_fail_closed(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        store.finalize()

        with self.assertRaises(EvidenceStateError):
            store.add_context({"query": "later"})
        with self.assertRaises(EvidenceStateError):
            store.add_candidate(self.candidate("s2"))
        with self.assertRaises(EvidenceStateError):
            store.add_fact(self.fact("score-001", ("s1",)))
        with self.assertRaises(EvidenceStateError):
            store.reject_candidate("s3", "later")

    def test_manifest_is_not_published_when_atomic_replace_fails(self):
        store = EvidenceStore.create(self.root, self.report)
        store.add_candidate(self.candidate("s1"))
        real_replace = os.replace

        def fail_manifest_replace(source, destination):
            if Path(destination).name == "manifest.json":
                raise OSError("synthetic manifest rename failure")
            return real_replace(source, destination)

        with patch("scripts.evidence.os.replace", side_effect=fail_manifest_replace):
            with self.assertRaises(OSError):
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
