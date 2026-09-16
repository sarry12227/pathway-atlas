"""Failures remain actionable without exposing host paths or fabricated progress."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import host_workflow
from scripts.adapters import StructuredAdapterError


class WorkflowFeedbackTest(unittest.TestCase):
    def test_real_missing_input_is_structured_and_does_not_claim_saved_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                [sys.executable, "-m", "scripts.host_workflow", "start", "--workspace", str(root / "work"),
                 "--answers", str(root / "private-input.json"), "--confirmed"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertNotIn(temporary, result.stderr)
            self.assertNotIn("private-input.json", result.stderr)
            feedback = json.loads(result.stderr)
            self.assertFalse(feedback["report_generated"])
            self.assertEqual(feedback["error_code"], "input_unavailable")
            self.assertFalse(feedback["user_action_required"])
            self.assertNotIn("checkpoint", feedback["user_message"])
            self.assertNotIn("已保存", feedback["user_message"])
            self.assertFalse((root / "work").exists())

    def test_resume_validation_errors_do_not_echo_untrusted_details(self):
        cases = [
            (ValueError("private-profile digest disagrees"), "input_invalid", 2),
            (StructuredAdapterError("private source extraction failed"), "source_unreadable", 2),
            (PermissionError("private output path denied"), "storage_unavailable", 2),
            (OSError("private device failed"), "operation_unavailable", 2),
        ]
        for error, code, exit_code in cases:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / "existing-progress.txt"
                marker.write_text("retain existing data", encoding="utf-8")
                stderr, stdout = io.StringIO(), io.StringIO()
                with patch.object(host_workflow.PlanningWorkflow, "resume", side_effect=error), redirect_stderr(stderr), redirect_stdout(stdout):
                    returned = host_workflow.main(["next", "--workspace", tmp, "--session", "sample"])
                self.assertEqual(returned, exit_code)
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn(str(error), stderr.getvalue())
                feedback = json.loads(stderr.getvalue())
                self.assertEqual(feedback["error_code"], code)
                self.assertFalse(feedback["ok"])
                self.assertFalse(feedback["user_action_required"])
                self.assertTrue(feedback["host_action"])
                self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in feedback["user_message"]))
                self.assertEqual(marker.read_text(encoding="utf-8"), "retain existing data")

    def test_missing_optional_module_does_not_falsely_promise_a_markdown_report(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch.object(host_workflow.PlanningWorkflow, "resume", side_effect=ModuleNotFoundError("private.optional")), redirect_stderr(stderr):
            returned = host_workflow.main(["finish", "--workspace", tmp, "--session", "sample", "--format", "docx"])
        self.assertEqual(returned, 3)
        feedback = json.loads(stderr.getvalue())
        self.assertEqual(feedback["error_code"], "capability_unavailable")
        self.assertFalse(feedback["report_generated"])
        self.assertNotIn("private.optional", stderr.getvalue())
        self.assertNotIn("Markdown remains available", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
