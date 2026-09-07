import json
import hashlib
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
RELEASE_PROCESS = ROOT / "docs" / "release-process.md"
PYPROJECT = ROOT / "pyproject.toml"
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
FIXED_README_PREFIX = """一句话让AI调用此skill：

```bash
请使用你当前环境的 Skill 安装能力，从 GitHub `https://github.com/sarry12227/pathway-atlas` 安装或更新 `pathway-atlas`（多元星途）到最新版；如果 GitHub 无法访问，请改用 Gitee 镜像 `https://gitee.com/sarry1/pathway-atlas`。若没有专用安装工具，请将完整仓库克隆或下载到当前 Agent 可识别的 Skills 目录，保留已有咨询记录，确认根目录存在 `SKILL.md` 且其中 `name` 为 `pathway-atlas`，再按当前环境支持的方式重新加载并调用它。成功加载后，沿用我已提供的信息，每轮只问一道题并展示当前选项，等待我回答；我确认个人情况后，再检索和核验公开资料。最后直接在对话中详细说明有依据的结论、理由、优先行动和待核验事项，文件作为补充。若无法安装或加载，请如实说明具体阻碍和最少下一步，不要声称已调用。
```
"""
INTRODUCTION = (
    "多元星途（PathwayAtlas）是给学生和家长用的开源 AI 升学规划 Skill。"
    "它让支持 Skill 的 AI 一步一步了解孩子的情况，查证公开招生信息，"
    "把选学校、选专业和多元升学路径整理成看得懂、能行动的规划。"
)
PUBLIC_SCRIPT_CLIS = {
    "compliance_scan.py",
    "docx_export.py",
    "generate_report.py",
    "live_smoke.py",
    "planning_session.py",
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

    def test_readme_starts_with_user_owned_install_prompt(self):
        raw = README.read_bytes()
        prefix = FIXED_README_PREFIX.encode("utf-8")
        self.assertTrue(raw.startswith(prefix))
        self.assertEqual(raw.count(prefix), 1)
        logo_start = raw.index(b"<p align=")
        self.assertEqual(raw[len(prefix) :], b"\n" + raw[logo_start:])
        self.assertEqual(
            hashlib.sha256(raw[: len(prefix)]).hexdigest(),
            hashlib.sha256(prefix).hexdigest(),
        )

    def test_docx_docs_describe_the_v3_host_flow_without_user_json_or_paths(self):
        legacy_command = (
            "python scripts/docx_export.py --dataset tests/fixtures/provinces/demo-312"
        )
        self.assertNotIn(legacy_command, self.text)
        self.assertIn("宿主内部", self.text)
        self.assertIn("canonical QueryPlan", self.text)
        release = RELEASE_PROCESS.read_text(encoding="utf-8")
        self.assertNotIn("scripts\\docx_export.py --dataset", release)
        self.assertIn("宿主内部", release)

    def test_readme_describes_the_unified_profile_sensitive_session(self):
        journey = section(self.text, "用户旅程")
        for marker in (
            "planning_session.py", "host_workflow.py start", "next", "ingest",
            "finish", "当前查询年", "Y → Y-1 → Y-2 → Y-3",
            "偏好参与判断", "当前最需要做的事", "公开预览",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, journey)
        self.assertIn("用户不接触内部 JSON、文件路径或命令", journey)

    def test_readme_contains_the_approved_public_description(self):
        paragraphs = [part.strip() for part in self.text.split("\n\n") if part.strip()]
        self.assertIn(INTRODUCTION, paragraphs)

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
                "docx_export.py",
                "preflight.py",
                "validate_data.py",
                "validate_evidence.py",
                "generate_report.py",
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
