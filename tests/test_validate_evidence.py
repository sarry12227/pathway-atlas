import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.evidence import EvidenceStore


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

    def copy_fixture(self, name: str) -> Path:
        destination = self.temp_root / name
        shutil.copytree(FIXTURES / name, destination)
        return destination

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
