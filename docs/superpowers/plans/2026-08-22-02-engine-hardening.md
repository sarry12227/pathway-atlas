# Deterministic Engine Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Hubei-oriented calculation and report scripts into a province-generic, evidence-aware deterministic engine that never invents exact values from missing, masked, partial, or conflicting inputs.

**Architecture:** The engine receives only normalized, accepted evidence and local user inputs. Province resolution, validation, rank estimation, school matching, pathway eligibility, Markdown generation, and DOCX export remain pure or file-bounded modules; they do not browse the web and do not choose sources.

**Tech Stack:** Python 3.10–3.13, standard-library CSV/JSON, optional `python-docx`; `unittest`; JSON Schema contracts from Plan 01.

**Spec:** `.scratch/shengxue-skill-open-source/spec.md`

**Depends on:** `2026-08-22-01-evidence-foundation.md`

## Global Constraints

- Preserve passing legacy behavior only where it does not contradict the approved evidence policy.
- Replace Hubei-specific names with province-neutral names while keeping compatibility aliases for one release when safe.
- Support both `3+1+2` and `3+3`; never validate a subject group against a two-value hard-coded list.
- The field name is `remarks`; readers may accept legacy `remark`, but writers emit only `remarks`.
- The default rank/pathway model contains no unexplained fixed `-4000` adjustment.
- A masked or partial source produces a status and explanation, not a fabricated numeric boundary.
- Missing optional pathway files must not abort the main school report.
- Public fixtures are synthetic; unconfirmed real school joy-report data remains outside Git.
- Use TDD and commit each task separately.

## File Map

- `scripts/province_registry.py`: province metadata discovery and mode-aware subject configuration.
- `scripts/data_loader.py`: normalized table loading with coverage metadata.
- `scripts/validate_data.py`: public, generic dataset validator.
- `scripts/school_recommend.py`: province-neutral school filtering and recommendation.
- `scripts/rank_calc.py`: evidence-based rank interval estimation.
- `scripts/path_recommend.py`: policy-backed eligibility and target-rank calculation.
- `scripts/generate_report.py`: evidence-aware Markdown report assembly.
- `scripts/docx_export.py`: semantic DOCX projection of the same report model.
- `tests/fixtures/provinces/`: synthetic `3+1+2` and `3+3` datasets.

---

### Task 1: Generic Province Registry and Dataset Resolution

**Files:**
- Create: `scripts/province_registry.py`
- Modify: `scripts/data_loader.py`
- Create: `schemas/province.schema.json`
- Create: `tests/fixtures/provinces/demo-312/province.json`
- Create: `tests/fixtures/provinces/demo-33/province.json`
- Create: `tests/test_province_registry.py`

**Interfaces:**
- Consumes: a dataset root containing child directories with `province.json`.
- Produces: `ProvinceConfig`, `discover_provinces(root) -> dict[str, ProvinceConfig]`, `resolve_province_dir(root, province) -> Path`, and `validate_subject_selection(config, primary, secondary) -> None`.

- [ ] **Step 1: Write failing registry tests**

```python
class ProvinceRegistryTest(unittest.TestCase):
    def test_resolves_by_metadata_not_hardcoded_map(self):
        path = resolve_province_dir(self.fixtures, "演示甲省")
        self.assertEqual(path.name, "demo-312")

    def test_33_accepts_configured_subject_group(self):
        config = discover_provinces(self.fixtures)["演示乙市"]
        validate_subject_selection(config, primary="物理", secondary=("化学", "地理"))

    def test_unknown_province_has_actionable_error(self):
        with self.assertRaisesRegex(UnknownProvinceError, "可用省份"):
            resolve_province_dir(self.fixtures, "不存在省")
```

- [ ] **Step 2: Confirm the old resolver cannot pass**

Run: `python -m unittest tests.test_province_registry -v`  
Expected: ERROR because the registry and fixtures are absent.

- [ ] **Step 3: Implement metadata discovery**

`province.json` requires `province`, `mode`, `primary_subjects`, `secondary_subjects`, `score_scale`, and `schema_version`. `mode` accepts `3+1+2` or `3+3`. Reject duplicate province names and path traversal. Change `data_loader.py` to receive an explicit resolved directory; remove direct indexing into `PROVINCE_DIRS`. Keep a deprecated `get_province_dir()` alias only if current callers need the migration bridge.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_province_registry -v`  
Expected: PASS for both reform modes and unknown-province diagnostics.

- [ ] **Step 5: Commit**

```bash
git add scripts/province_registry.py scripts/data_loader.py schemas/province.schema.json tests/fixtures/provinces tests/test_province_registry.py
git commit -m "refactor: resolve province datasets from metadata"
```

### Task 2: Mode-Aware Dataset Validator and Canonical Fields

**Files:**
- Create: `scripts/validate_data.py`
- Modify: `scripts/data_loader.py`
- Create: `tests/test_validate_data.py`

**Interfaces:**
- Consumes: a resolved province directory.
- Produces: `ValidationIssue`, `validate_dataset(path) -> list[ValidationIssue]`; CLI exits 0 when valid and 2 when invalid.

- [ ] **Step 1: Write failing validation tests**

```python
class ValidateDataTest(unittest.TestCase):
    def test_33_subjects_are_read_from_province_config(self):
        self.assertEqual(validate_dataset(self.fixture("demo-33")), [])

    def test_legacy_remark_is_normalized_to_remarks(self):
        row = load_admission_rows(self.csv_with_header("remark"))[0]
        self.assertEqual(row["remarks"], "中外合作")

    def test_uniqueness_includes_school_code_and_remarks(self):
        issues = validate_dataset(self.fixture("duplicate-program"))
        self.assertTrue(any(i.code == "duplicate_admission_key" for i in issues))
```

- [ ] **Step 2: Verify current validation fails the `3+3` case**

Run: `python -m unittest tests.test_validate_data -v`  
Expected: FAIL because validation is Hubei/two-group-specific or the new module is missing.

- [ ] **Step 3: Implement generic validation**

Move reusable validation into the public repository rather than importing the parent repo's `tools`. Validate required headers, integer/range fields, year coverage, subject membership from `ProvinceConfig`, stable school identity, and admission uniqueness using `(year, province, subject_group, school_code, program_group, remarks)`. Readers accept `remark` only as a migration alias and raise when both spellings disagree.

- [ ] **Step 4: Run validator tests and both fixtures**

```bash
python -m unittest tests.test_validate_data -v
python scripts/validate_data.py tests/fixtures/provinces/demo-312
python scripts/validate_data.py tests/fixtures/provinces/demo-33
```

Expected: all exit successfully; invalid duplicate fixture exits 2 in its subprocess test.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_data.py scripts/data_loader.py tests/test_validate_data.py tests/fixtures/provinces
git commit -m "feat: validate province data by declared exam mode"
```

### Task 3: Province-Neutral School Matching and Coverage States

**Files:**
- Modify: `scripts/school_recommend.py`
- Modify: `scripts/contracts.py`
- Create: `tests/test_school_recommend_generic.py`

**Interfaces:**
- Consumes: normalized schools/admissions, target province, subject selection, and evidence coverage.
- Produces: `is_in_province(school_province, target_province) -> bool`, `parse_secondary_subjects(text) -> frozenset[str]`, and `recommend_schools(...) -> RecommendationResult` with `coverage_status` and warnings.

- [ ] **Step 1: Write failing generic tests**

```python
class GenericSchoolRecommendTest(unittest.TestCase):
    def test_in_province_is_not_hubei_specific(self):
        self.assertTrue(is_in_province("上海市", "上海"))
        self.assertFalse(is_in_province("江苏", "上海"))

    def test_masked_line_is_not_an_exact_recommendation(self):
        result = recommend_schools(self.rows(masked=True), self.profile(rank=8000))
        self.assertEqual(result.coverage_status, EvidenceStatus.MASKED)
        self.assertEqual(result.items, ())

    def test_partial_years_are_disclosed(self):
        result = recommend_schools(self.rows(years=(2025,)), self.profile(rank=8000))
        self.assertIn("仅覆盖 2025", result.warnings)

    def test_subject_filter_runs_before_rank_banding(self):
        result = recommend_schools(self.rows(required_secondary={"化学"}), self.profile(secondary={"地理"}, rank=8000))
        self.assertEqual(result.items, ())
        self.assertEqual(result.excluded_by_subject_count, 1)

    def test_true_empty_differs_from_outside_verified_coverage(self):
        verified = recommend_schools(self.rows(coverage=(5000, 12000)), self.profile(rank=8000))
        outside = recommend_schools(self.rows(coverage=(5000, 7000)), self.profile(rank=8000))
        self.assertEqual(verified.empty_reason, "no_match_within_verified_coverage")
        self.assertEqual(outside.empty_reason, "rank_outside_verified_coverage")
```

- [ ] **Step 2: Confirm failures**

Run: `python -m unittest tests.test_school_recommend_generic -v`  
Expected: FAIL because `is_inside_hubei` and plain row lists do not express generic coverage.

- [ ] **Step 3: Implement normalized matching**

Normalize province suffixes without fuzzy substring matching. Parse secondary-subject constraints into sets and apply `any`/`all` rules declared by the row schema before rank banding. Return an immutable result carrying items, excluded-by-subject count, input years, usable years, verified rank coverage, coverage status, empty reason, and human-readable warnings. Distinguish a true empty result inside verified coverage from an input outside that coverage. Do not turn masked values such as `6**` or OCR-uncertain cells into numbers.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_school_recommend_generic -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/school_recommend.py scripts/contracts.py tests/test_school_recommend_generic.py
git commit -m "refactor: make school matching province neutral"
```

### Task 4: Evidence-Based Joy-Report Rank Estimation

**Files:**
- Modify: `scripts/rank_calc.py`
- Create: `schemas/rank-anchor.schema.json`
- Create: `tests/test_rank_evidence.py`

**Interfaces:**
- Consumes: accepted `RankAnchor` records with year, score/rank, school, class scope, source status, and coverage.
- Produces: `estimate_rank_from_anchors(anchors, student_score, student_rank) -> RankEstimate` with interval, method, confidence, usable-anchor count, and reasons.

- [ ] **Step 1: Write failing estimation tests**

```python
class RankEvidenceTest(unittest.TestCase):
    def test_two_consistent_years_produce_interval(self):
        estimate = estimate_rank_from_anchors(self.anchors(years=(2024, 2025)), 610, 4200)
        self.assertLessEqual(estimate.lower_rank, estimate.upper_rank)
        self.assertEqual(estimate.usable_anchor_count, 2)

    def test_one_anchor_is_insufficient_for_precise_estimate(self):
        estimate = estimate_rank_from_anchors(self.anchors(years=(2025,)), 610, 4200)
        self.assertEqual(estimate.status, EvidenceStatus.MISSING)
        self.assertIsNone(estimate.lower_rank)
        self.assertEqual(estimate.reason_code, "insufficient_comparable_anchors")

    def test_conflicting_school_scope_is_not_blended(self):
        estimate = estimate_rank_from_anchors(self.conflicting_scopes(), 610, 4200)
        self.assertEqual(estimate.status, EvidenceStatus.CONFLICT)
```

- [ ] **Step 2: Verify old `xibao.csv` logic fails policy tests**

Run: `python -m unittest tests.test_rank_evidence -v`  
Expected: FAIL because the old function returns a point estimate from insufficient or mixed-scope rows.

- [ ] **Step 3: Implement interval estimation**

Require at least two comparable years or three independently corroborated same-year anchors. Separate whole-school, class, subject-group, and named-program scopes. Calculate an interval from observed anchor deltas using a documented robust rule; expose each contributing anchor. If comparable evidence is insufficient, return `MISSING` with reason code `insufficient_comparable_anchors`; if sources disagree, return `CONFLICT`. Neither case returns a proxy value. No bundled real `xibao.csv` is used as a default.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_rank_evidence -v`  
Expected: PASS, including insufficient and conflict cases.

- [ ] **Step 5: Commit**

```bash
git add scripts/rank_calc.py schemas/rank-anchor.schema.json tests/test_rank_evidence.py
git commit -m "fix: base school rank estimates on comparable evidence"
```

### Task 5: Policy-Backed Pathway Recommendations

**Files:**
- Modify: `scripts/path_recommend.py`
- Create: `schemas/pathway-policy.schema.json`
- Create: `tests/test_path_policy.py`

**Interfaces:**
- Consumes: user profile, accepted pathway policy records, optional documented adjustment model.
- Produces: `evaluate_pathways(profile, policies, model=None) -> PathwayResult`; every recommendation carries eligibility, missing constraints, policy source IDs, and calculation basis.

- [ ] **Step 1: Write failing safety tests**

```python
class PathPolicyTest(unittest.TestCase):
    def test_no_unexplained_default_rank_adjustment(self):
        result = evaluate_pathways(self.profile(rank=1200), self.policies(), model=None)
        self.assertIsNone(result.target_rank)
        self.assertIn("未提供有依据的位次模型", result.warnings)

    def test_target_rank_never_becomes_nonpositive(self):
        result = evaluate_pathways(self.profile(rank=1200), self.policies(), self.model(delta=-4000))
        self.assertGreaterEqual(result.target_rank, 1)

    def test_missing_service_term_is_pending_verification(self):
        item = evaluate_pathways(self.profile(), self.policy_without_service_term()).items[0]
        self.assertEqual(item.status, "pending_verification")
```

- [ ] **Step 2: Verify the fixed-adjustment implementation fails**

Run: `python -m unittest tests.test_path_policy -v`  
Expected: FAIL because the existing default subtracts 4000 and incomplete policies may be recommended.

- [ ] **Step 3: Implement policy-backed evaluation**

Remove the unconditional adjustment. A model is valid only when it declares province, subject mode, cohort years, source IDs, method, and applicability range. Clamp computed ranks to the province score-table domain and disclose the transformation. Policies lacking service/employment obligations, penalty/exit rules, fees, or current-year validity are marked `pending_verification` and excluded from the formal shortlist.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_path_policy -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/path_recommend.py schemas/pathway-policy.schema.json tests/test_path_policy.py
git commit -m "fix: require policy evidence for pathway advice"
```

### Task 6: Evidence-Aware Markdown Report

**Files:**
- Modify: `scripts/generate_report.py`
- Create: `scripts/report_model.py`
- Create: `tests/fixtures/profiles/demo.json`
- Create: `tests/test_generate_report_evidence.py`

**Interfaces:**
- Consumes: `StudentProfile`, `CapabilityReport`, `RecommendationResult`, `RankEstimate`, `PathwayResult`, and `EvidenceManifest`.
- Produces: `build_report_model(...) -> ReportModel` and `render_markdown(model) -> str`.

- [ ] **Step 1: Write failing report semantics tests**

```python
class EvidenceReportTest(unittest.TestCase):
    def test_report_shows_provenance_and_coverage(self):
        text = render_markdown(self.partial_model())
        self.assertIn("证据状态：部分覆盖", text)
        self.assertIn("来源编号", text)
        self.assertIn("仅供参考", text)

    def test_missing_optional_path_data_does_not_abort(self):
        model = build_report_model(pathways=None, **self.required_inputs())
        self.assertIn("多元升学数据不足", render_markdown(model))

    def test_masked_value_is_never_rendered_as_boundary(self):
        self.assertNotIn("最低分：600", render_markdown(self.masked_model(raw="6**")))
```

- [ ] **Step 2: Verify current report path fails**

Run: `python -m unittest tests.test_generate_report_evidence -v`  
Expected: FAIL due to hard-coded province directory or absent evidence/status model.

- [ ] **Step 3: Separate report model from rendering**

Build an immutable semantic model first. Render capability tier, query/data coverage, source IDs, confidence, conflicts, masked cells, missing items, calculation basis, and the existing AI-reference disclaimer. Optional rank/pathway sections degrade to an explicit explanation. Never label an inference as an official cutoff.

- [ ] **Step 4: Run tests and a synthetic CLI report**

```bash
python -m unittest tests.test_generate_report_evidence -v
python scripts/generate_report.py --dataset tests/fixtures/provinces/demo-312 --profile tests/fixtures/profiles/demo.json --evidence tests/fixtures/evidence/three-source-consensus
```

Expected: tests PASS; generated Markdown contains no real student name and references only synthetic source IDs.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_report.py scripts/report_model.py tests/test_generate_report_evidence.py tests/fixtures/profiles
git commit -m "feat: render evidence-aware admission reports"
```

### Task 7: DOCX Semantic Parity Without Skipped Tests

**Files:**
- Modify: `scripts/docx_export.py`
- Create: `tests/test_docx_semantic_parity.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the same `ReportModel` used by Markdown and CLI arguments including repeated `--secondary-subject`.
- Produces: a DOCX whose headings, warnings, evidence table, and recommendation rows match the semantic model.

- [ ] **Step 1: Write failing parity tests**

```python
class DocxSemanticParityTest(unittest.TestCase):
    def test_secondary_subjects_and_evidence_are_present(self):
        path = export_docx(self.model(secondary=("化学", "生物")), self.output)
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("化学、生物", text)
        self.assertIn("证据状态", text)
        self.assertIn("仅供参考", text)
```

- [ ] **Step 2: Install the real document extra and verify failure**

Run:

```bash
python -m pip install -e ".[documents]"
python -m unittest tests.test_docx_semantic_parity -v
```

Expected: FAIL on missing CLI/model fields; the test must not skip for absent `python-docx`.

- [ ] **Step 3: Implement one-model/two-renderer parity**

Make DOCX export receive `ReportModel`; keep parsing only in the CLI wrapper. Add repeated `--secondary-subject`, evidence and coverage tables, masked/conflict warnings, and anonymous default filenames. Remove conditional test skips and fail setup clearly when the declared extra is not installed.

- [ ] **Step 4: Run Markdown and DOCX tests**

Run: `python -m unittest tests.test_generate_report_evidence tests.test_docx_semantic_parity -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docx_export.py tests/test_docx_semantic_parity.py pyproject.toml
git commit -m "feat: keep markdown and docx reports semantically aligned"
```

### Task 8: Archive Private Runtime Coupling and Replace Public Data With Synthetic Fixtures

**Files:**
- Move outside the standalone repository: `scripts/export_from_system.py`
- Move outside the standalone repository: `scripts/parity_check.py`
- Move outside the standalone repository: `scripts/fetch_via_qr.py`
- Move outside the standalone repository: `data/hubei/`, `output/`
- Create archive location: `C:\KIMI\AI\.scratch\shengxue-skill-private-migration\2026-08-22\`
- Create: `tests/test_public_package_boundary.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: repository tree.
- Produces: a standalone public package with no sibling `shengxue-system` dependency, unconfirmed real dataset, generated output, or unsafe legacy fetcher.

- [ ] **Step 1: Write the failing boundary test**

```python
class PublicPackageBoundaryTest(unittest.TestCase):
    def test_private_and_real_runtime_artifacts_are_absent(self):
        for path in (
            ROOT / "scripts/export_from_system.py",
            ROOT / "scripts/parity_check.py",
            ROOT / "scripts/fetch_via_qr.py",
            ROOT / "data/hubei",
            ROOT / "output",
        ):
            self.assertFalse(path.exists(), str(path))

    def test_scripts_do_not_import_sibling_private_system(self):
        for path in (ROOT / "scripts").glob("*.py"):
            self.assertNotIn("shengxue-system", path.read_text("utf-8"))
```

- [ ] **Step 2: Verify the boundary test detects legacy artifacts**

Run: `python -m unittest tests.test_public_package_boundary -v`  
Expected: FAIL and list the three private/unsafe scripts and local real-data directories.

- [ ] **Step 3: Remove only the confirmed public-boundary artifacts**

Resolve and print every source and destination as an absolute path first. Confirm each source is inside `C:\KIMI\AI\shengxue-skill` and each destination is inside `C:\KIMI\AI\.scratch\shengxue-skill-private-migration\2026-08-22`; then move the listed artifacts with native PowerShell `Move-Item -LiteralPath`. This keeps the migration recoverable while removing private coupling and unconfirmed data from the standalone repository. Confirm the safe downloader and synthetic fixtures cover public use cases. Document that rights-reviewed real snapshots may be installed later as a separately licensed optional data package.

- [ ] **Step 4: Run boundary and sensitive-content scans**

```bash
python -m unittest tests.test_public_package_boundary -v
git status --short
git ls-files data output
```

Expected: PASS; the final command prints nothing.

- [ ] **Step 5: Commit**

```bash
git add -A scripts data output README.md tests/test_public_package_boundary.py
git commit -m "chore: remove private and unverified public artifacts"
```

### Task 9: Engine Regression Gate

**Files:**
- Modify: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: both synthetic province modes and every public CLI.
- Produces: one deterministic regression command suitable for CI.

- [ ] **Step 1: Add failing end-to-end CLI assertions**

The smoke test invokes data validation, evidence validation, Markdown generation, and DOCX export for `demo-312`, plus data validation and subject parsing for `demo-33`. Assert return code, UTF-8 output, anonymous filenames, coverage wording, and zero network mocks invoked.

- [ ] **Step 2: Run the focused smoke test**

Run: `python -m unittest tests.test_cli_smoke -v`  
Expected: FAIL until every CLI uses the new shared contracts.

- [ ] **Step 3: Make only compatibility fixes exposed by the smoke test**

Keep CLI exit codes consistent: 0 success, 2 invalid input/evidence, 3 missing optional capability. Do not weaken a policy assertion to retain a legacy output.

- [ ] **Step 4: Run the full engine suite**

Run:

```bash
python -m unittest discover -s tests -v
python scripts/validate_data.py tests/fixtures/provinces/demo-312
python scripts/validate_data.py tests/fixtures/provinces/demo-33
```

Expected: all tests PASS with `python-docx` installed; zero skipped DOCX tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli_smoke.py
git commit -m "test: gate the deterministic admission engine"
```
