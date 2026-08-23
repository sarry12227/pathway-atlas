# Cross-Agent Retrieval Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real-time province, admission, high-school joy-report, and diversified-admission research reproducible across Codex, Claude Code, Kimi, and other capable Agents through one query plan, adapter contract, source policy, and evidence-to-engine handoff.

**Architecture:** `SKILL.md` is a thin host-neutral orchestrator. It runs preflight, creates a deterministic query plan, delegates discovery to host tools, normalizes downloaded/text/OCR inputs through adapters, applies the shared evidence policy, invokes the deterministic engine, and renders provenance-rich output. Host guides map tool names but do not alter quality thresholds.

**Tech Stack:** Markdown Agent instructions, Python 3.10–3.13, JSON/CSV/XLSX/PDF/image adapters, optional `openpyxl`/`pdfplumber`, host vision tools, `unittest` replay fixtures.

**Spec:** `.scratch/shengxue-skill-open-source/spec.md`

**Depends on:** `2026-08-22-01-evidence-foundation.md`, `2026-08-22-02-engine-hardening.md`

## Global Constraints

- Search at most 10 accessible candidates for each fact task, not 10 snippets treated as 10 independent sources.
- Deduplicate publisher, citation root, and content fingerprint before counting corroboration.
- Prefer A-tier official sources, then B-tier reliable reproductions, then three agreeing independent C-tier publishers as reference-grade evidence.
- A source being high in search results does not raise its tier.
- Current and previous years are queried independently; older years are used only when the report discloses the coverage window.
- QR/OCR is a local or host-native extraction path. No third-party upload occurs silently.
- Masked cells, cropped tables, monotonicity failures, and conflicting OCR readings remain non-exact.
- Capability tiers are full, standard, and offline; every report states which tier ran.
- Every normalized fact must preserve source URL, publisher, publication/retrieval time, method, location/page/sheet, and source ID.
- Use TDD and replay fixtures; live web tests are smoke checks, never the deterministic release gate.

## File Map

- `scripts/query_plan.py`: deterministic research-task generation.
- `schemas/query-plan.schema.json`: host-neutral query plan contract.
- `references/provinces/index.json`: supported new-gaokao province/mode catalog and official source roots.
- `references/source-policy.md`: source tiers, independence, conflicts, and quoting rules.
- `references/retrieval-playbook.md`: execution workflow and fallback ladder.
- `scripts/adapters/`: HTML, spreadsheet, PDF/text, OCR-row, and QR normalization.
- `references/hosts/`: Codex, Claude Code, Kimi, and generic host mappings.
- `SKILL.md`: compact six-stage workflow.
- `tests/fixtures/replay/`: synthetic Heilongjiang-like QR, Shanghai-like masked OCR, and joy-report conflict cases.

---

### Task 1: Deterministic Query Plan Contract

**Files:**
- Create: `scripts/query_plan.py`
- Create: `schemas/query-plan.schema.json`
- Create: `tests/test_query_plan.py`

**Interfaces:**
- Consumes: `StudentProfile`, `ProvinceConfig`, current exam year, high-school name, and requested pathways.
- Produces: `build_query_plan(...) -> QueryPlan` containing stable task IDs, fact type, year, query variants, preferred source tiers, maximum candidates `10`, freshness rules, and required extraction fields.

- [ ] **Step 1: Write failing query-plan tests**

```python
class QueryPlanTest(unittest.TestCase):
    def test_plan_covers_score_tables_admissions_joy_reports_and_pathways(self):
        plan = build_query_plan(self.profile(), self.province(), exam_year=2026)
        kinds = {task.kind for task in plan.tasks}
        self.assertTrue({"score_table", "admission", "joy_report", "pathway_policy"} <= kinds)

    def test_each_task_caps_accessible_candidates_at_ten(self):
        plan = build_query_plan(self.profile(), self.province(), exam_year=2026)
        self.assertTrue(all(task.max_candidates == 10 for task in plan.tasks))

    def test_years_are_explicit_not_implicit_latest(self):
        years = {t.year for t in build_query_plan(self.profile(), self.province(), 2026).tasks if t.kind == "admission"}
        self.assertEqual(years, {2024, 2025, 2026})
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_query_plan -v`  
Expected: ERROR because the query-plan module is absent.

- [ ] **Step 3: Implement stable task generation**

Use stable IDs such as `score-table:黑龙江:物理:2026`. Generate Chinese query variants for authority name, province, year, primary/secondary subjects, school name, joy-report synonyms, and specific pathway names. Admissions default to the current year when released plus the two preceding completed years; joy reports request up to three recent years. Record when the current-year source is not yet expected rather than substituting an older year invisibly.

- [ ] **Step 4: Run tests and serialize one plan**

```bash
python -m unittest tests.test_query_plan -v
python scripts/query_plan.py --profile tests/fixtures/profiles/demo.json --province tests/fixtures/provinces/demo-312/province.json --exam-year 2026
```

Expected: tests PASS; CLI JSON conforms to `query-plan.schema.json` and contains no personal name.

- [ ] **Step 5: Commit**

```bash
git add scripts/query_plan.py schemas/query-plan.schema.json tests/test_query_plan.py
git commit -m "feat: generate deterministic research query plans"
```

### Task 2: New-Gaokao Province and Official-Root Catalog

**Files:**
- Create: `references/provinces/index.json`
- Create: `references/provinces/README.md`
- Create: `schemas/province-catalog.schema.json`
- Create: `tests/test_province_catalog.py`

**Interfaces:**
- Consumes: current primary pages from provincial education/exam authorities.
- Produces: catalog records with `province`, `aliases`, `mode`, `authority_name`, `official_roots`, `verified_at`, and `notes`; no dynamic admission numbers are bundled.

- [ ] **Step 1: Write failing completeness and URL tests**

```python
class ProvinceCatalogTest(unittest.TestCase):
    def test_every_declared_new_gaokao_province_has_mode_and_official_root(self):
        for item in self.catalog["provinces"]:
            self.assertIn(item["mode"], {"3+1+2", "3+3"})
            self.assertTrue(item["official_roots"])
            self.assertTrue(all(url.startswith("https://") for url in item["official_roots"]))

    def test_aliases_are_unique(self):
        aliases = [a for p in self.catalog["provinces"] for a in p["aliases"]]
        self.assertEqual(len(aliases), len(set(aliases)))
```

- [ ] **Step 2: Verify the catalog is missing**

Run: `python -m unittest tests.test_province_catalog -v`  
Expected: ERROR due to absent catalog.

- [ ] **Step 3: Research and record the catalog from primary sources**

Browse each provincial education examination authority or education department, capture only the stable official root and declared reform mode, and set the actual verification date. If an authority has multiple official hosts, list each. Do not copy dynamic score tables or third-party rankings into this file. `README.md` explains how maintainers verify redirects, domain changes, and reform-mode changes.

- [ ] **Step 4: Run schema tests and manually sample redirects**

Run: `python -m unittest tests.test_province_catalog -v`  
Expected: PASS. Manually open at least one authority root from each reform batch and record any redirect correction in the same commit.

- [ ] **Step 5: Commit**

```bash
git add references/provinces schemas/province-catalog.schema.json tests/test_province_catalog.py
git commit -m "docs: catalog official new-gaokao source roots"
```

### Task 3: Structured HTML and Spreadsheet Adapters

**Files:**
- Create: `scripts/adapters/__init__.py`
- Create: `scripts/adapters/html_table.py`
- Create: `scripts/adapters/spreadsheet.py`
- Create: `tests/fixtures/replay/structured/score-table.html`
- Create: `tests/fixtures/replay/structured/admission.xlsx`
- Create: `tests/test_structured_adapters.py`

**Interfaces:**
- Consumes: local files previously obtained through the secure downloader plus a declared column mapping.
- Produces: `ExtractedTable` containing normalized cells, source location, row confidence, coverage range, and warnings; adapters never fetch URLs.

- [ ] **Step 1: Write failing adapter tests**

```python
class StructuredAdapterTest(unittest.TestCase):
    def test_html_preserves_caption_and_row_location(self):
        table = extract_html_table(self.fixture("score-table.html"), mapping=self.mapping)
        self.assertEqual(table.rows[0].location, "table[1]/tbody/tr[1]")
        self.assertEqual(table.coverage.lower_score, 650)

    def test_spreadsheet_preserves_sheet_and_formula_value_status(self):
        table = extract_spreadsheet(self.fixture("admission.xlsx"), sheet="物理类", mapping=self.mapping)
        self.assertEqual(table.rows[0].location, "物理类!A2:F2")
        self.assertNotEqual(table.rows[0].status, "official")
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_structured_adapters -v`  
Expected: ERROR because adapters and fixtures do not exist.

- [ ] **Step 3: Implement file-only normalization**

Use the standard library HTML parser for simple tables and `openpyxl` for XLSX. Require the caller to supply expected headers/aliases and table identity; do not guess among unrelated tables. Preserve sheet/cell or DOM row coordinates. Mark formula-only, merged, empty, duplicated-header, or truncated rows as warnings. Validate score/rank monotonicity without repairing values.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_structured_adapters -v`  
Expected: PASS with the spreadsheets extra installed.

- [ ] **Step 5: Commit**

```bash
git add scripts/adapters tests/fixtures/replay/structured tests/test_structured_adapters.py
git commit -m "feat: normalize structured admission source tables"
```

### Task 4: PDF, OCR-Row, and QR Adapters

**Files:**
- Create: `scripts/adapters/pdf_text.py`
- Create: `scripts/adapters/ocr_rows.py`
- Create: `scripts/adapters/qr.py`
- Create: `tests/fixtures/replay/ocr/rows.json`
- Create: `tests/fixtures/replay/qr/decoded-url.txt`
- Create: `tests/test_unstructured_adapters.py`

**Interfaces:**
- Consumes: local PDF/text, host-produced OCR row JSON, or host-produced QR payload text.
- Produces: normalized rows and validated public URLs. QR decoding itself is host-provided; fetching uses `scripts.downloader`.

- [ ] **Step 1: Write failing safety and uncertainty tests**

```python
class UnstructuredAdapterTest(unittest.TestCase):
    def test_masked_ocr_cell_remains_non_numeric(self):
        rows = normalize_ocr_rows(self.fixture("rows.json"), self.mapping)
        self.assertIsNone(rows[0].values["min_score"])
        self.assertEqual(rows[0].cell_status["min_score"], "masked")

    def test_non_monotonic_rank_sequence_is_rejected(self):
        with self.assertRaises(OcrValidationError):
            normalize_ocr_rows(self.non_monotonic_rows(), self.mapping)

    def test_qr_private_target_is_blocked_before_fetch(self):
        with self.assertRaises(DownloadSecurityError):
            resolve_qr_payload("http://127.0.0.1/table.xlsx", self.workspace)
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_unstructured_adapters -v`  
Expected: ERROR because adapter modules are missing.

- [ ] **Step 3: Implement explicit extraction boundaries**

PDF text extraction preserves page numbers and flags image-only pages. OCR JSON must include page/image ID, bounding box, raw text, normalized value, and confidence per cell. Reject masked patterns, rows outside score scale, and monotonicity violations; do not interpolate masked boundary rows. QR accepts only a decoded text payload, validates it with the secure downloader, records the QR image source ID and redirect chain, and never uploads an image to an external recognition site automatically.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_unstructured_adapters -v`  
Expected: PASS with all network calls mocked.

- [ ] **Step 5: Commit**

```bash
git add scripts/adapters tests/fixtures/replay/ocr tests/fixtures/replay/qr tests/test_unstructured_adapters.py
git commit -m "feat: normalize qr pdf and ocr evidence safely"
```

### Task 5: Source Policy and Retrieval Playbook

**Files:**
- Create: `references/source-policy.md`
- Create: `references/retrieval-playbook.md`
- Create: `tests/test_instruction_contracts.py`

**Interfaces:**
- Consumes: approved design rules.
- Produces: normative Agent-readable instructions for discovery, tiering, independence, conflict handling, current-year freshness, quotations, downloads, QR/OCR, and degradation.

- [ ] **Step 1: Write failing documentation-contract tests**

```python
class InstructionContractTest(unittest.TestCase):
    def test_source_policy_contains_non_negotiable_thresholds(self):
        text = self.read("references/source-policy.md")
        for phrase in ("最多 10", "三个独立", "不得取平均", "引用链", "内容指纹"):
            self.assertIn(phrase, text)

    def test_playbook_requires_preflight_and_evidence_validation(self):
        text = self.read("references/retrieval-playbook.md")
        self.assertLess(text.index("能力预检"), text.index("开始检索"))
        self.assertIn("validate_evidence.py", text)
```

- [ ] **Step 2: Verify missing-reference failures**

Run: `python -m unittest tests.test_instruction_contracts -v`  
Expected: FAIL because both references are absent.

- [ ] **Step 3: Write the normative playbook**

Define A/B/C tiers with examples but classify by publisher and origin, not URL appearance. Describe top-10 candidate enumeration, deduplication before corroboration, three-C-source exact agreement, no averaging conflicts, bounded retry, partial and masked handling, page/sheet/row citation, and a stop condition for every query-plan task. Include full/standard/offline degradation and a clear distinction between official facts, reference facts, inference intervals, conflicts, and missing evidence.

- [ ] **Step 4: Run documentation tests**

Run: `python -m unittest tests.test_instruction_contracts -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add references/source-policy.md references/retrieval-playbook.md tests/test_instruction_contracts.py
git commit -m "docs: define reproducible retrieval and source policy"
```

### Task 6: Host Capability Guides

**Files:**
- Create: `references/hosts/generic.md`
- Create: `references/hosts/codex.md`
- Create: `references/hosts/claude-code.md`
- Create: `references/hosts/kimi.md`
- Modify: `tests/test_instruction_contracts.py`

**Interfaces:**
- Consumes: the normative playbook and host capabilities.
- Produces: tool mappings for search, browser/download, local command execution, vision/OCR, file output, and explicit fallback behavior.

- [ ] **Step 1: Add failing host-parity tests**

```python
def test_every_host_guide_maps_all_capability_classes(self):
    required = ("search", "browse", "vision", "local_exec", "file_output", "offline")
    for path in HOST_GUIDES:
        text = path.read_text("utf-8")
        for capability in required:
            self.assertIn(f"`{capability}`", text, str(path))

def test_host_guides_do_not_override_source_threshold(self):
    for path in HOST_GUIDES:
        self.assertNotIn("降低为两个来源", path.read_text("utf-8"))
```

- [ ] **Step 2: Verify guides are missing**

Run: `python -m unittest tests.test_instruction_contracts -v`  
Expected: FAIL listing missing host guides.

- [ ] **Step 3: Write mappings without host-specific quality drift**

Each guide names likely tool categories, shows how the Agent passes capabilities to `preflight.py`, and sends all extracted facts through the same adapters and evidence validator. If a host lacks vision, it asks the user for a machine-readable file or marks OCR-only facts missing. If it lacks web access, it enters offline mode and never claims real-time verification.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_instruction_contracts -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add references/hosts tests/test_instruction_contracts.py
git commit -m "docs: map retrieval workflow across agent hosts"
```

### Task 7: Rewrite `SKILL.md` as a Six-Stage Orchestrator

**Files:**
- Modify: `SKILL.md`
- Create: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: user conversation and all contracts/references created above.
- Produces: the workflow `intake → preflight → query plan → evidence → deterministic calculation → report`; no embedded province data or host-exclusive tool command.

- [ ] **Step 1: Write failing skill-contract tests**

```python
class SkillContractTest(unittest.TestCase):
    def test_frontmatter_is_minimal_and_valid(self):
        metadata = parse_frontmatter(ROOT / "SKILL.md")
        self.assertEqual(set(metadata), {"name", "description"})
        self.assertEqual(metadata["name"], "shengxue-skill")

    def test_workflow_order_is_enforced(self):
        text = (ROOT / "SKILL.md").read_text("utf-8")
        stages = ["信息采集", "能力预检", "查询计划", "证据归一化", "确定性计算", "报告输出"]
        self.assertEqual([text.index(s) for s in stages], sorted(text.index(s) for s in stages))

    def test_required_references_and_cli_gates_are_linked(self):
        text = (ROOT / "SKILL.md").read_text("utf-8")
        for target in ("references/source-policy.md", "references/retrieval-playbook.md", "scripts/validate_evidence.py"):
            self.assertIn(target, text)

    def test_intake_covers_decision_relevant_fields(self):
        text = (ROOT / "SKILL.md").read_text("utf-8")
        for field in ("省份", "选科", "学校全称", "分数", "年级排名", "意向院校", "意向地区", "意向专业", "港澳", "奖项"):
            self.assertIn(field, text)
```

- [ ] **Step 2: Verify the current skill contradicts the new flow**

Run: `python -m unittest tests.test_skill_contract -v`  
Expected: FAIL because the current file lacks the common preflight/query/evidence gates or contains obsolete assumptions.

- [ ] **Step 3: Rewrite the orchestrator**

Keep `SKILL.md` concise. Specify trigger/intake fields, province-first mode detection, complete/standard/offline preflight, per-task query-plan execution, top-10 candidate handling, evidence validation before any number enters calculation, deterministic CLI invocation, and Markdown/DOCX output semantics. Link detailed material instead of duplicating it. Require an explicit disclosure whenever data is partial, reference-grade, inferred, conflicting, missing, or masked.

- [ ] **Step 4: Run skill-contract and instruction tests**

Run: `python -m unittest tests.test_skill_contract tests.test_instruction_contracts -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add SKILL.md tests/test_skill_contract.py
git commit -m "refactor: orchestrate evidence-first admission research"
```

### Task 8: Deterministic Replay Scenarios

**Files:**
- Create: `tests/fixtures/replay/heilongjiang-qr/`
- Create: `tests/fixtures/replay/shanghai-masked-ocr/`
- Create: `tests/fixtures/replay/joy-report-crosscheck/`
- Create: `tests/test_replay_scenarios.py`

**Interfaces:**
- Consumes: synthetic host discoveries and extraction results.
- Produces: finalized evidence bundles and report models without live browsing.

- [ ] **Step 1: Write failing scenario assertions**

```python
class ReplayScenarioTest(unittest.TestCase):
    def test_qr_spreadsheet_reaches_reference_fact_with_provenance(self):
        result = replay("heilongjiang-qr")
        self.assertEqual(result.fact.status, EvidenceStatus.REFERENCE)
        self.assertIn("sheet", result.fact.notes)

    def test_masked_ocr_never_creates_exact_boundary(self):
        result = replay("shanghai-masked-ocr")
        self.assertEqual(result.fact.status, EvidenceStatus.MASKED)
        self.assertIsNone(result.fact.value)

    def test_top_ten_reposts_collapse_before_three_source_rule(self):
        result = replay("joy-report-crosscheck")
        self.assertEqual(result.independent_source_count, 2)
        self.assertEqual(result.fact.status, EvidenceStatus.MISSING)
        self.assertEqual(result.fact.reason_code, "insufficient_independent_sources")
```

- [ ] **Step 2: Verify replay support is absent**

Run: `python -m unittest tests.test_replay_scenarios -v`  
Expected: FAIL.

- [ ] **Step 3: Add fully synthetic replay bundles**

Model the structural edge cases from prior debugging without copying actual third-party tables, school names, QR URLs, or masked real values. Every fixture includes discovery candidates, deduplication outcome, extraction artifact, normalized facts, capability report, and expected report assertions.

- [ ] **Step 4: Run replay and full orchestration tests**

```bash
python -m unittest tests.test_replay_scenarios -v
python -m unittest tests.test_query_plan tests.test_province_catalog tests.test_structured_adapters tests.test_unstructured_adapters tests.test_instruction_contracts tests.test_skill_contract -v
```

Expected: PASS without network access.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/replay tests/test_replay_scenarios.py
git commit -m "test: replay qr ocr and source-consensus edge cases"
```

### Task 9: Optional Live Smoke Contract

**Files:**
- Create: `scripts/live_smoke.py`
- Create: `tests/test_live_smoke_contract.py`

**Interfaces:**
- Consumes: one official root from the catalog and network availability.
- Produces: a JSON health record for reachability, redirect domain, retrieval timestamp, and content type; it never updates facts or fails the deterministic test suite.

- [ ] **Step 1: Write failing offline contract tests**

Mock a reachable official page, a redirect to an unlisted domain, timeout, and DNS failure. Assert stable result states `healthy`, `redirect_review`, and `unavailable`; no result is promoted into evidence automatically.

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_live_smoke_contract -v`  
Expected: FAIL because the script is absent.

- [ ] **Step 3: Implement a bounded, read-only health check**

Reuse downloader URL safety, set a short timeout, fetch at most 1 MiB, redact query strings in logs, and return status JSON. The later scheduled workflow may open an alert issue, but this script never edits the province catalog or evidence bundle.

- [ ] **Step 4: Run offline tests**

Run: `python -m unittest tests.test_live_smoke_contract -v`  
Expected: PASS with mocked network.

- [ ] **Step 5: Commit**

```bash
git add scripts/live_smoke.py tests/test_live_smoke_contract.py
git commit -m "feat: add bounded official-source health smoke"
```
