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
PROVINCIAL_REGIONS = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
    "台湾",
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


def markdown_sections(text):
    """Parse second-level Markdown sections without treating prose as headings."""
    headings = tuple(re.finditer(r"^##\s+([^\n]+?)\s*$", text, flags=re.MULTILINE))
    sections = {}
    for index, heading in enumerate(headings):
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections[heading.group(1).strip()] = text[start:end].strip()
    return sections


def markdown_table_rows(text):
    """Return content rows from simple pipe tables, excluding separator rows."""
    rows = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return tuple(rows)


def prose_sentences(text):
    plain = re.sub(r"[`*_]", "", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return tuple(sentence.strip() for sentence in re.split(r"[。！？]", plain) if sentence.strip())


def data_governance_violations(data_sources):
    violations = []
    sections = markdown_sections(data_sources)
    tier_rows = [
        row
        for row in markdown_table_rows(sections.get("A/B/C 来源", ""))
        if row and row[0] in {"A 级", "B 级", "C 级"}
    ]
    b_rows = [row for row in tier_rows if row[0] == "B 级"]
    if len(b_rows) != 1 or len(b_rows[0]) < 3:
        violations.append("DATA_SOURCES.md: one B-tier policy row required")
    else:
        b_policy = " ".join(b_rows[0][2:])
        if not re.search(r"没有直接 A[^；]*至少两个[^；]*独立[^；]*可追溯[^；]*B[^；]*一致", b_policy):
            violations.append("DATA_SOURCES.md: independent two-B threshold missing")

    freshness = prose_sentences(sections.get("时效与新鲜度", ""))
    hash_rules = [sentence for sentence in freshness if "哈希变化" in sentence]
    if len(hash_rules) != 1 or not all(
        term in hash_rules[0] for term in ("触发", "重新提取", "验证", "不自动覆盖已发布事实")
    ):
        violations.append("DATA_SOURCES.md: hash changes must revalidate without auto-overwrite")

    takedown = prose_sentences(sections.get("删除请求", ""))
    takedown_rules = [sentence for sentence in takedown if "收到可信请求" in sentence]
    if len(takedown_rules) != 1 or not re.search(
        r"先停止后续分发[^。]*隔离受影响快照", takedown_rules[0]
    ):
        violations.append("DATA_SOURCES.md: takedown must stop distribution and isolate snapshot")
    return violations


def private_reporting_violations(security, conduct):
    violations = []
    security_sentences = prose_sentences(markdown_sections(security).get("私密报告", ""))
    security_rules = [sentence for sentence in security_sentences if "公开 Issue" in sentence]
    if len(security_rules) != 1 or not all(
        term in security_rules[0] for term in ("禁止", "披露", "漏洞", "真实学生数据")
    ):
        violations.append("SECURITY.md: public issues must not disclose vulnerabilities or student data")

    conduct_sentences = prose_sentences(markdown_sections(conduct).get("执行责任与报告", ""))
    conduct_rules = [sentence for sentence in conduct_sentences if "公开 Issue" in sentence]
    if len(conduct_rules) != 1 or not all(
        term in conduct_rules[0] for term in ("不得", "披露", "敏感事件")
    ):
        violations.append("CODE_OF_CONDUCT.md: public issues must not disclose conduct incidents")
    return violations


def roadmap_violations(roadmap):
    """Validate only roadmap outcome sections, not historical/release prose elsewhere."""
    violations = []
    sections = markdown_sections(roadmap)
    if not sections:
        return ["ROADMAP.md: outcome sections missing"]

    for heading, body in sections.items():
        normalized = re.sub(r"\s+", " ", body)
        if "目标" not in normalized or "验收信号" not in normalized:
            violations.append(f"ROADMAP.md: {heading} lacks goal or acceptance signal")

    outcome_scope = "\n".join(f"{heading}\n{body}" for heading, body in sections.items())
    named_regions = tuple(region for region in PROVINCIAL_REGIONS if region in outcome_scope)
    if named_regions:
        violations.append(f"ROADMAP.md: province commitment: {','.join(named_regions)}")

    date_token = r"20\d{2}(?:年(?:\d{1,2}月(?:\d{1,2}日)?|第[一二三四0-9]+季度)?|[-/]\d{1,2}[-/]\d{1,2})"
    delivery = r"(?:完成|上线|发布|交付|推出|覆盖|支持|实现|验收|上线日期|发布日期)"
    if re.search(rf"(?:{date_token}.{{0,24}}{delivery}|{delivery}.{{0,24}}{date_token})", outcome_scope):
        violations.append("ROADMAP.md: dated delivery commitment")
    return violations


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
    if not re.search(r"不保证录取|不承诺录取", changelog):
        violations.append("CHANGELOG.md: preview limitation missing")

    violations.extend(data_governance_violations(data_sources))
    violations.extend(private_reporting_violations(security, conduct))
    violations.extend(roadmap_violations(roadmap))

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
        self.assertEqual(roadmap_violations(roadmap), [])

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
            "calendar-promise": (
                "ROADMAP.md",
                "**验收信号：** 新增来源",
                "**验收信号：** 新增来源于2027年6月上线",
            ),
            "province-promise": ("ROADMAP.md", "证据覆盖", "湖北证据覆盖"),
        }
        self.assertEqual(contract_violations(self.documents), [])
        for name, (filename, old, new) in mutations.items():
            with self.subTest(name=name):
                mutated = dict(self.documents)
                mutated[filename] = mutated[filename].replace(old, new, 1)
                self.assertNotEqual(mutated[filename], self.documents[filename], f"mutation did not apply: {name}")
                self.assertTrue(contract_violations(mutated), f"mutation escaped: {name}")

    def test_governance_direction_mutations_fail_closed(self):
        mutations = {
            "single-b-source": (
                "DATA_SOURCES.md",
                "没有直接 A 时至少两个独立且可追溯的 B 一致",
                "没有直接 A 时一个可追溯的 B",
                "DATA_SOURCES.md: independent two-B threshold missing",
            ),
            "hash-auto-overwrite": (
                "DATA_SOURCES.md",
                "哈希变化触发重新提取与验证，不自动覆盖已发布事实",
                "哈希变化自动覆盖已发布事实",
                "DATA_SOURCES.md: hash changes must revalidate without auto-overwrite",
            ),
            "continue-distribution": (
                "DATA_SOURCES.md",
                "先停止后续分发并隔离受影响快照",
                "继续后续分发且不隔离受影响快照",
                "DATA_SOURCES.md: takedown must stop distribution and isolate snapshot",
            ),
            "public-security-disclosure": (
                "SECURITY.md",
                "公开 Issue 禁止披露漏洞细节或真实学生数据",
                "公开 Issue 应披露漏洞细节和真实学生数据",
                "SECURITY.md: public issues must not disclose vulnerabilities or student data",
            ),
            "public-conduct-disclosure": (
                "CODE_OF_CONDUCT.md",
                "不得在公开 Issue 披露敏感事件内容",
                "应在公开 Issue 披露敏感事件内容",
                "CODE_OF_CONDUCT.md: public issues must not disclose conduct incidents",
            ),
        }
        self.assertEqual(contract_violations(self.documents), [])
        for name, (filename, old, new, expected) in mutations.items():
            with self.subTest(name=name):
                mutated = dict(self.documents)
                mutated[filename] = mutated[filename].replace(old, new, 1)
                self.assertNotEqual(mutated[filename], self.documents[filename], f"mutation did not apply: {name}")
                self.assertIn(expected, contract_violations(mutated), f"mutation escaped: {name}")

    def test_roadmap_mutations_cover_provincial_names_and_future_commitments(self):
        for region in PROVINCIAL_REGIONS:
            with self.subTest(region=region):
                mutated = dict(self.documents)
                mutated["ROADMAP.md"] = mutated["ROADMAP.md"].replace(
                    "更多公开材料", f"{region}公开材料", 1
                )
                self.assertNotEqual(mutated["ROADMAP.md"], self.documents["ROADMAP.md"])
                self.assertTrue(
                    any(
                        violation.startswith("ROADMAP.md: province commitment:")
                        for violation in contract_violations(mutated)
                    ),
                    f"province mutation escaped: {region}",
                )

        mutations = {
            "year-completion": (
                "更多公开材料",
                "更多公开材料，并于2027年完成",
            ),
            "month-launch": (
                "更多公开材料",
                "更多公开材料，并于2027年8月上线",
            ),
            "quarter-release": (
                "更多公开材料",
                "更多公开材料，并于2027年第三季度发布",
            ),
            "launch-date": (
                "更多公开材料",
                "更多公开材料，上线日期为2027-08-25",
            ),
        }
        self.assertEqual(contract_violations(self.documents), [])
        for name, (old, new) in mutations.items():
            with self.subTest(name=name):
                mutated = dict(self.documents)
                mutated["ROADMAP.md"] = mutated["ROADMAP.md"].replace(old, new, 1)
                self.assertNotEqual(mutated["ROADMAP.md"], self.documents["ROADMAP.md"], f"mutation did not apply: {name}")
                self.assertIn(
                    "ROADMAP.md: dated delivery commitment",
                    contract_violations(mutated),
                    f"mutation escaped: {name}",
                )

    def test_historical_dates_and_explicit_noncommitments_are_safe_prose(self):
        safe = dict(self.documents)
        safe["CHANGELOG.md"] += "\n## v0.0.9 — 2025-12-31\n\n历史版本。\n"
        safe["ROADMAP.md"] = safe["ROADMAP.md"].replace(
            "这是结果导向的维护方向",
            "本路线图不承诺2027年完成，也不承诺安徽覆盖。这是结果导向的维护方向",
            1,
        )
        self.assertEqual(contract_violations(safe), [])


if __name__ == "__main__":
    unittest.main()
