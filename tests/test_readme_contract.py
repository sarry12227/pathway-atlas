import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 test extra
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
INTRODUCTION = (
    "多元星途（PathwayAtlas）是面向全国新高考省份的开源 AI 升学规划 Skill："
    "实时检索并交叉验证公开数据，"
    "通过本地确定性管线生成可追溯的普通批冲稳保与多元升学方案。"
)
PUBLIC_SCRIPT_CLIS = {
    "compliance_scan.py",
    "docx_export.py",
    "generate_report.py",
    "live_smoke.py",
    "preflight.py",
    "query_plan.py",
    "validate_data.py",
    "validate_evidence.py",
}


def section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing README section: {heading}")
    return match.group("body")


def documented_script_names(text: str) -> set[str]:
    return set(re.findall(r"(?m)^python scripts/([a-z_]+\.py)(?:\s|$)", text))


def documented_install_extras(text: str) -> tuple[str, ...]:
    matches = re.findall(
        r"(?m)^python -m pip install -e\s+[\"']?\.\[([^\]]+)\][\"']?\s*$",
        text,
    )
    return tuple(
        extra.strip()
        for match in matches
        for extra in match.split(",")
        if extra.strip()
    )


def capability_contract(text: str) -> dict[str, object]:
    capability = section(text, "能力档")
    tiers = tuple(
        re.findall(r"\*\*[^*]+（(full|standard|offline)）\*\*", capability)
    )
    preflight = re.search(
        r"`preflight\.py`[^。\n]*能力缺失[^。\n]*JSON[^。\n]*退出码 `([0-9]+)`",
        capability,
    )
    docx = re.search(
        r"DOCX[^。\n]*能力缺失[^。\n]*Markdown[^。\n]*不创建 DOCX"
        r"[^。\n]*`docx_export\.py`[^。\n]*退出码 `([0-9]+)`",
        capability,
    )
    return {
        "tiers": tiers,
        "preflight_exit": None if preflight is None else int(preflight.group(1)),
        "docx_exit": None if docx is None else int(docx.group(1)),
        "markdown_preserved": docx is not None,
    }


class ReadmeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = README.read_text(encoding="utf-8")

    def test_readme_contains_the_approved_public_description(self):
        paragraphs = [part.strip() for part in self.text.split("\n\n") if part.strip()]
        self.assertIn(INTRODUCTION, paragraphs)

    def test_first_line_install_prompt_invokes_the_full_intake_and_decision_flow(self):
        first_line = self.text.splitlines()[0]
        for phrase in (
            "复制给 AI",
            "github.com/sarry12227/pathway-atlas",
            "gitee.com/sarry1/pathway-atlas",
            "不超过 20 题",
            "自动回填",
            "确认匿名画像",
            "乐观、中性、保守位次",
            "普通批冲稳保",
            "主攻、重点准备、备选、观察或不建议",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, first_line)

    def test_readme_explains_realtime_and_deterministic_halves(self):
        for phrase in (
            "Agent 实时检索",
            "交叉验证",
            "本地确定性",
            "证据包",
            "能力预检",
            "计算阶段不访问网络",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_readme_delegates_source_rules_without_copying_volatile_limits(self):
        for phrase in (
            "A 级原始来源",
            "B 级权威整理",
            "C 级独立整理",
            "3 个独立发布者",
            "references/source-policy.md",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)
        for stale_detail in ("前 10 个", "冲≤3", "稳≤4", "保≤5", "减 4000"):
            with self.subTest(stale_detail=stale_detail):
                self.assertNotIn(stale_detail, self.text)

    def test_readme_covers_capability_and_installation_contracts(self):
        for phrase in (
            "完整档",
            "标准档",
            "离线档",
            "Python 3.10",
            ".[all,test]",
            "Generic Agent",
            "Codex",
            "Claude Code",
            "Kimi Code",
            "references/hosts/generic.md",
            "references/hosts/codex.md",
            "references/hosts/claude-code.md",
            "references/hosts/kimi.md",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_install_command_extras_are_declared_by_project_metadata(self):
        with PYPROJECT.open("rb") as handle:
            metadata = tomllib.load(handle)
        declared = set(metadata["project"]["optional-dependencies"])
        documented = documented_install_extras(self.text)
        self.assertEqual(documented, ("all", "test"))
        self.assertLessEqual(set(documented), declared)

    def test_readme_covers_synthetic_evidence_and_report_journey(self):
        for phrase in (
            "虚构测试数据",
            "tests/fixtures/provinces/demo-312",
            "tests/fixtures/evidence/three-source-consensus",
            "字段级来源",
            "Markdown",
            "DOCX",
            "QR",
            "OCR",
            "masked",
            "partial",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_documented_python_scripts_are_tracked_public_clis(self):
        documented = documented_script_names(self.text)
        self.assertGreaterEqual(
            documented,
            {
                "preflight.py",
                "validate_data.py",
                "validate_evidence.py",
                "generate_report.py",
                "docx_export.py",
            },
        )
        self.assertLessEqual(documented, PUBLIC_SCRIPT_CLIS)
        self.assert_documented_scripts_tracked(self.text)

    def assert_documented_scripts_tracked(self, text: str) -> None:
        documented = documented_script_names(text)
        untracked = []
        for name in documented:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", f"scripts/{name}"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if tracked.returncode != 0:
                untracked.append(f"scripts/{name}")
        self.assertEqual(untracked, [], f"README commands are not tracked: {untracked}")

    def assert_capability_semantics(self, text: str) -> None:
        contract = capability_contract(text)
        self.assertEqual(contract["tiers"], ("full", "standard", "offline"))
        self.assertEqual(contract["preflight_exit"], 0)
        self.assertEqual(contract["docx_exit"], 3)
        self.assertTrue(contract["markdown_preserved"])

    def test_preflight_degradation_docs_match_zero_exit_json(self):
        self.assert_capability_semantics(self.text)

        preflight = subprocess.run(
            [sys.executable, str(SCRIPTS / "preflight.py")],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        payload = json.loads(preflight.stdout)
        self.assertEqual(payload["tier"], "offline")
        self.assertTrue(payload["missing_capabilities"])
        self.assertTrue(payload["degradations"])

    def test_docx_capability_docs_match_exit_three_and_preserve_markdown(self):
        contract = capability_contract(self.text)
        self.assertEqual(contract["docx_exit"], 3)
        self.assertTrue(contract["markdown_preserved"])

        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            markdown = sandbox / "anonymous-admission-report.md"
            markdown_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "generate_report.py"),
                    "--dataset",
                    str(FIXTURES / "provinces" / "demo-312"),
                    "--profile",
                    str(FIXTURES / "profiles" / "demo.json"),
                    "--evidence",
                    str(FIXTURES / "evidence" / "three-source-consensus"),
                    "--output",
                    str(markdown),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                markdown_result.returncode,
                0,
                markdown_result.stderr.decode("utf-8", errors="strict"),
            )
            before = markdown.read_bytes()

            startup = sandbox / "startup"
            startup.mkdir()
            (startup / "sitecustomize.py").write_text(
                """
import importlib.abc
import sys

class BlockDocx(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "docx" or fullname.startswith("docx."):
            raise ModuleNotFoundError("DOCX capability blocked by README contract")
        return None

sys.meta_path.insert(0, BlockDocx())
""".lstrip(),
                encoding="utf-8",
                newline="\n",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(startup)
            docx = sandbox / "anonymous-admission-report.docx"
            docx_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "docx_export.py"),
                    "--dataset",
                    str(FIXTURES / "provinces" / "demo-312"),
                    "--profile",
                    str(FIXTURES / "profiles" / "demo.json"),
                    "--evidence",
                    str(FIXTURES / "evidence" / "three-source-consensus"),
                    "--output",
                    str(docx),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                docx_result.returncode,
                3,
                docx_result.stderr.decode("utf-8", errors="strict"),
            )
            self.assertIn("缺少能力", docx_result.stderr.decode("utf-8", errors="strict"))
            self.assertEqual(markdown.read_bytes(), before)
            self.assertFalse(docx.exists())

    def test_capability_semantics_have_mutation_canaries(self):
        mutations = {
            "preflight-exit-three": self.text.replace(
                "降级 JSON 并返回退出码 `0`", "降级 JSON 并返回退出码 `3`", 1
            ),
            "docx-exit-zero": self.text.replace(
                "`docx_export.py` 返回退出码 `3`",
                "`docx_export.py` 返回退出码 `0`",
                1,
            ),
            "discard-markdown": self.text.replace(
                "保留已经生成的 Markdown、不创建 DOCX", "不创建 DOCX", 1
            ),
        }
        for name, mutated in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(mutated, self.text, f"mutation did not apply: {name}")
                with self.assertRaises(AssertionError):
                    self.assert_capability_semantics(mutated)

    def test_untracked_script_mutation_is_rejected(self):
        mutated = self.text.replace(
            "python scripts/preflight.py", "python scripts/untracked_probe.py", 1
        )
        self.assertNotEqual(mutated, self.text)
        documented = documented_script_names(mutated)
        self.assertIn("untracked_probe.py", documented)
        with self.assertRaises(AssertionError):
            self.assert_documented_scripts_tracked(mutated)

    def test_readme_discloses_privacy_data_rights_preview_and_limits(self):
        for phrase in (
            "隐私",
            "不保证录取",
            "AI 生成仅供参考",
            "v0.1.0",
            "公开预览",
            "MIT",
            "DATA_SOURCES.md",
            "第三方数据",
            "再分发权",
            "SECURITY.md",
            "CONTRIBUTING.md",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_readme_does_not_claim_zero_network_or_production_readiness(self):
        for forbidden in ("零网络依赖", "已生产就绪", "可保证录取"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
