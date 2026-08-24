import re
import unittest
from pathlib import Path

from scripts.contracts import EvidenceStatus, FactClaim, SourceCandidate, SourceTier
from scripts.source_policy import evaluate_claims


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
STAGES = (
    "信息采集",
    "能力预检",
    "查询计划",
    "证据归一化",
    "确定性计算",
    "报告输出",
)
PUBLIC_COMMANDS = (
    "scripts/preflight.py",
    "scripts/query_plan.py",
    "scripts/validate_data.py",
    "scripts/validate_evidence.py",
    "scripts/generate_report.py",
    "scripts/docx_export.py",
)
REFERENCE_LINKS = (
    "references/source-policy.md",
    "references/retrieval-playbook.md",
    "references/hosts/generic.md",
    "references/hosts/codex.md",
    "references/hosts/claude-code.md",
    "references/hosts/kimi.md",
)
LEGACY_MARKERS = (
    "recommend.py",
    "estimate_rank.py",
    "recommend_paths.py",
    "verify_province.py",
    "fetch_via_qr.py",
    "web-search-playbook.md",
    "gaokao-provinces.md",
    "data/hubei",
    "--name",
)


def read_utf8(path):
    source = path.read_bytes()
    text = source.decode("utf-8", errors="strict")
    if text.encode("utf-8") != source:
        raise AssertionError("SKILL.md must be canonical UTF-8")
    return source, text


def parse_frontmatter(text):
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, flags=re.DOTALL)
    if match is None:
        raise AssertionError("SKILL.md must have one YAML frontmatter block")
    fields = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key or not value.strip():
            raise AssertionError("frontmatter must use simple key/value fields")
        if key in fields:
            raise AssertionError(f"duplicate frontmatter field: {key}")
        fields[key] = value.strip().strip('"\'')
    return fields, match.group(2)


def stage_sections(body):
    headings = re.findall(r"^## ([^\n]+)$", body, flags=re.MULTILINE)
    if tuple(headings) != STAGES:
        raise AssertionError("body must contain the exact six ordered stage headings")
    sections = {}
    for index, heading in enumerate(STAGES):
        start_marker = f"## {heading}\n"
        start = body.index(start_marker) + len(start_marker)
        if index + 1 == len(STAGES):
            sections[heading] = body[start:]
        else:
            end = body.index(f"## {STAGES[index + 1]}\n", start)
            sections[heading] = body[start:end]
    return sections


def markdown_links(text):
    return tuple(re.findall(r"\[[^\]]+\]\(([^)]+)\)", text))


class SkillContractTest(unittest.TestCase):
    def setUp(self):
        self.skill_bytes, self.skill = read_utf8(SKILL)
        self.frontmatter, self.body = parse_frontmatter(self.skill)
        self.sections = None

    def test_frontmatter_is_trigger_only(self):
        self.assertEqual(set(self.frontmatter), {"name", "description"})
        self.assertEqual(self.frontmatter["name"], "shengxue-skill")
        description = self.frontmatter["description"]
        self.assertRegex(description, r"^Use when \S")
        self.assertLessEqual(len(description), 500)
        self.assertIsNone(
            re.search(
                r"preflight|query.plan|validate|full|standard|offline|"
                r"3\+3|3\+1\+2|湖北|province availability",
                description,
                flags=re.IGNORECASE,
            ),
            "description must describe user triggers, not workflow or availability",
        )

    def test_body_has_exact_six_stage_shape(self):
        sections = stage_sections(self.body)
        self.assertEqual(tuple(sections), STAGES)
        self.assertLessEqual(len(self.body.splitlines()), 120)

    def test_intake_is_province_first_decision_relevant_and_anonymous(self):
        intake = stage_sections(self.body)["信息采集"]
        rows = re.findall(r"^\|\s*([0-9]+)\s*\|\s*([^|]+?)\s*\|", intake, re.MULTILINE)
        self.assertEqual(
            tuple(field.strip() for _, field in rows),
            (
                "省份",
                "选科",
                "学校全称",
                "分数",
                "年级排名",
                "意向院校",
                "意向地区/城市",
                "意向专业",
                "港澳意愿",
                "奖项/活动",
            ),
        )
        self.assertEqual(tuple(number for number, _ in rows), tuple(map(str, range(1, 11))))
        self.assertRegex(intake, r"未知|选填")
        self.assertRegex(
            intake,
            r"拒绝收集[^。\n]*(?:姓名|学生姓名)[^。\n]*电话[^。\n]*地址[^。\n]*班级"
            r"[^。\n]*(?:通信 ID|通信ID)[^。\n]*(?:凭证|secret)[^。\n]*本地路径",
        )
        self.assertIn("ProvinceConfig", intake)
        self.assertIn("canonical subject", intake.lower())
        self.assertNotRegex(intake, r"只支持|默认省份|湖北")

    def test_preflight_uses_host_mapping_and_runtime_tiers(self):
        preflight = stage_sections(self.body)["能力预检"]
        for tier in ("full", "standard", "offline"):
            self.assertEqual(preflight.count(f"`{tier}`"), 1)
        self.assertRegex(preflight, r"search.*browse.*vision")
        self.assertRegex(preflight, r"local_exec.*file_output.*workflow gates")
        self.assertRegex(preflight, r"能力损失[^。\n]*coverage")
        self.assertRegex(preflight, r"退出码 `2`[^。\n]*(?:无效|invalid)")
        self.assertRegex(preflight, r"退出码 `3`[^。\n]*(?:可选|optional)")

    def test_query_plan_and_offline_pressure_scenario(self):
        query = stage_sections(self.body)["查询计划"]
        for field in (
            "ProvinceConfig.mode",
            "subject_group",
            "required_extraction_fields",
            "availability",
            "freshness",
            "max_candidates",
        ):
            self.assertIn(field, query)
        self.assertRegex(query, r"不得[^。\n]*(?:固定|另设)[^。\n]*(?:Top-N|候选数)")
        self.assertRegex(
            query,
            r"offline[^。\n]*(?:authenticated|已认证)[^。\n]*(?:用户提供|user-supplied)"
            r"[^。\n]*(?:不声称|禁止声称)[^。\n]*(?:当前|实时|current|live)",
        )

    def test_evidence_validation_blocks_calculation_pressure_scenario(self):
        evidence = stage_sections(self.body)["证据归一化"]
        calculation = stage_sections(self.body)["确定性计算"]
        self.assertRegex(evidence, r"HTML.*XLSX.*PDF.*OCR.*QR")
        self.assertRegex(evidence, r"secure downloader")
        self.assertRegex(evidence, r"year.*method.*locator.*source")
        self.assertIn("source-policy.md", evidence)
        self.assertRegex(evidence, r"冲突[^。\n]*(?:不得|禁止)取平均")
        self.assertRegex(
            evidence,
            r"authenticated snapshot[^。\n]*(?:之前|前)[^。\n]*(?:不得|禁止)[^。\n]*(?:数字|计算)",
        )
        self.assertRegex(calculation, r"validated snapshots")
        self.assertRegex(calculation, r"validated dataset/config")
        self.assertRegex(calculation, r"不联网|no network")
        self.assertRegex(calculation, r"3\+3.*3\+1\+2|3\+1\+2.*3\+3")
        self.assertRegex(calculation, r"evidence status.*coverage")
        self.assertIsNone(re.search(r"(?:rank|位次).{0,20}[+-]\s*[0-9]", calculation, re.I))

    def test_admission_rows_use_the_public_typed_bridge_without_rule_duplication(self):
        evidence = stage_sections(self.body)["证据归一化"]
        for marker in (
            "scripts.adapters.admission_bridge",
            "QueryTask",
            "ValidatedAdmissionRow",
            "admission_row_hash",
            "coverage_status",
        ):
            self.assertIn(marker, evidence)
        self.assertIsNone(
            re.search(
                r"(?:coverage_status|admission_row_hash)[^。\n]{0,30}"
                r"(?:阈值|至少|required\s+sources?|minimum)",
                evidence,
                re.IGNORECASE,
            )
        )

    def test_evidence_admission_delegates_to_policy_without_official_only_downgrade(self):
        evidence = stage_sections(self.body)["证据归一化"]
        self.assertRegex(
            evidence,
            r"按信源规范仍未达到采纳门槛时[^。\n]*"
            r"`partial`[^。\n]*`conflict`[^。\n]*`missing`",
        )
        self.assertNotIn("官方证据不足", evidence)

        def candidate(source_id, tier):
            return SourceCandidate(
                source_id=source_id,
                url=f"https://{source_id}.example.test/article",
                publisher=f"Publisher {source_id}",
                tier=tier,
                published_at="2026-08-01",
                retrieved_at="2026-08-24T00:00:00Z",
                content_hash=f"sha256:{source_id}",
                citation_root=f"https://{source_id}.example.test/root",
                summary="Synthetic source",
            )

        def status_for(tier, count):
            sources = tuple(candidate(f"s{index}", tier) for index in range(count))
            claims = tuple(
                FactClaim("synthetic_field", 588, "分", source.source_id, "table")
                for source in sources
            )
            return evaluate_claims("synthetic_field", claims, sources).status

        self.assertEqual(status_for(SourceTier.C, 3), EvidenceStatus.REFERENCE)
        self.assertEqual(status_for(SourceTier.C, 2), EvidenceStatus.MISSING)
        self.assertEqual(status_for(SourceTier.B, 2), EvidenceStatus.CORROBORATED)

    def test_report_handles_optional_docx_and_discloses_uncertainty(self):
        report = stage_sections(self.body)["报告输出"]
        self.assertRegex(report, r"Markdown.*DOCX")
        self.assertRegex(report, r"DOCX[^。\n]*(?:缺失|不可用)[^。\n]*退出码 `3`")
        states = re.search(
            r"`reference`、`inferred`、`partial`、`conflict`、`missing`、`masked`",
            report,
        )
        self.assertIsNotNone(states, "report must disclose the exact six non-exact states")
        self.assertRegex(report, r"coverage.*method.*bounds")
        self.assertRegex(report, r"匿名[^。\n]*确定性[^。\n]*path-neutral[^。\n]*(?:exclusive|原子)")
        self.assertRegex(report, r"不承诺[^。\n]*(?:录取|投资)")
        self.assertRegex(report, r"明确授权[^。\n]*(?:发布|上传|push)")

    def test_links_and_public_commands_are_current(self):
        links = markdown_links(self.body)
        self.assertEqual(set(links), set(REFERENCE_LINKS))
        self.assertEqual(len(links), len(set(links)))
        for target in links:
            self.assertTrue((ROOT / target).is_file(), target)
        for command in PUBLIC_COMMANDS:
            self.assertEqual(self.body.count(command), 1, command)
            self.assertTrue((ROOT / command).is_file(), command)
        for marker in LEGACY_MARKERS:
            self.assertNotIn(marker, self.body)

    def assert_contract(self, text):
        frontmatter, body = parse_frontmatter(text)
        self.assertEqual(frontmatter.get("name"), "shengxue-skill")
        description = frontmatter.get("description", "")
        self.assertRegex(description, r"^Use when \S")
        self.assertIsNone(
            re.search(
                r"preflight|query.plan|validate|workflow|stages?|CLI|output|"
                r"DOCX|Markdown|writes?|generates?|full|standard|offline|"
                r"3\+3|3\+1\+2|湖北|province availability",
                description,
                re.IGNORECASE,
            )
        )
        sections = stage_sections(body)
        query = sections["查询计划"]
        evidence = sections["证据归一化"]
        calculation = sections["确定性计算"]
        intake = sections["信息采集"]
        self.assertRegex(query, r"offline[^。\n]*(?:不声称|禁止声称)[^。\n]*(?:当前|实时|current|live)")
        self.assertIsNone(
            re.search(
                r"(?:^|[；。\n])[^；。\n]{0,12}(?:静默|后台|必要时|回退)"
                r"[^；。\n]{0,20}(?:联网|live)[^；。\n]{0,20}(?:实时|当前|验证)",
                query,
                re.IGNORECASE,
            )
        )
        self.assertRegex(evidence, r"validate_data.py.*validate_evidence.py")
        self.assertRegex(
            evidence,
            r"authenticated snapshot[^。\n]*(?:之前|前)"
            r"[^。\n]*(?:不得|禁止)[^。\n]*(?:数字|计算)",
        )
        self.assertIsNone(
            re.search(
                r"先(?:开始)?计算[^；。\n]{0,24}(?:稍后|再|然后)"
                r"[^；。\n]{0,12}验证|"
                r"calculat\w*\s+before\s+validat\w*",
                evidence,
                re.IGNORECASE,
            )
        )
        self.assertIsNone(
            re.search(
                r"(?:C\s*(?:级|tier))[^。\n|]{0,40}"
                r"(?:至少|required|minimum|门槛)[^。\n|]{0,12}(?:3|three)|"
                r"^\|\s*C(?:\s+tier)?\s*\|\s*(?:3|three)\s*\|",
                evidence,
                re.IGNORECASE | re.MULTILINE,
            )
        )
        self.assertRegex(calculation, r"validated snapshots")
        self.assertRegex(intake, r"拒绝收集[^。\n]*(?:姓名|学生姓名)[^。\n]*电话")
        self.assertIsNone(
            re.search(
                r"(?:^|[；。\n])\s*(?!(?:拒绝|不得|禁止))[^；。\n]{0,8}"
                r"(?:收集|记录|保存)[^；。\n]{0,30}(?:学生姓名|姓名|电话|地址|班级|通信 ID)",
                intake,
            )
        )
        self.assertIsNone(
            re.search(
                r"(?:rank|位次).{0,20}(?:[+-]\s*[0-9]|(?:加|减|上浮|下调)\s*4000)",
                body,
                re.IGNORECASE,
            )
        )
        self.assertIsNone(
            re.search(
                r"(?:[ABC]\s*级|tier).{0,30}(?:至少|needs?|requires?)\s*[0-9]+\s*(?:个|sources?)",
                body,
                re.I,
            )
        )

    def test_semantic_mutation_canaries_and_safe_prose(self):
        canaries = (
            (
                "形成 authenticated snapshot 之前不得给出数字或开始计算",
                "先开始计算并给出数字，再形成 authenticated snapshot",
            ),
            (
                "不得取平均",
                "A 级至少 1 个来源，B 级至少 2 个来源，C 级至少 3 个来源并取平均",
            ),
            (
                "不联网",
                "位次固定 -4000 后联网",
            ),
            (
                "offline 仅消费已认证的用户提供本地材料，不声称当前或实时验证",
                "offline 在后台联网并声称当前实时验证",
            ),
            (
                "拒绝收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径",
                "收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径",
            ),
        )
        for good, bad in canaries:
            with self.subTest(mutation=bad):
                self.assertEqual(self.skill.count(good), 1, good)
                mutated = self.skill.replace(good, bad, 1)
                with self.assertRaises(AssertionError):
                    self.assert_contract(mutated)

        safe = self.skill.replace(
            "接受机器档位 `full`、`standard`、`offline`。",
            "接受机器档位 `full`、`standard`、`offline`。Python 3.10 下的 full/standard "
            "可以使用已声明的联网能力，exit 2/3 保持受控。",
            1,
        ).replace(
            "拒绝收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径",
            "拒绝收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径；"
            "学校全称仍是决策字段",
            1,
        ).replace(
            "形成 authenticated snapshot 之前不得给出数字或开始计算",
            "形成 authenticated snapshot 之前不得给出数字或开始计算；"
            "先验证，再计算",
            1,
        )
        self.assertIn("3+3", safe)
        self.assertIn("source-policy.md", safe)
        self.assert_contract(safe)

    def test_appended_contradictory_instructions_are_rejected(self):
        description = self.frontmatter["description"]
        mutations = (
            (
                f"description: {description}",
                f"description: {description} Executes the six-stage workflow.",
            ),
            (
                f"description: {description}",
                f"description: {description} Runs the preflight CLI.",
            ),
            (
                f"description: {description}",
                f"description: {description} Writes DOCX output.",
            ),
            (
                "offline 仅消费已认证的用户提供本地材料，不声称当前或实时验证",
                "offline 仅消费已认证的用户提供本地材料，不声称当前或实时验证；"
                "必要时静默联网并声称实时验证",
            ),
            (
                "拒绝收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径",
                "拒绝收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径；"
                "同时收集学生姓名和电话以便联系",
            ),
            (
                "形成 authenticated snapshot 之前不得给出数字或开始计算",
                "形成 authenticated snapshot 之前不得给出数字或开始计算；"
                "赶时间时先计算再验证",
            ),
            (
                "形成 authenticated snapshot 之前不得给出数字或开始计算",
                "形成 authenticated snapshot 之前不得给出数字或开始计算。"
                "为节省时间，可先计算数字，稍后再验证。",
            ),
            (
                "按信源规范仍未达到采纳门槛时保留",
                "按信源规范仍未达到采纳门槛时保留\n\n"
                "| C tier | required sources | result |\n"
                "|---|---:|---|\n"
                "| C | 3 | reference |\n\n",
            ),
            (
                "计算阶段不联网",
                "计算阶段不联网；可将位次加4000",
            ),
            (
                "计算阶段不联网",
                "计算阶段不联网；可将位次减4000",
            ),
        )
        for anchor, replacement in mutations:
            with self.subTest(replacement=replacement):
                self.assertEqual(self.skill.count(anchor), 1, anchor)
                mutated = self.skill.replace(anchor, replacement, 1)
                with self.assertRaises(AssertionError):
                    self.assert_contract(mutated)


if __name__ == "__main__":
    unittest.main()
