import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "LICENSE",
    "CONTRIBUTING.md",
    "DATA_SOURCES.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "ROADMAP.md",
)


def read_documents():
    return {
        name: (ROOT / name).read_text(encoding="utf-8")
        if (ROOT / name).is_file()
        else ""
        for name in REQUIRED_FILES
    }


def markdown_links(text):
    return tuple(re.findall(r"\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)", text))


def contract_violations(documents):
    """Return semantic community-contract violations for mutation testing."""
    violations = []
    license_text = documents.get("LICENSE", "")
    contributing = documents.get("CONTRIBUTING.md", "")
    data_sources = documents.get("DATA_SOURCES.md", "")
    security = documents.get("SECURITY.md", "")
    conduct = documents.get("CODE_OF_CONDUCT.md", "")
    changelog = documents.get("CHANGELOG.md", "")
    roadmap = documents.get("ROADMAP.md", "")

    required_phrases = {
        "LICENSE": (
            "MIT License",
            "Copyright (c) 2026 sarry12227",
            "源代码",
            "虚构测试数据",
            "第三方数据",
            "再分发权",
        ),
        "CONTRIBUTING.md": (
            "虚构测试数据",
            "source ID",
            "数据权利声明",
            "TDD",
            "先写失败测试",
            "release_check.py",
            "python -m unittest discover -s tests -v",
        ),
        "DATA_SOURCES.md": (
            "A 级",
            "B 级",
            "C 级",
            "URL",
            "结构化事实",
            "内容哈希",
            "MIT 不自动授予第三方数据的再分发权",
            "快照再分发审查",
            "更正请求",
            "删除请求",
            "retrieved_at",
            "适用年份",
        ),
        "SECURITY.md": (
            "0.1.x",
            "支持",
            "GitHub private vulnerability reporting",
            "公开 Issue",
            "真实学生数据",
        ),
        "CODE_OF_CONDUCT.md": (
            "Contributor Covenant",
            "适用范围",
            "执行责任",
            "纠正",
            "警告",
            "临时限制",
            "永久限制",
        ),
        "CHANGELOG.md": (
            "v0.1.0",
            "公开预览",
            "证据",
            "3+1+2",
            "3+3",
            "Markdown",
            "DOCX",
            "虚构",
        ),
        "ROADMAP.md": (
            "结果导向",
            "验收信号",
            "证据覆盖",
            "跨平台",
            "贡献者",
        ),
    }
    for name, phrases in required_phrases.items():
        text = documents.get(name, "")
        for phrase in phrases:
            if phrase not in text:
                violations.append(f"{name}: missing {phrase}")

    if not re.search(r"\|\s*0\.1\.x\s*\|[^\n|]*支持", security):
        violations.append("SECURITY.md: 0.1.x support row missing")
    if "不适用上述 MIT" in license_text or "CC BY" in license_text:
        violations.append("LICENSE: unsupported real-data redistribution grant")
    if not re.search(r"只有 C 级[^。\n]*至少[^。\n]*3 个独立发布者", data_sources):
        violations.append("DATA_SOURCES.md: C-tier threshold missing")
    if not re.search(r"快照[^。\n]*许可[^。\n]*再分发", data_sources):
        violations.append("DATA_SOURCES.md: snapshot permission gate missing")
    if not re.search(r"公开 Issue[^。\n]*(?:不得|禁止)[^。\n]*(?:漏洞|学生数据)", security):
        violations.append("SECURITY.md: public-report prohibition missing")
    if not re.search(r"不保证录取|不承诺录取", changelog):
        violations.append("CHANGELOG.md: preview limitation missing")

    combined = "\n".join(documents.values())
    unsafe_patterns = {
        "email address": r"(?i)(?<![a-z0-9_.+-])[a-z0-9_.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![a-z0-9_.-])",
        "Chinese mobile number": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "Chinese identity number": r"(?<!\d)\d{17}[0-9Xx](?!\d)",
        "Windows absolute path": r"(?i)(?<![\w])(?:[a-z]:\\|[a-z]:/)",
        "private parent project": r"shengxue-ai-planner|shengxue-system",
        "active admission guarantee": r"(?<!不)(?<!不作)(?:保证录取|承诺录取|保录取|保过)",
        "sales copy": r"限时优惠|立即购买|正价|引流|咨询顾问|扫码购买|¥\s*\d|￥\s*\d",
    }
    for label, pattern in unsafe_patterns.items():
        if re.search(pattern, combined):
            violations.append(f"community files contain {label}")

    if re.search(r"20\d{2}[年/-]\d{1,2}|第\s*[一二三四0-9]+\s*季度", roadmap):
        violations.append("ROADMAP.md: calendar commitment")
    if re.search(r"(?:湖北|湖南|广东|江苏|山东|浙江|上海|北京|天津|河北|辽宁|福建|重庆)", roadmap):
        violations.append("ROADMAP.md: province commitment")
    return violations


class CommunityFilesTest(unittest.TestCase):
    def setUp(self):
        self.documents = read_documents()

    def test_required_files_exist_as_canonical_utf8(self):
        for name in REQUIRED_FILES:
            with self.subTest(name=name):
                path = ROOT / name
                self.assertTrue(path.is_file(), name)
                source = path.read_bytes()
                text = source.decode("utf-8", errors="strict")
                self.assertEqual(text.encode("utf-8"), source)

    def test_license_limits_mit_to_code_and_synthetic_fixtures(self):
        text = self.documents["LICENSE"]
        self.assertIn("Copyright (c) 2026 sarry12227", text)
        self.assertIn("源代码", text)
        self.assertIn("虚构测试数据", text)
        self.assertIn("第三方数据", text)
        self.assertIn("再分发权", text)
        self.assertNotIn("CC BY", text)
        self.assertNotIn("不适用上述 MIT", text)

    def test_contributing_requires_evidence_rights_tdd_and_release_checks(self):
        text = self.documents["CONTRIBUTING.md"]
        for phrase in (
            "虚构测试数据",
            "source ID",
            "数据权利声明",
            "先写失败测试",
            "release_check.py",
            "python -m unittest discover -s tests -v",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_data_policy_defines_tiers_storage_redistribution_and_lifecycle(self):
        text = self.documents["DATA_SOURCES.md"]
        for phrase in (
            "A 级",
            "B 级",
            "C 级",
            "3 个独立发布者",
            "URL",
            "结构化事实",
            "内容哈希",
            "MIT 不自动授予第三方数据的再分发权",
            "快照再分发审查",
            "更正请求",
            "删除请求",
            "retrieved_at",
            "适用年份",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_security_and_conduct_offer_private_truthful_enforcement_paths(self):
        security = self.documents["SECURITY.md"]
        conduct = self.documents["CODE_OF_CONDUCT.md"]
        self.assertRegex(security, r"\|\s*0\.1\.x\s*\|[^\n|]*支持")
        self.assertIn("GitHub private vulnerability reporting", security)
        self.assertRegex(security, r"公开 Issue[^。\n]*(?:不得|禁止)")
        self.assertIn("真实学生数据", security)
        for phrase in ("Contributor Covenant", "适用范围", "执行责任", "临时限制", "永久限制"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, conduct)

    def test_changelog_describes_the_implemented_preview_without_marketing(self):
        text = self.documents["CHANGELOG.md"]
        for phrase in ("v0.1.0", "公开预览", "证据", "3+1+2", "3+3", "Markdown", "DOCX", "虚构"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertRegex(text, r"不保证录取|不承诺录取")

    def test_roadmap_is_outcome_based_without_province_or_date_promises(self):
        roadmap = self.documents["ROADMAP.md"]
        self.assertIn("结果导向", roadmap)
        self.assertIn("验收信号", roadmap)
        self.assertIsNone(re.search(r"20\d{2}[年/-]\d{1,2}|第\s*[一二三四0-9]+\s*季度", roadmap))
        self.assertIsNone(re.search(r"湖北|湖南|广东|江苏|山东|浙江|上海|北京|天津|河北|辽宁|福建|重庆", roadmap))

    def test_local_markdown_links_resolve_and_no_contact_is_invented(self):
        for name, text in self.documents.items():
            for target in markdown_links(text):
                with self.subTest(name=name, target=target):
                    self.assertNotRegex(target, r"(?i)^mailto:")
                    if re.match(r"(?i)^https?://", target):
                        continue
                    relative = target.split("#", 1)[0]
                    self.assertTrue(relative, "fragment-only links are not used here")
                    resolved = (ROOT / relative).resolve()
                    resolved.relative_to(ROOT.resolve())
                    self.assertTrue(resolved.exists(), f"broken link in {name}: {target}")

    def test_community_contract_rejects_pii_marketing_and_local_paths(self):
        self.assertEqual(contract_violations(self.documents), [])

    def test_mutation_canaries_reject_rights_security_scope_and_safety_regressions(self):
        mutations = {
            "wrong-owner": ("LICENSE", "sarry12227", "unknown-owner"),
            "data-redistribution": (
                "DATA_SOURCES.md",
                "MIT 不自动授予第三方数据的再分发权",
                "MIT 自动授予第三方数据的再分发权",
            ),
            "unsupported-version": ("SECURITY.md", "0.1.x", "0.2.x"),
            "active-guarantee": ("ROADMAP.md", "结果导向", "结果导向：保证录取"),
            "invented-email": ("CODE_OF_CONDUCT.md", "执行责任", "执行责任 contact@example.com"),
            "private-path": ("CONTRIBUTING.md", "TDD", r"TDD C:\private\student.csv"),
            "calendar-promise": ("ROADMAP.md", "验收信号", "2027年6月验收信号"),
            "province-promise": ("ROADMAP.md", "证据覆盖", "湖北证据覆盖"),
        }
        self.assertEqual(contract_violations(self.documents), [])
        for name, (filename, old, new) in mutations.items():
            with self.subTest(name=name):
                mutated = dict(self.documents)
                mutated[filename] = mutated[filename].replace(old, new, 1)
                self.assertNotEqual(mutated[filename], self.documents[filename], f"mutation did not apply: {name}")
                self.assertTrue(contract_violations(mutated), f"mutation escaped: {name}")


if __name__ == "__main__":
    unittest.main()
