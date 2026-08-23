from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree
from zipfile import ZipFile

from scripts.province_registry import (
    SubjectSelectionError,
    discover_provinces,
    validate_subject_selection,
)
from scripts.school_recommend import parse_secondary_subjects


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
PROVINCES = FIXTURES / "provinces"
EVIDENCE = FIXTURES / "evidence"
PROFILE = FIXTURES / "profiles" / "demo.json"


class CliResult:
    def __init__(self, process: subprocess.CompletedProcess[bytes]):
        self.returncode = process.returncode
        # Strict decoding is the UTF-8 contract: replacement characters are not
        # accepted merely because a terminal happened to display the output.
        self.stdout = process.stdout.decode("utf-8", errors="strict")
        self.stderr = process.stderr.decode("utf-8", errors="strict")


def _docx_text(path: Path) -> str:
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with ZipFile(path) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))
    return "".join(node.text or "" for node in root.iter(namespace + "t"))


class DeterministicEngineCliSmokeTest(unittest.TestCase):
    """One offline replay gate over the public deterministic CLI boundary."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.sandbox = Path(cls.temporary.name)
        cls.site = cls.sandbox / "startup"
        cls.site.mkdir()
        (cls.site / "sitecustomize.py").write_text(
            """
import importlib.abc
import os
from pathlib import Path
import socket
import sys

Path(os.environ["SHENGXUE_SENTINEL_ACTIVE"]).write_text("active", encoding="utf-8")

def _network_attempt(*args, **kwargs):
    Path(os.environ["SHENGXUE_NETWORK_ATTEMPT"]).write_text("attempted", encoding="utf-8")
    raise AssertionError("network access attempted during deterministic replay")

socket.create_connection = _network_attempt
socket.getaddrinfo = _network_attempt
socket.socket.connect = _network_attempt
socket.socket.connect_ex = _network_attempt

if os.environ.get("SHENGXUE_BLOCK_DOCX") == "1":
    class _BlockDocx(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "docx" or fullname.startswith("docx."):
                raise ModuleNotFoundError("document capability blocked by smoke sentinel")
            return None
    sys.meta_path.insert(0, _BlockDocx())
""".lstrip(),
            encoding="utf-8",
            newline="\n",
        )
        configured = os.environ.get("SHENGXUE_DOCUMENTS_PYTHON")
        cls.documents_python = Path(configured) if configured else Path(sys.executable)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _run(
        self,
        *arguments: object,
        python: Path | None = None,
        block_docx: bool = False,
    ) -> CliResult:
        executable = Path(sys.executable) if python is None else python
        active = self.sandbox / "sentinel-active"
        attempted = self.sandbox / "network-attempt"
        active.unlink(missing_ok=True)
        attempted.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONPATH"] = str(self.site)
        environment["SHENGXUE_SENTINEL_ACTIVE"] = str(active)
        environment["SHENGXUE_NETWORK_ATTEMPT"] = str(attempted)
        if block_docx:
            environment["SHENGXUE_BLOCK_DOCX"] = "1"
        else:
            environment.pop("SHENGXUE_BLOCK_DOCX", None)
        process = subprocess.run(
            [str(executable), *(str(item) for item in arguments)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertTrue(active.is_file(), "offline startup sentinel was not loaded")
        self.assertFalse(
            attempted.exists(),
            "a socket/DNS boundary was invoked during deterministic replay",
        )
        return CliResult(process)

    def _script(
        self,
        name: str,
        *arguments: object,
        python: Path | None = None,
        block_docx: bool = False,
    ) -> CliResult:
        return self._run(
            SCRIPTS / name,
            *arguments,
            python=python,
            block_docx=block_docx,
        )

    def _assert_safe_failure(self, result: CliResult, expected: int = 2):
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        visible = result.stdout + result.stderr
        for forbidden in (
            "Traceback",
            str(ROOT),
            str(self.sandbox),
            ".worktrees",
            "C:",
            "\\\\",
            "张三",
            "13800138000",
            "C:\\Users\\",
            "/home/",
        ):
            self.assertNotIn(forbidden, visible)

    def _assert_document_runtime(self):
        probe = subprocess.run(
            [
                str(self.documents_python),
                "-c",
                "import docx; assert tuple(map(int, docx.__version__.split('.')[:2])) >= (1, 1)",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            probe.returncode,
            0,
            "DOCX smoke requires the declared documents extra; set "
            "SHENGXUE_DOCUMENTS_PYTHON to a Python with python-docx>=1.1.\n"
            + probe.stderr.decode("utf-8", errors="replace"),
        )

    def test_data_validation_and_real_33_subject_semantics(self):
        """Catches mode-specific validation or a fake 3+3 parser path."""
        for fixture in ("demo-312", "demo-33"):
            with self.subTest(fixture=fixture):
                result = self._script("validate_data.py", PROVINCES / fixture)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertTrue(payload["valid"])
                self.assertEqual(payload["issues"], [])

        invalid = self._script("validate_data.py", PROVINCES / "duplicate-program")
        self._assert_safe_failure(invalid)
        invalid_payload = json.loads(invalid.stdout)
        duplicate = next(
            issue
            for issue in invalid_payload["issues"]
            if issue["code"] == "duplicate_admission_key"
        )
        self.assertEqual(duplicate["row"], 5)

        config = discover_provinces(PROVINCES)["演示乙市"]
        parsed = parse_secondary_subjects("生物、地理")
        self.assertEqual(parsed, frozenset(("生物", "地理")))
        validate_subject_selection(config, "物理", tuple(sorted(parsed)))
        with self.assertRaises(SubjectSelectionError):
            validate_subject_selection(config, "物理", ("物理", "地理"))

    def test_evidence_validation_uses_native_success_and_policy_failure_codes(self):
        """Catches a validator that accepts repost conflicts or stops emitting JSON."""
        accepted = self._script(
            "validate_evidence.py", EVIDENCE / "three-source-consensus"
        )
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        accepted_payload = json.loads(accepted.stdout)
        self.assertTrue(accepted_payload["valid"])
        self.assertEqual(accepted_payload["independent_source_count"], 3)

        rejected = self._script("validate_evidence.py", EVIDENCE / "repost-conflict")
        self._assert_safe_failure(rejected)
        rejected_payload = json.loads(rejected.stdout)
        self.assertFalse(rejected_payload["valid"])
        self.assertEqual(rejected_payload["independent_source_count"], 1)

    def test_markdown_replay_is_anonymous_deterministic_and_evidence_aware(self):
        """Catches report-only derivation, unstable bytes, and provenance omissions."""
        command = (
            "generate_report.py",
            "--dataset",
            PROVINCES / "demo-312",
            "--profile",
            PROFILE,
            "--evidence",
            EVIDENCE / "three-source-consensus",
        )
        first = self._script(*command)
        second = self._script(*command)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout.encode("utf-8"), second.stdout.encode("utf-8"))
        self.assertEqual(
            hashlib.sha256(first.stdout.encode("utf-8")).digest(),
            hashlib.sha256(second.stdout.encode("utf-8")).digest(),
        )
        for literal in (
            "# 匿名升学规划报告（演示甲省）",
            "查询覆盖：",
            "数据覆盖：缺失",
            "证据状态：缺失",
            "检索日期：2026-08-23",
            "清单哈希：sha256:",
            "屏蔽值、冲突、部分覆盖与缺失数据",
            "AI 生成，仅供参考",
        ):
            self.assertIn(literal, first.stdout)
        self.assertGreaterEqual(first.stdout.count("AI 生成，仅供参考"), 3)
        for forbidden in ("张三", "13800138000", "http://", "https://", str(ROOT)):
            self.assertNotIn(forbidden, first.stdout)

    def test_report_invalid_data_evidence_and_profile_fail_closed(self):
        """Catches invalid inputs that leak paths/PII or fall through with a traceback."""
        base = (
            "--dataset",
            PROVINCES / "demo-312",
            "--profile",
            PROFILE,
            "--evidence",
            EVIDENCE / "three-source-consensus",
        )
        cases = (
            (
                "invalid data",
                (
                    "--dataset",
                    PROVINCES / "duplicate-program",
                    "--profile",
                    PROFILE,
                    "--evidence",
                    EVIDENCE / "three-source-consensus",
                ),
            ),
            (
                "invalid evidence",
                (
                    "--dataset",
                    PROVINCES / "demo-312",
                    "--profile",
                    PROFILE,
                    "--evidence",
                    EVIDENCE / "repost-conflict",
                ),
            ),
        )
        for name, arguments in cases:
            with self.subTest(name=name):
                self._assert_safe_failure(self._script("generate_report.py", *arguments))

        profile_payload = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile_payload["rank"] = 0
        invalid_profile = self.sandbox / "invalid-profile.json"
        invalid_profile.write_text(
            json.dumps(profile_payload, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )
        self._assert_safe_failure(
            self._script(
                "generate_report.py",
                *base[:2],
                "--profile",
                invalid_profile,
                *base[4:],
            )
        )

    def test_docx_uses_the_same_snapshot_and_is_byte_deterministic(self):
        """Catches a second DOCX model, non-anonymous output, or unstable package bytes."""
        self._assert_document_runtime()
        outputs = (self.sandbox / "first", self.sandbox / "second")
        for output in outputs:
            output.mkdir(exist_ok=True)
            result = self._script(
                "docx_export.py",
                "--dataset",
                PROVINCES / "demo-312",
                "--profile",
                PROFILE,
                "--evidence",
                EVIDENCE / "three-source-consensus",
                "--secondary-subject",
                "化学",
                "--secondary-subject",
                "地理",
                "--output",
                output,
                python=self.documents_python,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["anonymous"])
            self.assertEqual(payload["filename"], "匿名升学规划报告-演示甲省-2026.docx")
            self.assertEqual(payload["secondary_subjects"], ["化学", "地理"])

        first = outputs[0] / "匿名升学规划报告-演示甲省-2026.docx"
        second = outputs[1] / "匿名升学规划报告-演示甲省-2026.docx"
        first_bytes = first.read_bytes()
        second_bytes = second.read_bytes()
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            hashlib.sha256(first_bytes).digest(), hashlib.sha256(second_bytes).digest()
        )
        text = _docx_text(first)
        for literal in (
            "匿名升学规划报告（演示甲省）",
            "化学、地理",
            "查询覆盖",
            "数据覆盖",
            "检索日期",
            "2026-08-23",
            "证据状态",
            "AI 生成，仅供参考",
        ):
            self.assertIn(literal, text)
        for forbidden in ("张三", "13800138000", "http://", "https://", str(ROOT)):
            self.assertNotIn(forbidden, text)

    def test_missing_document_capability_is_exit_three_without_weakening_installed_gate(self):
        """Catches late ImportError/exit-2 handling while real DOCX stays mandatory above."""
        self._assert_document_runtime()
        result = self._script(
            "docx_export.py",
            python=self.documents_python,
            block_docx=True,
        )
        self._assert_safe_failure(result, expected=3)
        self.assertIn("缺少能力", result.stderr)
        self.assertIn("python-docx", result.stderr)


if __name__ == "__main__":
    unittest.main()
