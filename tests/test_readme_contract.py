import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
INTRODUCTION = (
    "面向全国新高考省份的开源 AI 升学规划 Skill：实时检索并交叉验证公开数据，"
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


class ReadmeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = README.read_text(encoding="utf-8")

    def test_first_paragraph_is_the_approved_public_description(self):
        paragraphs = [part.strip() for part in self.text.split("\n\n") if part.strip()]
        self.assertGreaterEqual(len(paragraphs), 2)
        self.assertEqual(paragraphs[1], INTRODUCTION)

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
        documented = set(re.findall(r"python scripts/([a-z_]+\.py)", self.text))
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
        for name in documented:
            with self.subTest(name=name):
                self.assertTrue((ROOT / "scripts" / name).is_file())

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
