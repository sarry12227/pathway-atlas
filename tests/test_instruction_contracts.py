import json
import re
import subprocess
import sys
import unittest
from dataclasses import fields
from pathlib import Path

from scripts.contracts import CapabilityTier, EvidenceStatus, FactClaim, SourceCandidate, SourceTier
from scripts.preflight import HOST_CAPABILITIES, OPTIONAL_MODULES
from scripts.evidence import FACT_EXTRACTION_METHODS
from scripts.province_registry import ProvinceConfig
from scripts.query_plan import QueryPlan, QueryTask
from scripts.source_policy import evaluate_claims


ROOT = Path(__file__).resolve().parents[1]
SOURCE_POLICY = ROOT / "references" / "source-policy.md"
RETRIEVAL_PLAYBOOK = ROOT / "references" / "retrieval-playbook.md"
HOST_WORKFLOW = ROOT / "references" / "host-workflow.md"
EVIDENCE_SCHEMA = ROOT / "schemas" / "evidence-bundle.schema.json"
HOST_GUIDES = tuple(
    ROOT / "references" / "hosts" / name
    for name in ("generic.md", "codex.md", "claude-code.md", "kimi.md")
)
HOST_CAPABILITY_ROWS = (
    "search", "browse", "vision", "local_exec", "file_output", "offline",
)

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
        if HOST_WORKFLOW.exists():
            self.host_workflow_bytes, self.host_workflow = read_utf8(HOST_WORKFLOW)

    def test_required_references_exist(self):
        self.assertTrue(SOURCE_POLICY.is_file(), "missing references/source-policy.md")
        self.assertTrue(RETRIEVAL_PLAYBOOK.is_file(), "missing references/retrieval-playbook.md")
        self.assertTrue(HOST_WORKFLOW.is_file(), "missing references/host-workflow.md")

    def test_playbook_drives_one_resumable_planning_session(self):
        for marker in (
            "scripts/planning_session.py", "init", "confirm", "next", "ingest",
            "finalize", "compute", "status", "Y → Y-1 → Y-2 → Y-3",
            "打开原页面", "搜索摘要", "unavailable reason", "exact adapter",
            "不得虚构 `province.json`", "host-workflow.md", "--limit 1..100",
            "--newer-task <completed-task-id>", "ocr_rows",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.playbook)
        self.assertLess(self.playbook.index("confirm"), self.playbook.index("开始检索"))
        finalization = section(self.playbook, "## 7. 最终化并验证证据")
        self.assertLess(finalization.index("validate_evidence"), finalization.index("compute"))

    def test_host_guides_map_real_tools_to_the_shared_session_loop(self):
        for path in HOST_GUIDES:
            _source, text = read_utf8(path)
            with self.subTest(path=path.name):
                self.assertIn("[retrieval playbook](../retrieval-playbook.md)", text)
                self.assertNotIn("## Ordered handoff", text)
                self.assertNotIn("## Preflight", text)
                for loop_marker in ("planning_session.py status", "task-by-task", "validate_evidence.py"):
                    self.assertNotIn(loop_marker, text)
                self.assertNotIn("structured OCR row JSON", text)
                self.assertNotRegex(text, r"(?i)ask the user to (?:run|provide).*(?:command|JSON|path)")

    def test_playbook_uses_the_typed_admission_bridge_as_a_thin_handoff(self):
        extraction = section(self.playbook, "## 5. 通过匹配适配器提取")
        for marker in (
            "scripts.adapters.admission_bridge.bridge_admission_evidence",
            "QueryTask",
            "ValidatedAdmissionRow",
            "admission_row_hash",
            "coverage_status",
            "EvidenceStore",
        ):
            self.assertIn(marker, extraction)
        self.assertNotRegex(
            extraction,
            r"(?:A|B|C)\s*(?:级|tier)[^。\n|]{0,30}"
            r"(?:至少|required|minimum)[^。\n|]{0,12}[0-9]",
        )

    def test_playbook_names_the_factory_only_completed_chain(self):
        required = (
            "build_task_evidence_outcome",
            "evidence_outcome=",
            "build_evidence_manifest_outcome",
            "build_calculation_outcome",
            "build_report_publication_outcome",
            "同一宿主进程",
            "裸 digest",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.playbook)

    def test_required_host_guides_exist(self):
        self.assertEqual(
            tuple(sorted(path.name for path in (ROOT / "references" / "hosts").glob("*.md"))),
            tuple(sorted(path.name for path in HOST_GUIDES)),
        )

    def assert_host_contract(self, path, text):
        self.assertEqual(
            headings(text),
            (
                f"# {path.stem.replace('-', ' ').title()} host capability mapping",
                "## Intake boundary",
                "## Capability map",
                "## Safety boundary",
            )
            if path.name != "claude-code.md"
            else (
                "# Claude Code host capability mapping",
                "## Intake boundary",
                "## Capability map",
                "## Safety boundary",
            ),
        )
        intro = text.split("## Intake boundary", 1)[0]
        intro_lines = [line for line in intro.splitlines()[1:] if line.strip()]
        self.assertEqual(len(intro_lines), 2)
        self.assertTrue(all(line.startswith("- ") for line in intro_lines))
        intake = section(text, "## Intake boundary")
        for marker in (
            "parse_numbered_questionnaire",
            "build_profile_from_questionnaire",
            "ordinary conversation text",
            "never ask the user for JSON or a path",
            "remains `unknown` or empty",
        ):
            self.assertIn(marker, intake)
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        self.assertEqual(links, ["../retrieval-playbook.md", "../source-policy.md"])
        for target in links:
            self.assertTrue((path.parent / target).is_file(), target)
            self.assertNotIn(Path(target).name, {"web-search-playbook.md", "gaokao-provinces.md"})

        rows = table_in(text, "## Capability map")
        self.assertEqual(tuple(rows[0]), ("Capability", "Use", "Declare", "Absent fallback"))
        self.assertEqual(tuple(row["Capability"] for row in rows), HOST_CAPABILITY_ROWS)
        self.assertEqual(len({row["Capability"] for row in rows}), 6)
        self.assertTrue(all(all(row[column] for column in row) for row in rows))
        by_capability = {row["Capability"]: row for row in rows}

        declared_flags = tuple(
            dict.fromkeys(re.findall(r"--host-capability ([a-z_]+)", text))
        )
        self.assertEqual(declared_flags, HOST_CAPABILITIES)
        for excluded in ("local_exec", "file_output", "offline"):
            self.assertNotIn(f"--host-capability {excluded}", text)

        fallback_markers = {
            "search": ("already authenticated material", "offline mode", "discovery unavailable"),
            "browse": ("do not claim page verification", "already authenticated material", "offline mode"),
            "vision": (
                "machine-readable HTML/XLSX/PDF/text",
                "host-decoded QR payload",
                "missing",
            ),
            "local_exec": ("stop before deterministic calculation", "disclose the limitation", "move the session"),
            "file_output": ("path-neutral structured handoff", "do not claim", "written"),
            "offline": (
                "no-live-network",
                "no search/browse",
                "already attached authenticated inputs",
                "current/live facts unavailable",
            ),
        }
        for capability, markers in fallback_markers.items():
            fallback = by_capability[capability]["Absent fallback"]
            for marker in markers:
                self.assertIn(marker, fallback)

        self.assertIn("current session", text)
        self.assertIn("apply the linked policy/playbook unchanged", text)
        self.assertIn("Capability loss changes coverage only", text)
        safety = section(text, "## Safety boundary")
        for marker in ("explicit user authorization", "evidence disclosure", "local/host-native", "missing fact"):
            self.assertIn(marker, safety)
        self.assertNotIn("silent external upload", text)
        self.assertNotRegex(
            text,
            r"(?i)(?:candidate[- ]cap|retry-per-network|\b[ABC][ -]tier\b|"
            r"(?:one|two|three|[1-9]) independent sources?|lower the source threshold|"
            r"average conflicting)",
        )
        forbidden_public_data = (
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
        for pattern in forbidden_public_data:
            self.assertIsNone(re.search(pattern, text))

    def test_host_guides_have_structural_parity_and_current_session_mappings(self):
        expected_examples = {
            "generic.md": (
                "unknown host",
                "search API",
                "browser/page reader",
                "vision/OCR",
                "shell/runner",
                "writable workspace",
            ),
            "codex.md": (
                "web/search",
                "in-app browser",
                "local command",
                "local image",
                "workspace file",
                "purpose-built connector",
                "browser state",
            ),
            "claude-code.md": ("web search/fetch", "shell/local file", "text-only", "third-party OCR"),
            "kimi.md": ("联网搜索", "网页读取", "本地命令/文件", "图像理解", "structured handoff"),
        }
        for path in HOST_GUIDES:
            source, text = read_utf8(path)
            with self.subTest(path=path.name):
                self.assertEqual(source, path.read_bytes())
                self.assert_host_contract(path, text)
                for marker in expected_examples[path.name]:
                    self.assertIn(marker, text)

    def test_host_preflight_examples_execute_against_runtime_vocabulary(self):
        preflight = section(self.playbook, "## 1. 能力预检")
        rendered = re.search(r"^python scripts/preflight\.py (.+)$", preflight, flags=re.MULTILINE)
        self.assertIsNotNone(rendered)
        args = rendered.group(1).replace("[", "").replace("]", "").split()
        completed = subprocess.run(
            [sys.executable, "scripts/preflight.py", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(tuple(report["host_capabilities"]), tuple(sorted(HOST_CAPABILITIES)))
        offline = subprocess.run(
            [sys.executable, "scripts/preflight.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(offline.returncode, 0, offline.stdout + offline.stderr)
        self.assertEqual(json.loads(offline.stdout)["tier"], CapabilityTier.OFFLINE.value)

    def test_host_guide_mutation_canaries_reject_unsafe_workflow_drift(self):
        for path in HOST_GUIDES:
            _source, text = read_utf8(path)
            file_output_guard = "do not claim an evidence bundle or report was written"
            self.assertEqual(text.count(file_output_guard), 1)
            file_output_mutation = text.replace(
                file_output_guard,
                "claim an evidence bundle or report was written",
                1,
            )
            self.assertIn("do not claim page verification", file_output_mutation)
            self.assertNotIn(file_output_guard, file_output_mutation)
            mutations = (
                text.replace("local/host-native", "silent external upload", 1),
                text.replace("current/live facts unavailable", "current facts verified", 1),
                text.replace("stop before deterministic calculation", "continue deterministic calculation", 1),
                file_output_mutation,
                text + "\nPass --host-capability local_exec.\n",
                text + "\nTwo independent sources are enough; lower the source threshold.\n",
            )
            for index, mutated in enumerate(mutations):
                with self.subTest(path=path.name, mutation=index), self.assertRaises(AssertionError):
                    self.assert_host_contract(path, mutated)

            safe = text + "\nA file_output gate may record that no file was written.\n"
            self.assert_host_contract(path, safe)

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
        citation_by_field = {row["字段"]: row["完成条件"] for row in citation_rows}
        self.assertEqual(
            tuple(re.findall(r"`([^`]+)`", citation_by_field["extraction_method"])),
            FACT_EXTRACTION_METHODS,
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

        degradation = {row["机器档位"]: row for row in table_in(text, "## 能力降级分支")}
        self.assertEqual(set(degradation), {item.value for item in CapabilityTier})
        self.assertEqual(degradation[CapabilityTier.FULL.value]["人类标签"], "完整档")
        self.assertTrue(
            all(row["证据规范"] == "[同一信源规范](source-policy.md)" for row in degradation.values())
        )
        self.assertEqual(degradation["offline"]["实时声明"], "禁止声称当前或实时验证")

        operations = {}
        for heading in (
            "## 4. 分类并去重",
            "## 6. 证据采纳",
            "## 7. 最终化并验证证据",
        ):
            rows = table_in(text, heading)
            self.assertEqual(len(rows), 1)
            operations[heading] = (
                rows[0]["输入状态"], rows[0]["动作"], rows[0]["输出状态"]
            )
        self.assertEqual(
            operations,
            {
                "## 4. 分类并去重": ("candidates-enumerated", "deduplicate_candidates", "independence-components"),
                "## 6. 证据采纳": ("extraction-results", "persist-source-policy-result", "EvidenceStore-persisted"),
                "## 7. 最终化并验证证据": ("EvidenceStore-persisted", "finalize-then-validate", "authenticated-snapshot"),
            },
        )

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
        self.assertIn("EvidenceStore.add_fact(..., year=, extraction_method=, locator=)", citation)
        self.assertIn("fact-provenance", citation)
        self.assertIn("恰好 1 条", citation)
        self.assertIn("manifest hash", citation)
        for unsafe_locator in (
            "drive prefix",
            "environment reference",
            "UNC/device/absolute/traversal/URL",
            "secret",
            "PII",
        ):
            self.assertIn(unsafe_locator, citation)

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
        query_task_fields = {item.name for item in fields(QueryTask)}
        for field in (
            "task_id", "province", "subject_group", "year", "kind",
            "required_extraction_fields", "max_candidates",
        ):
            self.assertIn(field, query_task_fields)
            self.assertIn(field, query_step)
        self.assertIn("subject_group", {item.name for item in fields(QueryPlan)})
        self.assertIn("mode", {item.name for item in fields(ProvinceConfig)})
        self.assertIn("ProvinceConfig.mode", query_step)
        for invented in ("canonical_subjects", "required_fields"):
            self.assertNotIn(invented, query_step)
        extraction = section(self.playbook, "## 5. 通过匹配适配器提取")
        self.assertIn("page/sheet/table/row", extraction)
        self.assertIn("page/image/bbox", extraction)
        self.assertIn("decoded text", extraction)
        self.assertIn("secure downloader", extraction)

    def test_capability_document_contract_matches_runtime_machine_vocabulary(self):
        preflight = section(self.playbook, "## 1. 能力预检")
        rows = table_in(self.playbook, "## 1. 能力预检")
        declared = {row["类型"]: tuple(item.strip() for item in row["有限值"].split(",")) for row in rows}
        self.assertEqual(declared["host_capabilities"], HOST_CAPABILITIES)
        self.assertEqual(declared["optional_modules"], OPTIONAL_MODULES)
        self.assertEqual(declared["capability_tier"], tuple(item.value for item in CapabilityTier))
        self.assertNotIn("complete", preflight)
        self.assertIn("full（完整档）", self.source)
        self.assertNotIn("complete、standard", self.source)

    def test_evidence_schema_defines_the_canonical_fact_provenance_record(self):
        schema = json.loads(EVIDENCE_SCHEMA.read_text("utf-8"))
        provenance = schema["$defs"]["factProvenance"]
        self.assertFalse(provenance["additionalProperties"])
        self.assertEqual(
            set(provenance["required"]),
            {"kind", "fact_id", "source_ids", "year", "extraction_method", "locator"},
        )
        self.assertEqual(provenance["properties"]["kind"], {"const": "fact-provenance"})
        self.assertEqual(
            tuple(provenance["properties"]["extraction_method"]["enum"]),
            FACT_EXTRACTION_METHODS,
        )
        self.assertEqual(provenance["properties"]["year"]["type"], "integer")
        contexts = schema["properties"]["contexts"]
        self.assertEqual(contexts["type"], "array")
        self.assertEqual(
            contexts["items"]["anyOf"][0], {"$ref": "#/$defs/factProvenance"}
        )

    def test_relative_links_exist_and_legacy_migration_inputs_are_not_normative(self):
        links = []
        for path, text in (
            (SOURCE_POLICY, self.source),
            (RETRIEVAL_PLAYBOOK, self.playbook),
            (HOST_WORKFLOW, self.host_workflow),
        ):
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                self.assertNotRegex(target, r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
                self.assertNotIn(target, {"web-search-playbook.md", "gaokao-provinces.md"})
                self.assertTrue((path.parent / target).is_file(), target)
                links.append((path.name, target))
        self.assertEqual(
            links,
            [("source-policy.md", "retrieval-playbook.md")]
            + [
                ("retrieval-playbook.md", "source-policy.md"),
                ("retrieval-playbook.md", "host-workflow.md"),
            ]
            + [("retrieval-playbook.md", "source-policy.md")] * 3,
        )

    def test_public_command_probes_named_by_playbook_execute(self):
        commands = re.findall(r"^\$ (python -m scripts\.[a-z_]+ --help)$", self.playbook, flags=re.MULTILINE)
        self.assertEqual(
            commands,
            [
                "python -m scripts.preflight --help",
                "python -m scripts.query_plan --help",
                "python -m scripts.validate_evidence --help",
                "python -m scripts.planning_session --help",
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

    def test_host_workflow_guide_matches_the_executable_facade(self):
        commands = re.findall(
            r"^python -m scripts\.host_workflow ([a-z]+)(?: .*)?$",
            self.host_workflow,
            flags=re.MULTILINE,
        )
        self.assertEqual(
            set(commands),
            {"start", "next", "ingest", "unavailable", "finish"},
        )
        self.assertNotIn("status", commands)
        normalized = " ".join(self.host_workflow.split())
        for marker in (
            "`pending` is the total",
            "`next` contains only the requested display slice",
            "default limit is 3",
            "range is 1 through 100",
            "newest year first",
            "There is no separate `status` command",
            "--reason newer_comparable_year_accepted --newer-task <completed-task-id>",
            "typed receipt from the journal",
            "never skips older tasks automatically",
            '"adapter": "ocr_rows"',
            '"ocr_path":',
            "min_exact_confidence` at 0.95",
            "page/image/bbox",
            "UTF-8-sig",
            "normalizes CRLF and CR to LF",
            "candidate hash still covers the original saved bytes",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, normalized)

        completed = subprocess.run(
            [sys.executable, "-m", "scripts.host_workflow", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        for marker in (
            "{start,next,ingest,unavailable,finish}",
            "--limit LIMIT",
            "--newer-task NEWER_TASK",
            "--submission SUBMISSION",
        ):
            self.assertIn(marker, completed.stdout)

    def test_references_are_strict_utf8_deterministic_and_public_safe(self):
        self.assertEqual(self.source_bytes, SOURCE_POLICY.read_bytes())
        self.assertEqual(self.playbook_bytes, RETRIEVAL_PLAYBOOK.read_bytes())
        self.assertEqual(self.host_workflow_bytes, HOST_WORKFLOW.read_bytes())
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
        host_forbidden = (
            r"(?i)(?:\b[a-z]:\\|\\\\[^\\\s]+[\\/]|/(?:home|users|tmp|var)/)",
            forbidden[1],
            forbidden[2],
            forbidden[3],
        )
        for pattern in host_forbidden:
            with self.subTest(host_pattern=pattern):
                self.assertIsNone(re.search(pattern, self.host_workflow))

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
            self.playbook.replace(
                "| candidates-enumerated | deduplicate_candidates | independence-components |",
                "| candidates-enumerated | count-before-dedup | independence-components |", 1,
            ),
            self.playbook.replace(
                "| extraction-results | persist-source-policy-result | EvidenceStore-persisted |",
                "| extraction-results | average-conflict | EvidenceStore-persisted |", 1,
            ),
            self.playbook.replace(
                "| EvidenceStore-persisted | finalize-then-validate | authenticated-snapshot |",
                "| EvidenceStore-persisted | calculate-before-validate | authenticated-snapshot |", 1,
            ),
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
