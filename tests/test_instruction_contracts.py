import re
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.contracts import EvidenceStatus, FactClaim, SourceCandidate, SourceTier
from scripts.source_policy import evaluate_claims


ROOT = Path(__file__).resolve().parents[1]
SOURCE_POLICY = ROOT / "references" / "source-policy.md"
RETRIEVAL_PLAYBOOK = ROOT / "references" / "retrieval-playbook.md"

SOURCE_HEADINGS = (
    "# 信源与证据采纳规范",
    "## 规范边界",
    "## 发布者与来源分级",
    "## 采纳决策表",
    "## 独立性与去重",
    "## 证据状态",
    "## 提取状态与非精确边界",
    "## 引用、时效与重放",
    "## 流程序列入口",
)
PLAYBOOK_HEADINGS = (
    "# 可复现检索流程",
    "## 统一规范入口",
    "## 1. 能力预检",
    "## 2. 构建并读取确定性查询计划",
    "## 3. 开始检索并枚举候选",
    "## 4. 分类并去重",
    "## 5. 通过匹配适配器提取",
    "## 6. 证据采纳",
    "## 7. 最终化并验证证据",
    "## 8. 为每个查询任务停止",
    "## 9. 交接确定性引擎与报告",
    "## 能力降级分支",
)


def read_utf8(path):
    source = path.read_bytes()
    text = source.decode("utf-8", errors="strict")
    if text.encode("utf-8") != source:
        raise AssertionError("reference is not canonical UTF-8")
    return source, text


def headings(text):
    return tuple(line for line in text.splitlines() if re.fullmatch(r"#{1,2} .+", line))


def section(text, heading):
    marker = heading + "\n"
    start = text.index(marker) + len(marker)
    match = re.search(r"^#{1,2} ", text[start:], flags=re.MULTILINE)
    return text[start:] if match is None else text[start : start + match.start()]


def table_in(text, heading):
    lines = [line.strip() for line in section(text, heading).splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        raise AssertionError(f"{heading} must contain a Markdown table")

    def cells(line):
        return [item.strip() for item in line.strip("|").split("|")]

    header = cells(lines[0])
    separator = cells(lines[1])
    if len(separator) != len(header) or any(re.fullmatch(r":?-{3,}:?", item) is None for item in separator):
        raise AssertionError(f"{heading} table separator is malformed")
    rows = [dict(zip(header, cells(line))) for line in lines[2:]]
    if any(len(cells(line)) != len(header) for line in lines[2:]):
        raise AssertionError(f"{heading} table row width is inconsistent")
    return rows


class InstructionContractTest(unittest.TestCase):
    def setUp(self):
        if SOURCE_POLICY.exists() and RETRIEVAL_PLAYBOOK.exists():
            self.source_bytes, self.source = read_utf8(SOURCE_POLICY)
            self.playbook_bytes, self.playbook = read_utf8(RETRIEVAL_PLAYBOOK)

    def test_required_references_exist(self):
        self.assertTrue(SOURCE_POLICY.is_file(), "missing references/source-policy.md")
        self.assertTrue(RETRIEVAL_PLAYBOOK.is_file(), "missing references/retrieval-playbook.md")

    def assert_source_contract(self, text):
        self.assertEqual(headings(text), SOURCE_HEADINGS)
        decisions = {row["路径"]: row for row in table_in(text, "## 采纳决策表")}
        expected = {
            "A": ("1", EvidenceStatus.OFFICIAL.value),
            "B→已验证 A 根": ("1", EvidenceStatus.OFFICIAL.value),
            "B（无直接 A）": ("2", EvidenceStatus.CORROBORATED.value),
            "C": ("3", EvidenceStatus.REFERENCE.value),
        }
        self.assertEqual(
            {key: (row["最少独立来源"], row["接纳状态"]) for key, row in decisions.items()},
            expected,
        )
        for row in decisions.values():
            self.assertEqual(row["精确冲突"], "保留 conflict；value=None；停止该层精确采纳")

        dimensions = table_in(text, "## 独立性与去重")
        self.assertEqual(
            {row["维度"] for row in dimensions},
            {"publisher", "canonical_site", "citation_root", "content_fingerprint"},
        )
        self.assertTrue(all(row["重复计数"] == "同一连通分量只计 1 个" for row in dimensions))

        state_rows = table_in(text, "## 证据状态")
        self.assertEqual({row["状态"] for row in state_rows}, {item.value for item in EvidenceStatus})
        nonexact = {row["状态"]: row["精确事实"] for row in state_rows}
        for state in ("inferred", "partial", "masked", "conflict", "missing"):
            self.assertEqual(nonexact[state], "否")

        boundary_rows = table_in(text, "## 提取状态与非精确边界")
        self.assertEqual(
            {row["输入情形"] for row in boundary_rows},
            {
                "masked-boundary",
                "cropped-or-local-ocr",
                "formula-cell",
                "uncertain-cell",
                "incomplete-page-or-sheet",
            },
        )
        self.assertTrue(all(row["精确边界"] == "禁止" for row in boundary_rows))

        citation_rows = table_in(text, "## 引用、时效与重放")
        self.assertEqual(
            {row["字段"] for row in citation_rows},
            {
                "publisher",
                "canonical_url_or_attachment_id",
                "retrieved_at",
                "content_fingerprint",
                "citation_chain",
                "year",
                "extraction_method",
                "locator",
            },
        )

    def assert_playbook_contract(self, text):
        self.assertEqual(headings(text), PLAYBOOK_HEADINGS)
        for heading in PLAYBOOK_HEADINGS[2:11]:
            self.assertEqual(section(text, heading).count("完成标准："), 1)

        controls = {row["控制项"]: row["值"] for row in table_in(text, "## 3. 开始检索并枚举候选")}
        self.assertEqual(
            controls,
            {
                "candidate-cap": "10",
                "retry-per-network-action": "1",
                "first-plausible-stop": "禁止",
            },
        )
        stops = table_in(text, "## 8. 为每个查询任务停止")
        self.assertEqual(
            [row["停止 ID"] for row in stops],
            ["accepted", "candidate-cap", "variants-exhausted", "unavailable"],
        )
        self.assertEqual(len(stops), 4)

        degradation = {row["档位"]: row for row in table_in(text, "## 能力降级分支")}
        self.assertEqual(set(degradation), {"complete", "standard", "offline"})
        self.assertTrue(
            all(row["证据规范"] == "[同一信源规范](source-policy.md)" for row in degradation.values())
        )
        self.assertEqual(degradation["offline"]["实时声明"], "禁止声称当前或实时验证")

        dedup = text.index("## 4. 分类并去重")
        admission = text.index("## 6. 证据采纳")
        validation = text.index("## 7. 最终化并验证证据")
        calculation = text.index("## 9. 交接确定性引擎与报告")
        self.assertLess(dedup, admission)
        self.assertLess(validation, calculation)

    def candidate(self, name, tier, *, citation_root=None):
        return SourceCandidate(
            source_id=name,
            url=f"https://{name}.example.test/item",
            publisher=f"Synthetic Publisher {name}",
            tier=tier,
            published_at="2026-08-01",
            retrieved_at="2026-08-24T00:00:00Z",
            content_hash=f"sha256:{name}",
            citation_root=citation_root or f"https://root-{name}.example.test/original",
            summary="Synthetic source",
        )

    @staticmethod
    def claim(name):
        return FactClaim("synthetic_field", 1, "unit", name, "synthetic-method")

    def test_exact_headings_tables_and_order_form_executable_documents(self):
        self.assert_source_contract(self.source)
        self.assert_playbook_contract(self.playbook)

    def test_document_thresholds_match_runtime_admission(self):
        decisions = {row["路径"]: row for row in table_in(self.source, "## 采纳决策表")}
        runtime_cases = {}
        for tier, key, count in (
            (SourceTier.A, "A", 1),
            (SourceTier.B, "B（无直接 A）", 2),
            (SourceTier.C, "C", 3),
        ):
            sources = [self.candidate(f"{tier.value.lower()}{index}", tier) for index in range(count)]
            fact = evaluate_claims("synthetic_field", [self.claim(item.source_id) for item in sources], sources)
            runtime_cases[key] = (str(count), fact.status.value)

        official = self.candidate("official", SourceTier.A)
        rooted_b = self.candidate("rooted-b", SourceTier.B, citation_root=official.url)
        rooted_fact = evaluate_claims("synthetic_field", [self.claim(rooted_b.source_id)], [official, rooted_b])
        runtime_cases["B→已验证 A 根"] = ("1", rooted_fact.status.value)

        documented = {
            key: (row["最少独立来源"], row["接纳状态"])
            for key, row in decisions.items()
        }
        self.assertEqual(documented, runtime_cases)

    def test_policy_co_locates_identity_state_freshness_and_extraction_rules(self):
        independence = section(self.source, "## 独立性与去重")
        self.assertIn("canonical URL", independence)
        self.assertIn("移除 tracking", independence)
        self.assertIn("拒绝原因", independence)
        self.assertIn("复制稿和其 syndication", independence)

        extraction = section(self.source, "## 提取状态与非精确边界")
        for adapter in ("HTML", "XLSX", "PDF", "OCR", "QR"):
            self.assertIn(adapter, extraction)
        self.assertIn("提取状态不是证据等级", extraction)
        self.assertIn("580分以上", extraction)
        self.assertIn("前100名", extraction)

        citation = section(self.source, "## 引用、时效与重放")
        self.assertIn("deterministic query plan", citation)
        self.assertIn("历史", citation)
        self.assertIn("最小支持摘录", citation)
        self.assertIn("区间", citation)
        self.assertIn("method/source/bounds", citation)
        self.assertIn("secure downloader", citation)

    def test_playbook_steps_are_bounded_and_use_current_public_seams(self):
        required = (
            "scripts.preflight",
            "scripts.query_plan",
            "scripts.source_policy.py",
            "EvidenceStore",
            "scripts.validate_evidence",
            "authenticated immutable evidence snapshot",
            "deterministic engine/report",
        )
        for seam in required:
            self.assertIn(seam, self.playbook)
        query_step = section(self.playbook, "## 2. 构建并读取确定性查询计划")
        for field in (
            "task_id",
            "province",
            "mode",
            "canonical_subjects",
            "year",
            "kind",
            "required_fields",
            "max_candidates",
        ):
            self.assertIn(field, query_step)
        extraction = section(self.playbook, "## 5. 通过匹配适配器提取")
        self.assertIn("page/sheet/table/row", extraction)
        self.assertIn("page/image/bbox", extraction)
        self.assertIn("decoded text", extraction)
        self.assertIn("secure downloader", extraction)

    def test_relative_links_exist_and_legacy_migration_inputs_are_not_normative(self):
        links = []
        for path, text in ((SOURCE_POLICY, self.source), (RETRIEVAL_PLAYBOOK, self.playbook)):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                self.assertNotRegex(target, r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
                self.assertNotIn(target, {"web-search-playbook.md", "gaokao-provinces.md"})
                self.assertTrue((path.parent / target).is_file(), target)
                links.append((path.name, target))
        self.assertEqual(
            links,
            [("source-policy.md", "retrieval-playbook.md")]
            + [("retrieval-playbook.md", "source-policy.md")] * 4,
        )

    def test_public_command_probes_named_by_playbook_execute(self):
        commands = re.findall(r"^\$ (python -m scripts\.[a-z_]+ --help)$", self.playbook, flags=re.MULTILINE)
        self.assertEqual(
            commands,
            [
                "python -m scripts.preflight --help",
                "python -m scripts.query_plan --help",
                "python -m scripts.validate_evidence --help",
            ],
        )
        for command in commands:
            completed = subprocess.run(
                [sys.executable, *command.split()[1:]],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_references_are_strict_utf8_deterministic_and_public_safe(self):
        self.assertEqual(self.source_bytes, SOURCE_POLICY.read_bytes())
        self.assertEqual(self.playbook_bytes, RETRIEVAL_PLAYBOOK.read_bytes())
        combined = self.source + "\n" + self.playbook
        forbidden = (
            r"(?i)(?:[a-z]:[\\/]|\\\\[^\\\s]+[\\/]|/(?:home|users|tmp|var)/)",
            r"(?<!\d)1[3-9]\d{9}(?!\d)",
            r"(?i)(?:api[_-]?key|password|bearer)\s*[:=]",
            r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
            r"(?i)\b(?:xibao|fetch_via_qr|tools/validate_data)\b",
            r"\b4000\b",
            r"(?i)(?:湖北|hubei|is_inside_hubei)",
            r"(?i)(?:^|[\s`])output/",
            r"recommend_schools\([^,\n]+,[^,\n]+\)",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, combined))

    def test_mutation_canaries_detect_semantic_contract_regressions(self):
        policy_mutations = (
            self.source.replace("| C | 3 | reference |", "| C | 2 | reference |", 1),
            self.source.replace(
                "保留 conflict；value=None；停止该层精确采纳",
                "对冲突值取平均后继续",
                1,
            ),
        )
        for mutated in policy_mutations:
            with self.subTest(kind="policy"), self.assertRaises(AssertionError):
                self.assert_source_contract(mutated)

        playbook_mutations = (
            self.playbook.replace("| candidate-cap | 10 |", "| candidate-cap | 11 |", 1),
            self.playbook.replace("## 4. 分类并去重", "## SWAP-DEDUP", 1)
            .replace("## 6. 证据采纳", "## 4. 分类并去重", 1)
            .replace("## SWAP-DEDUP", "## 6. 证据采纳", 1),
            self.playbook.replace("## 7. 最终化并验证证据", "## SWAP-VALIDATE", 1)
            .replace("## 9. 交接确定性引擎与报告", "## 7. 最终化并验证证据", 1)
            .replace("## SWAP-VALIDATE", "## 9. 交接确定性引擎与报告", 1),
            self.playbook.replace("禁止声称当前或实时验证", "允许声称实时数据已验证", 1),
        )
        for mutated in playbook_mutations:
            with self.subTest(kind="playbook"), self.assertRaises(AssertionError):
                self.assert_playbook_contract(mutated)

        safe_policy = self.source + "\n平均响应时间属于工具性能，不是冲突事实的处理动作。\n"
        safe_playbook = self.playbook + "\n候选第 11 项不会被访问；这是边界说明而不是 candidate-cap。\n"
        self.assert_source_contract(safe_policy)
        self.assert_playbook_contract(safe_playbook)


if __name__ == "__main__":
    unittest.main()
