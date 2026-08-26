import json
import pathlib
import unittest
from dataclasses import FrozenInstanceError, is_dataclass
from enum import Enum

from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceFact,
    EvidenceManifest,
    EvidenceStatus,
    FactClaim,
    SourceCandidate,
    SourceTier,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ContractTest(unittest.TestCase):
    def test_manifest_machine_identifiers_use_exact_public_grammars(self):
        valid_session = "a13800138000bcdef123456789abcdef"
        valid_hash = "sha256:" + valid_session * 2

        manifest = EvidenceManifest(
            session_id=valid_session,
            manifest_hash=valid_hash,
        )

        self.assertEqual(manifest.session_id, valid_session)
        self.assertEqual(manifest.manifest_hash, valid_hash)
        for invalid_session in (
            "session-123",
            valid_session.upper(),
            "a" * 31,
            "g" * 32,
        ):
            with self.subTest(session_id=invalid_session), self.assertRaises(ValueError):
                EvidenceManifest(session_id=invalid_session)
        for invalid_hash in (
            "sha256:manifest",
            "sha256:" + "A" * 64,
            "sha256:" + "0" * 63,
            "md5:" + "0" * 64,
            None,
            False,
            0,
        ):
            with self.subTest(manifest_hash=invalid_hash), self.assertRaises(ValueError):
                EvidenceManifest(manifest_hash=invalid_hash)

    def test_fact_serializes_stable_enum_values(self):
        fact = EvidenceFact(
            fact_id="score-001",
            field="min_score",
            value=588,
            unit="分",
            status=EvidenceStatus.REFERENCE,
            source_ids=("s1", "s2", "s3"),
            method="three-source-consensus",
            notes="",
        )
        payload = fact.to_dict()
        self.assertEqual(payload["status"], "reference")
        self.assertEqual(payload["source_ids"], ["s1", "s2", "s3"])
        self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False)), payload)

    def test_source_tier_values_are_host_neutral(self):
        self.assertEqual(SourceTier.A.value, "A")
        self.assertEqual([item.value for item in SourceTier], ["A", "B", "C"])

    def test_status_and_capability_vocabularies_are_exact(self):
        self.assertEqual(
            [item.value for item in EvidenceStatus],
            [
                "official",
                "corroborated",
                "reference",
                "inferred",
                "conflict",
                "missing",
                "masked",
                "partial",
            ],
        )
        self.assertEqual([item.value for item in CapabilityTier], ["full", "standard", "offline"])

    def test_every_contract_dataclass_is_frozen_and_json_safe(self):
        candidate = SourceCandidate(
            source_id="s1",
            url="https://example.test/source",
            publisher="Example",
            tier=SourceTier.A,
            published_at=None,
            retrieved_at="2026-08-23T00:00:00Z",
            content_hash="sha256:abc",
            citation_root="https://example.test",
            summary="Synthetic source",
        )
        claim = FactClaim(
            field="min_score",
            value=588,
            unit="分",
            source_id="s1",
            method="table-cell",
        )
        report = CapabilityReport(
            tier=CapabilityTier.STANDARD,
            host_capabilities=("search", "browse"),
            available_capabilities=("search", "browse"),
            missing_capabilities=("vision",),
            degradations=("skip-image-tables",),
            python_version="3.10.0",
            optional_modules=("openpyxl",),
        )
        manifest = EvidenceManifest(
            schema_version="1.0",
            session_id="a" * 32,
            capability_tier=CapabilityTier.STANDARD,
            candidates_filename="candidates.jsonl",
            facts_filename="facts.jsonl",
            rejected_count=1,
            manifest_hash="sha256:" + "b" * 64,
        )

        for item in (candidate, claim, report, manifest):
            self.assertTrue(is_dataclass(item))
            self.assertEqual(json.loads(json.dumps(item.to_dict())), item.to_dict())
            with self.assertRaises(FrozenInstanceError):
                setattr(item, next(iter(item.__dict__)), None)

        self.assertEqual(candidate.to_dict()["tier"], "A")
        self.assertEqual(manifest.to_dict()["capability_tier"], "standard")

    def test_schemas_validate_serialized_contract_shape(self):
        candidate = SourceCandidate(
            "s1",
            "https://example.test/source",
            "Example",
            SourceTier.C,
            "2026-08-01",
            "2026-08-23T00:00:00Z",
            "sha256:abc",
            "https://example.test",
            "Synthetic source",
        )
        fact = EvidenceFact(
            "score-001",
            "min_score",
            588,
            "分",
            EvidenceStatus.REFERENCE,
            ("s1",),
            "table-cell",
            "",
        )
        manifest = EvidenceManifest(
            "1.0",
            "a" * 32,
            CapabilityTier.STANDARD,
            "candidates.jsonl",
            "facts.jsonl",
            0,
            "sha256:" + "b" * 64,
        )
        report = CapabilityReport(CapabilityTier.STANDARD)

        capability_schema = json.loads(
            (ROOT / "schemas" / "capability-report.schema.json").read_text("utf-8")
        )
        evidence_schema = json.loads(
            (ROOT / "schemas" / "evidence-bundle.schema.json").read_text("utf-8")
        )
        self.assertFalse(capability_schema["additionalProperties"])
        self.assertFalse(evidence_schema["additionalProperties"])
        self.assertEqual(set(capability_schema["required"]), set(report.to_dict()))
        bundle = {
            "manifest": manifest.to_dict(),
            "candidates": [candidate.to_dict()],
            "facts": [fact.to_dict()],
        }
        self.assertEqual(set(evidence_schema["required"]), set(bundle))
        self.assertNotIn("unexpected", evidence_schema["properties"])
        manifest_schema = evidence_schema["$defs"]["manifest"]["properties"]
        self.assertEqual(
            manifest_schema.get("session_id", {}).get("pattern"),
            "^[0-9a-f]{32}$",
        )
        self.assertEqual(
            manifest_schema.get("manifest_hash", {}).get("pattern"),
            "^sha256:[0-9a-f]{64}$",
        )

    def test_manifest_defaults_include_version_and_random_session(self):
        first = EvidenceManifest()
        second = EvidenceManifest()
        self.assertEqual(first.schema_version, "1.0")
        self.assertTrue(first.session_id)
        self.assertNotEqual(first.session_id, second.session_id)

    def test_non_json_values_fail_at_serialization_boundary(self):
        class CustomValue:
            pass

        for value, type_name in (
            ({"nested", "set"}, "set"),
            (b"bytes", "bytes"),
            (CustomValue(), "CustomValue"),
        ):
            with self.subTest(type_name=type_name):
                claim = FactClaim(
                    field="unsupported",
                    value={"nested": value},
                    unit=None,
                    source_id="s1",
                    method="test",
                )
                with self.assertRaisesRegex(TypeError, type_name):
                    claim.to_dict()


if __name__ == "__main__":
    unittest.main()
