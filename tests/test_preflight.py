import json
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.contracts import CapabilityTier
from scripts.preflight import detect_capabilities


class PreflightTest(unittest.TestCase):
    @staticmethod
    def all_modules(name):
        return True

    @staticmethod
    def no_modules(name):
        return False

    def test_full_requires_search_browse_vision_and_parsers(self):
        report = detect_capabilities(
            {"search", "browse", "vision"}, module_probe=self.all_modules
        )
        self.assertEqual(report.tier, CapabilityTier.FULL)

    def test_missing_vision_is_standard_not_full(self):
        report = detect_capabilities(
            {"search", "browse"}, module_probe=self.all_modules
        )
        self.assertEqual(report.tier, CapabilityTier.STANDARD)

    def test_no_network_capability_is_offline(self):
        report = detect_capabilities(set(), module_probe=self.no_modules)
        self.assertEqual(report.tier, CapabilityTier.OFFLINE)

    def test_missing_parser_is_reported_as_standard_degradation(self):
        report = detect_capabilities(
            {"search", "browse", "vision"},
            module_probe=lambda name: name != "pdfplumber",
        )
        self.assertEqual(report.tier, CapabilityTier.STANDARD)
        self.assertIn("pdfplumber", report.missing_capabilities)
        self.assertTrue(report.degradations)

    def test_host_capabilities_are_explicit_and_stably_sorted(self):
        report = detect_capabilities({"vision", "search", "browse"}, self.all_modules)
        self.assertEqual(report.host_capabilities, ("browse", "search", "vision"))
        self.assertEqual(report.available_capabilities, ("browse", "search", "vision"))

    def test_module_probe_is_injected_without_importing_modules(self):
        seen = []

        def probe(name):
            seen.append(name)
            return name == "docx"

        report = detect_capabilities({"search", "browse"}, module_probe=probe)
        self.assertEqual(seen, ["docx", "openpyxl", "pdfplumber"])
        self.assertEqual(report.optional_modules, ("docx",))

    def test_python_below_supported_version_is_reported(self):
        with patch("scripts.preflight.sys.version_info", (3, 9, 18)):
            report = detect_capabilities({"search", "browse"}, self.all_modules)
        self.assertIn("python>=3.10", report.missing_capabilities)
        self.assertTrue(any("python" in item for item in report.degradations))


class PreflightCliTest(unittest.TestCase):
    def test_cli_emits_stable_json_and_exits_zero(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "preflight.py"),
                "--host-capability",
                "search",
                "--host-capability",
                "browse",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            list(payload),
            [
                "tier",
                "host_capabilities",
                "available_capabilities",
                "missing_capabilities",
                "degradations",
                "python_version",
                "optional_modules",
            ],
        )
        self.assertEqual(payload["tier"], "standard")


if __name__ == "__main__":
    unittest.main()
