import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


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
