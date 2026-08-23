import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace

from scripts.evidence import EvidenceStore
from scripts.validate_evidence import (
    ValidatedEvidenceSnapshot,
    validate_bundle_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "evidence"
VALIDATOR = ROOT / "scripts" / "validate_evidence.py"


class ValidateEvidenceCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def run_cli(self, bundle: str | Path):
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(bundle)],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        try:
            summary = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"validator stdout was not JSON: {error}: {result.stdout!r}")
        return result, summary

    def copy_fixture(self, name: str, destination_name: str | None = None) -> Path:
        destination = self.temp_root / (destination_name or name)
        shutil.copytree(FIXTURES / name, destination)
        return destination

    @staticmethod
    def rewrite_candidates(bundle: Path, transform) -> None:
        path = bundle / "candidates.jsonl"
        candidates = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        transformed = [transform(index, candidate) for index, candidate in enumerate(candidates)]
        path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                for item in transformed
            ),
            encoding="utf-8",
            newline="\n",
        )

    @staticmethod
    def rewrite_facts(bundle: Path, transform) -> None:
        path = bundle / "normalized" / "facts.jsonl"
        facts = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        transformed = [transform(index, fact) for index, fact in enumerate(facts)]
        path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                for item in transformed
            ),
            encoding="utf-8",
            newline="\n",
        )

    @staticmethod
    def rewrite_manifest_hash(bundle: Path) -> None:
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        capability = json.loads((bundle / "capability.json").read_text("utf-8"))
        rejection_lines = (bundle / "rejections.jsonl").read_text("utf-8").splitlines()
        store = object.__new__(EvidenceStore)
        store._capability = capability
        store._rejections = {str(index): None for index in range(len(rejection_lines))}
        records = {
            name: (bundle / name).read_text("utf-8")
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
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def test_consensus_fixture_passes_with_machine_readable_summary(self):
        result, summary = self.run_cli(FIXTURES / "three-source-consensus")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(summary["valid"], True)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["candidate_count"], 3)
        self.assertEqual(summary["fact_count"], 1)

    def test_public_snapshot_is_factory_only_deep_frozen_and_hash_bound(self):
        result = validate_bundle_snapshot(FIXTURES / "three-source-consensus")

        self.assertEqual(result.issues, ())
        self.assertIsInstance(result.snapshot, ValidatedEvidenceSnapshot)
        snapshot = result.snapshot
        assert snapshot is not None
        self.assertEqual(snapshot.retrieval_dates, ("2026-08-23",))
        self.assertEqual(snapshot.manifest.manifest_hash, snapshot.manifest_hash)
        payload = snapshot.facts[0].to_dict()
        payload["field"] = "tampered"
        self.assertNotEqual(snapshot.facts[0].to_dict()["field"], "tampered")
        with self.assertRaises(FrozenInstanceError):
            snapshot.retrieval_dates = ("2026-08-24",)
        with self.assertRaises(TypeError):
            replace(snapshot, retrieval_dates=("2026-08-24",))
        with self.assertRaises(TypeError):
            ValidatedEvidenceSnapshot()

    def test_invalid_bundle_snapshot_returns_issues_and_no_data(self):
        result = validate_bundle_snapshot(FIXTURES / "repost-conflict")

        self.assertIsNone(result.snapshot)
        self.assertTrue(result.issues)

    def test_rehashed_future_candidate_date_stays_bound_to_snapshot(self):
        bundle = self.copy_fixture("three-source-consensus")

        def future(_index, candidate):
            candidate["retrieved_at"] = "2099-01-02T00:00:00Z"
            return candidate

        self.rewrite_candidates(bundle, future)
        self.rewrite_manifest_hash(bundle)

        result = validate_bundle_snapshot(bundle)

        self.assertEqual(result.issues, ())
        assert result.snapshot is not None
        self.assertEqual(result.snapshot.retrieval_dates, ("2099-01-02",))

    def test_fixture_artifacts_checkout_with_hash_stable_lf_endings(self):
        fixture_files = sorted(
            path.relative_to(ROOT).as_posix()
            for path in FIXTURES.rglob("*")
            if path.is_file()
        )
        result = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", *fixture_files],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        attributes: dict[str, dict[str, str]] = {name: {} for name in fixture_files}
        for line in result.stdout.splitlines():
            name, attribute, value = line.split(": ", 2)
            attributes[name][attribute] = value
        self.assertEqual(len(attributes), 12)
        for name in fixture_files:
            with self.subTest(name=name):
                self.assertEqual(attributes[name], {"text": "set", "eol": "lf"})

    def test_repost_conflict_fixture_fails_independence_policy(self):
        result, summary = self.run_cli(FIXTURES / "repost-conflict")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(summary["valid"], False)
        self.assertIn("independent_sources", {item["code"] for item in summary["errors"]})
        self.assertEqual(summary["independent_source_count"], 1)

    def test_same_site_candidates_cannot_manufacture_cli_consensus(self):
        bundle = self.copy_fixture("three-source-consensus")

        def use_same_site(index, candidate):
            candidate["url"] = (
                "https://Example.TEST:443/first"
                if index == 0
                else "https://example.test./second"
                if index == 1
                else "https://www.example.test/third"
            )
            return candidate

        self.rewrite_candidates(bundle, use_same_site)
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("independent_sources", {item["code"] for item in summary["errors"]})
        self.assertEqual(summary["independent_source_count"], 1)

    def test_blank_identity_candidate_is_excluded_from_cli_independence(self):
        bundle = self.copy_fixture("three-source-consensus")

        def blank_first_publisher(index, candidate):
            if index == 0:
                candidate["publisher"] = ""
            return candidate

        self.rewrite_candidates(bundle, blank_first_publisher)
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("schema", {item["code"] for item in summary["errors"]})
        self.assertEqual(summary["independent_source_count"], 2)

    def configure_two_source_b_fact(self, bundle: Path, roots: tuple[str, str]) -> None:
        def make_b_sources(index, candidate):
            if index < 2:
                candidate["tier"] = "B"
                candidate["citation_root"] = roots[index]
            return candidate

        def make_b_fact(_index, fact):
            fact["source_ids"] = ["s1", "s2"]
            fact["status"] = "corroborated"
            fact["method"] = "two-source-consensus"
            return fact

        self.rewrite_candidates(bundle, make_b_sources)
        self.rewrite_facts(bundle, make_b_fact)
        self.rewrite_manifest_hash(bundle)

    def test_opaque_b_roots_fail_cli_provenance_validation(self):
        bundle = self.copy_fixture("three-source-consensus")
        self.configure_two_source_b_fact(bundle, ("opaque-root-one", "opaque-root-two"))

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("schema", {item["code"] for item in summary["errors"]})

    def test_empty_port_b_roots_fail_cli_without_echoing_urls(self):
        bundle = self.copy_fixture("three-source-consensus")
        roots = ("https://alpha.test:/original", "https://beta.test:/original")
        self.configure_two_source_b_fact(bundle, roots)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("schema", {item["code"] for item in summary["errors"]})
        for root in roots:
            self.assertNotIn(root, result.stdout)
            self.assertNotIn(root, result.stderr)

    def test_two_independent_http_b_roots_still_pass_cli_replay(self):
        bundle = self.copy_fixture("three-source-consensus")
        self.configure_two_source_b_fact(
            bundle,
            ("https://upstream-one.test/original", "http://upstream-two.test/original"),
        )

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(summary["errors"], [])

    def test_userinfo_citation_root_is_rejected_without_echoing_it(self):
        bundle = self.copy_fixture("three-source-consensus")
        secret = "sensitive-password"
        self.configure_two_source_b_fact(
            bundle,
            (f"https://user:{secret}@upstream-one.test/original", "https://upstream-two.test/original"),
        )

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("schema", {item["code"] for item in summary["errors"]})
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    def test_schema_requires_nonempty_http_candidate_and_provenance_urls(self):
        schema = json.loads((ROOT / "schemas" / "evidence-bundle.schema.json").read_text("utf-8"))
        properties = schema["$defs"]["sourceCandidate"]["properties"]

        for field in ("url", "citation_root"):
            with self.subTest(field=field):
                contract = properties[field]
                self.assertGreaterEqual(contract["minLength"], 1)
                pattern = re.compile(contract["pattern"])
                self.assertIsNotNone(pattern.search("https://example.test/path"))
                self.assertIsNone(pattern.search("opaque-root"))
                self.assertIsNone(pattern.search("/relative/path"))

    def test_rejected_candidate_cannot_support_an_exact_fact(self):
        bundle = self.copy_fixture("three-source-consensus")
        rejection_reason = "sensitive-reason-must-not-be-echoed"
        (bundle / "rejections.jsonl").write_text(
            json.dumps(
                {"source_id": "s1", "reason": rejection_reason},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["rejected_count"] = 1
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("rejected_source", {item["code"] for item in summary["errors"]})
        self.assertNotIn(rejection_reason, result.stdout)
        self.assertNotIn(rejection_reason, result.stderr)

    def test_unsafe_rejection_source_id_is_rejected(self):
        bundle = self.copy_fixture("three-source-consensus")
        (bundle / "rejections.jsonl").write_text(
            '{"reason":"synthetic","source_id":"../../outside"}\n',
            encoding="utf-8",
            newline="\n",
        )
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["rejected_count"] = 1
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("rejections", {item["code"] for item in summary["errors"]})

    def test_duplicate_rejection_source_id_is_rejected(self):
        bundle = self.copy_fixture("three-source-consensus")
        (bundle / "rejections.jsonl").write_text(
            '{"reason":"first","source_id":"r1"}\n'
            '{"reason":"second","source_id":"r1"}\n',
            encoding="utf-8",
            newline="\n",
        )
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["rejected_count"] = 2
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("rejections", {item["code"] for item in summary["errors"]})

    def test_fabricated_fact_method_is_rejected_after_policy_replay(self):
        bundle = self.copy_fixture("three-source-consensus")
        facts = bundle / "normalized" / "facts.jsonl"
        facts.write_text(
            facts.read_text("utf-8").replace(
                '"method":"three-source-consensus"',
                '"method":"fabricated-method"',
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("method", {item["code"] for item in summary["errors"]})

    def test_inferred_exact_fact_without_derivation_contract_is_rejected(self):
        bundle = self.copy_fixture("three-source-consensus")

        def fabricate_inference(_index, fact):
            fact["status"] = "inferred"
            fact["source_ids"] = []
            fact["method"] = "fabricated-method"
            return fact

        self.rewrite_facts(bundle, fabricate_inference)
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unsupported_derivation", {item["code"] for item in summary["errors"]})

    def test_inferred_fact_with_sources_and_public_method_is_still_not_replayable_in_v1(self):
        bundle = self.copy_fixture("three-source-consensus")

        def public_but_unversioned_inference(_index, fact):
            fact["status"] = "inferred"
            fact["method"] = "published-headcount-method"
            return fact

        self.rewrite_facts(bundle, public_but_unversioned_inference)
        self.rewrite_manifest_hash(bundle)

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unsupported_derivation", {item["code"] for item in summary["errors"]})

    def test_non_consensus_statuses_cannot_carry_exact_values(self):
        for status in ("conflict", "missing", "masked", "partial"):
            with self.subTest(status=status):
                bundle = self.copy_fixture("three-source-consensus", f"exact-{status}")

                def set_status(_index, fact):
                    fact["status"] = status
                    fact["method"] = "reported-boundary"
                    return fact

                self.rewrite_facts(bundle, set_status)
                self.rewrite_manifest_hash(bundle)

                result, summary = self.run_cli(bundle)

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("unsupported_status", {item["code"] for item in summary["errors"]})

    def test_masked_and_partial_facts_require_sources_and_a_method(self):
        for status in ("masked", "partial"):
            with self.subTest(status=status):
                bundle = self.copy_fixture("three-source-consensus", f"unsupported-{status}")

                def remove_support(_index, fact):
                    fact["status"] = status
                    fact["value"] = None
                    fact["source_ids"] = []
                    fact["method"] = ""
                    return fact

                self.rewrite_facts(bundle, remove_support)
                self.rewrite_manifest_hash(bundle)

                result, summary = self.run_cli(bundle)

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("unsupported_status", {item["code"] for item in summary["errors"]})

    def test_masked_and_partial_null_facts_with_known_support_are_allowed(self):
        for status in ("masked", "partial"):
            with self.subTest(status=status):
                bundle = self.copy_fixture("three-source-consensus", f"supported-{status}")

                def retain_supported_boundary(_index, fact):
                    fact["status"] = status
                    fact["value"] = None
                    fact["source_ids"] = ["s1"]
                    fact["method"] = "reported-boundary"
                    return fact

                self.rewrite_facts(bundle, retain_supported_boundary)
                self.rewrite_manifest_hash(bundle)

                result, summary = self.run_cli(bundle)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(summary["errors"], [])

    def test_manifest_tampering_fails_hash_validation(self):
        bundle = self.copy_fixture("three-source-consensus")
        candidates = bundle / "candidates.jsonl"
        candidates.write_text(
            candidates.read_text("utf-8").replace("Publisher One", "Changed Publisher"),
            encoding="utf-8",
            newline="\n",
        )

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest_hash", {item["code"] for item in summary["errors"]})

    def test_unknown_evidence_status_fails_recursive_schema_validation(self):
        bundle = self.copy_fixture("three-source-consensus")
        facts = bundle / "normalized" / "facts.jsonl"
        facts.write_text(
            facts.read_text("utf-8").replace('"status":"reference"', '"status":"certain"'),
            encoding="utf-8",
            newline="\n",
        )

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("schema", {item["code"] for item in summary["errors"]})

    def test_chinese_pii_key_is_rejected_without_echoing_value(self):
        bundle = self.copy_fixture("three-source-consensus")
        secret = "13800138000-不应回显"
        (bundle / "context.jsonl").write_text(
            json.dumps({"nested": {"电话": secret}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("privacy", {item["code"] for item in summary["errors"]})
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    def test_missing_required_artifact_fails_closed(self):
        bundle = self.copy_fixture("three-source-consensus")
        (bundle / "normalized" / "facts.jsonl").unlink()

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("artifact", {item["code"] for item in summary["errors"]})

    def test_manifest_path_escape_is_rejected(self):
        bundle = self.copy_fixture("three-source-consensus")
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["facts_filename"] = "../outside.jsonl"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("artifact", {item["code"] for item in summary["errors"]})

    def test_symlinked_artifact_is_rejected(self):
        bundle = self.copy_fixture("three-source-consensus")
        external = self.temp_root / "external-context.jsonl"
        external.write_text('{}\n', encoding="utf-8")
        context = bundle / "context.jsonl"
        context.unlink()
        try:
            context.symlink_to(external)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {type(error).__name__}")

        result, summary = self.run_cli(bundle)

        self.assertEqual(result.returncode, 2)
        self.assertIn("artifact", {item["code"] for item in summary["errors"]})


if __name__ == "__main__":
    unittest.main()
