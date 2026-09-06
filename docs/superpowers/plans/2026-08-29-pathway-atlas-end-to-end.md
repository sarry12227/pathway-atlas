# PathwayAtlas End-to-End Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing evidence and calculation components into one recoverable planning session that starts with the anonymous questionnaire and ends with evidence-backed representative schools, decisive personalized pathways, and time/value-ranked actions.

**Architecture:** A host-neutral `PlanningSession` owns the workflow state while host Agents execute declared query tasks with their actual search/browse/vision tools. Dynamic province facts remain session-local and cross typed adapters and evidence bridges; versioned public decision rules consume only authenticated snapshots. Markdown and DOCX continue to project one shared report model.

**Tech Stack:** Python 3.10–3.13, standard-library `dataclasses`/`enum`/`json`/`hashlib`, existing `unittest` suite, JSON Schema Draft 2020-12, existing HTML/XLSX/PDF/OCR/QR adapters, `python-docx` optional extra.

**Spec:** `docs/superpowers/specs/2026-08-29-pathway-atlas-end-to-end-design.md`

## Global Constraints

- Do not ship or preinstall dynamic real-world scores, ranks, admission lines, or policy snapshots.
- Search each data type independently in the exact order `Y -> Y-1 -> Y-2 -> Y-3`; never search beyond three prior years.
- A direct A source may produce `official`; two independent traceable B sources may produce `corroborated`; three independent C publishers may produce `reference`; conflicts are never averaged.
- Users never create or understand profile, province, dataset, evidence, or session JSON and never provide a local file path.
- Preserve anonymity, path-neutral errors, immutable authenticated snapshots, atomic exclusive publication, and Python 3.10 syntax.
- The supplied professional planning document is not a repository input. Do not copy its student identity, provider brand, prices, packages, commercial wording, individual school conclusions, or unverified numbers.
- README must begin byte-for-byte with the user-owned text in Task 1. Import the user's remote README change before implementation; do not rewrite that text manually or append content inside its fenced block.
- Do not publish, push, upload, or submit to external catalogs until automated gates and real-Agent acceptance pass and the user separately authorizes publication.

---

## File Map

### New focused modules

- `scripts/planning_session.py` — workflow state machine, session snapshot validation, atomic local persistence, and the single public orchestration CLI.
- `scripts/decision_policy.py` — versioned non-official planning rules, pathway decision reason codes, and province calculation policy projection.
- `scripts/research_snapshot.py` — assemble authenticated score/admission/policy facts into runtime datasets without caller-authored `province.json`.
- `scripts/action_plan.py` — immutable action records, deduplication, dependencies, and deterministic priority ordering.
- `scripts/adapters/rank_bridge.py` — exact adapter/evidence results to `rank_channel:*` and rank anchors.
- `scripts/adapters/pathway_extraction.py` — field-complete pathway extraction projection before the existing evidence-to-domain bridge.
- `schemas/planning-session.schema.json`, `schemas/decision-policy.schema.json` — strict public machine contracts.

### Existing modules with narrow changes

- `scripts/planning_profile.py`, `schemas/planning-profile.schema.json` — v3 structured decision inputs and complete field-use trace.
- `scripts/query_plan.py`, `schemas/query-plan.schema.json` — build discovery tasks from the trusted catalog and confirmed profile without a dynamic province file.
- `scripts/province_registry.py`, `scripts/contracts.py` — separate stable discovery, versioned calculation policy, and session research snapshot.
- `scripts/school_recommend.py`, `scripts/path_recommend.py` — consume complete preference/readiness/constraint inputs and emit reason codes.
- `scripts/generate_report.py`, `scripts/report_model.py`, `scripts/docx_export.py` — run the session result and project ranked actions once.
- `SKILL.md`, `references/retrieval-playbook.md`, `references/hosts/*.md`, `README.md` — thin orchestration instructions; README changes only after the fixed prefix.

### Tests

- `tests/test_readme_contract.py`
- `tests/test_planning_profile.py`
- `tests/test_query_plan.py`
- `tests/test_planning_session.py`
- `tests/test_rank_evidence_bridge.py`
- `tests/test_pathway_evidence_bridge.py`
- `tests/test_personalized_planning.py`
- `tests/test_action_plan.py`
- `tests/test_pathway_atlas_blackbox.py`
- `tests/test_skill_contract.py`
- `tests/test_instruction_contracts.py`
- `tests/test_docx_semantic_parity.py`

---

### Task 1: Lock the User-Owned README Prefix and Baseline the Missing Orchestrator

**Files:**
- Modify: `tests/test_readme_contract.py`
- Preserve/import: `README.md:1-7`

**Interfaces:**
- Consumes: exact user-owned README prefix from the approved spec.
- Produces: a byte-exact README guard that all later tasks must preserve.

- [ ] **Step 1: Reconcile the user's remote README without rewriting it**

Fetch the repository remotes, inspect the remote `main` README, and merge or cherry-pick only the user-owned README change. Before accepting it, assert that the decoded file begins with this exact value:

```python
FIXED_README_PREFIX = """一句话让AI调用此skill：

```bash
请使用你当前环境的 Skill 安装能力，从 GitHub `https://github.com/sarry12227/pathway-atlas` 安装 `pathway-atlas`（多元星途）；如果 GitHub 无法访问，请改用 Gitee 镜像 `https://gitee.com/sarry1/pathway-atlas`。若环境没有专用安装工具，请将仓库克隆或下载到当前 Agent 的 Skills 目录，确认根目录存在 `SKILL.md` 且其中 `name` 为 `pathway-atlas`，然后重新加载并调用它。
```
"""
```

If the remote does not contain this exact prefix, stop and report the mismatch rather than synthesizing a replacement.

- [ ] **Step 2: Write the README contract test**

```python
def test_readme_starts_with_user_owned_install_prompt(self):
    raw = (ROOT / "README.md").read_bytes()
    self.assertTrue(raw.startswith(FIXED_README_PREFIX.encode("utf-8")))
    self.assertEqual(raw.count(FIXED_README_PREFIX.encode("utf-8")), 1)
```

- [ ] **Step 3: Run the README test**

Run: `python -m unittest tests.test_readme_contract.ReadmeContractTest.test_readme_starts_with_user_owned_install_prompt -v`

Expected: PASS only when the imported user-owned prefix is unchanged.

- [ ] **Step 4: Commit the permanent README guard**

```bash
git add README.md tests/test_readme_contract.py
git commit -m "test: lock the user-owned install prompt"
```

---

### Task 2: Replace Free-Text Profile Loss with Typed Decision Inputs and a Complete Trace

**Files:**
- Modify: `scripts/planning_profile.py`
- Modify: `schemas/planning-profile.schema.json`
- Modify: `tests/test_planning_profile.py`
- Create: `tests/test_personalized_planning.py`

**Interfaces:**
- Consumes: existing `PlanningProfile`, `RankObservation`, and v2 payloads.
- Produces: `PreparationAssets`, `PlanningConstraints`, `DecisionPriorities`, `DecisionInputTrace`, `PlanningProfile.create(v3_payload)`, `PlanningProfile.to_decision_trace()`.

- [ ] **Step 1: Write RED tests for typed v3 fields**

```python
def test_v3_profile_preserves_every_planning_dimension(self):
    profile = PlanningProfile.create(v3_payload())
    self.assertEqual(profile.preparation_assets.subject_strengths, ("数学", "物理"))
    self.assertEqual(profile.constraints.risk_preference, "balanced")
    self.assertEqual(profile.constraints.service_commitment, "reject")
    self.assertEqual(profile.priorities.school_vs_major, "major_first")
    self.assertEqual(profile.pathway_preferences["strong_foundation"], "interested")

def test_every_v3_field_has_one_declared_use(self):
    profile = PlanningProfile.create(v3_payload())
    trace = profile.to_decision_trace()
    self.assertEqual({item.field for item in trace}, set(profile.decision_field_names()))
    self.assertEqual(len({item.field for item in trace}), len(trace))
    self.assertTrue(all(item.use in {"decision_input", "display_only", "not_applicable"} for item in trace))
```

Include RED cases for duplicate keys, unknown enum values, unsafe free text, forged `display_only`, mutable nested collections, and a v2 migration that writes explicit `unknown` values instead of guessing.

- [ ] **Step 2: Run the profile RED**

Run: `python -m unittest tests.test_planning_profile tests.test_personalized_planning -v`

Expected: FAIL because v3 nested records and trace methods do not exist.

- [ ] **Step 3: Add frozen typed records**

Implement these exact public shapes in `planning_profile.py`:

```python
@dataclass(frozen=True)
class PreparationAssets:
    subject_strengths: tuple[str, ...]
    awards: tuple[str, ...]
    research_experiences: tuple[str, ...]
    activities: tuple[str, ...]
    english_readiness: str
    interview_readiness: str
    physical_readiness: str

@dataclass(frozen=True)
class PlanningConstraints:
    excluded_regions: tuple[str, ...]
    budget_level: str
    institution_types: tuple[str, ...]
    service_commitment: str
    adjustment_preference: str
    risk_preference: str
    health_constraints: tuple[str, ...]

@dataclass(frozen=True)
class DecisionPriorities:
    school_vs_major: str
    target_schools: tuple[str, ...]
    target_majors: tuple[str, ...]
    target_regions: tuple[str, ...]
    future_plan: str
    concerns: tuple[str, ...]
    desired_outcomes: tuple[str, ...]

@dataclass(frozen=True)
class DecisionInputTrace:
    field: str
    use: str
    consumers: tuple[str, ...]
    reason: str
```

Use finite enum sets and existing public-text/privacy gates. Nested mappings become immutable sorted tuples or mapping proxies; direct construction and `replace()` must not bypass factory validation.

- [ ] **Step 4: Add profile v3 and explicit v2 migration**

Set the new schema version to `3.0`. `load_planning_profile()` accepts v2 only through `_v2_to_v3_payload()`, which copies known values and writes `"unknown"`/empty tuples for fields v2 never captured. Do not change the 20-question conversation count.

- [ ] **Step 5: Implement the complete decision trace**

Define one constant mapping from canonical field path to consumers, for example:

```python
_DECISION_FIELD_USES = {
    "grade": ("decision_input", ("action_plan",), "selects the planning phase"),
    "preparation_assets.awards": ("decision_input", ("pathway_decision",), "measures documented readiness"),
    "priorities.concerns": ("decision_input", ("action_plan", "report"), "orders uncertainty-reduction actions"),
}
```

`decision_field_names()` and `to_decision_trace()` must derive from the same mapping so a newly added field fails tests until classified.

- [ ] **Step 6: Run profile and schema tests**

Run: `python -m unittest tests.test_planning_profile tests.test_personalized_planning -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/planning_profile.py schemas/planning-profile.schema.json tests/test_planning_profile.py tests/test_personalized_planning.py
git commit -m "feat: model complete planning decision inputs"
```

---

### Task 3: Build Query Plans from Trusted Discovery and the Confirmed Profile

**Files:**
- Create: `scripts/decision_policy.py`
- Create: `schemas/decision-policy.schema.json`
- Modify: `scripts/query_plan.py`
- Modify: `schemas/query-plan.schema.json`
- Modify: `scripts/province_registry.py`
- Modify: `scripts/contracts.py`
- Modify: `tests/test_query_plan.py`
- Create: `tests/test_decision_policy.py`

**Interfaces:**
- Consumes: `PlanningProfile`, `ProvinceCatalogSnapshot`, `ProvinceDiscovery`.
- Produces: `DecisionPolicySnapshot.load_default()`, `ResearchContext.create(profile, catalog)`, `build_query_plan(profile, catalog, policy)`.

- [ ] **Step 1: Write the dynamic-province-file RED**

```python
def test_planning_profile_builds_plan_without_province_json(self):
    profile = PlanningProfile.create(v3_payload(province="湖北"))
    plan = build_query_plan(profile, catalog_snapshot(), DecisionPolicySnapshot.load_default())
    self.assertEqual(plan.province, "湖北")
    self.assertEqual(plan.subject_group, "物理+化学+地理")
    self.assertEqual(tuple(sorted({task.year for task in plan.tasks})), (2025, 2026, 2027, 2028))
```

Write additional RED cases showing that `service_commitment="reject"` omits service-bound pathway research, `pathway_preferences["strong_foundation"]="not_interested"` omits that pathway, and an explicitly unknown value creates a bounded discovery task instead of a recommendation.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_query_plan tests.test_decision_policy -v`

Expected: FAIL because `DecisionPolicySnapshot` and the new signature do not exist.

- [ ] **Step 3: Implement versioned decision policy**

Use factory-only immutable records:

```python
@dataclass(frozen=True)
class ScenarioSelectionPolicy:
    tier_caps: Mapping[str, int]
    min_supporting_years_for_medium_confidence: int
    required_year_majority: str

@dataclass(frozen=True)
class DecisionPolicySnapshot:
    schema_version: str
    policy_id: str
    reviewed_at: str
    scenario: ScenarioSelectionPolicy
    pathway_reason_order: tuple[str, ...]
    action_priority_order: tuple[str, ...]

    @classmethod
    def load_default(cls) -> "DecisionPolicySnapshot": ...
```

The JSON Schema requires exact keys, public identifiers, positive caps, a real calendar date, and finite reason-code vocabularies. The policy is labeled as a project planning rule, never an official province policy.

- [ ] **Step 4: Separate discovery context from calculation policy**

Add:

```python
@dataclass(frozen=True)
class ResearchContext:
    province: str
    mode: str
    subject_group: str
    exam_year: int
    authority_name: str
    official_roots: tuple[str, ...]
    requested_pathways: tuple[str, ...]

    @classmethod
    def create(cls, profile: PlanningProfile, catalog: ProvinceCatalogSnapshot) -> "ResearchContext": ...
```

Derive the canonical subject key from catalog mode and validated subjects. Remove the public query-plan dependency on caller-authored `ProvinceConfig`; retain a private compatibility adapter only for existing internal tests until Task 9 migration.

- [ ] **Step 5: Make query tasks profile-relevant**

Always emit core tasks for province policy, score tables, ordinary-batch admission, enrollment plans, and subject requirements. Emit pathway tasks only when the profile marks them interested/unknown or their eligibility is unresolved. Store exclusions as stable decision trace records, not empty query tasks. Preserve exact per-kind `Y..Y-3` coverage and candidate/retry bounds.

- [ ] **Step 6: Run query, schema, and catalog tests**

Run: `python -m unittest tests.test_query_plan tests.test_decision_policy tests.test_province_catalog -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/decision_policy.py schemas/decision-policy.schema.json scripts/query_plan.py schemas/query-plan.schema.json scripts/province_registry.py scripts/contracts.py tests/test_query_plan.py tests/test_decision_policy.py
git commit -m "feat: plan profile-scoped public research"
```

---

### Task 4: Add the Recoverable Planning Session State Machine

**Files:**
- Create: `scripts/planning_session.py`
- Create: `schemas/planning-session.schema.json`
- Modify: `tests/test_planning_session.py`
- Modify: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: `PlanningProfile`, `PreflightReport`, `QueryPlan`, authenticated manifest hashes.
- Produces: `SessionStage`, `PlanningSession.create()`, transition methods, `PlanningSessionStore`, CLI commands `init|confirm|next|ingest|finalize|compute|status`.

- [ ] **Step 1: Write the session transition RED matrix**

```python
def test_session_requires_exact_stage_order(self):
    session = PlanningSession.create(machine_session_id(), profile_digest="profile:v3:abc")
    self.assertEqual(session.stage, SessionStage.INTAKE)
    with self.assertRaises(SessionTransitionError):
        session.with_query_plan("plan:abc")
    confirmed = session.confirm_profile("profile:v3:abc")
    ready = confirmed.with_preflight(preflight_report()).with_query_plan("plan:abc")
    self.assertEqual(ready.stage, SessionStage.QUERY_PLAN_READY)

def test_profile_revision_invalidates_all_downstream_hashes(self):
    complete = completed_session()
    revised = complete.revise_profile("profile:v3:new")
    self.assertEqual(revised.stage, SessionStage.PROFILE_CONFIRMED)
    self.assertIsNone(revised.evidence_manifest_hash)
    self.assertIsNone(revised.report_digest)
```

Also test duplicate commands, unknown JSON keys, malformed machine IDs, arbitrary direct construction, TOCTOU replacement, partial writes, rival destination ownership, path-neutral errors, deterministic serialization, and Python 3.10 imports.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_planning_session -v`

Expected: FAIL/ERROR because the state machine is absent.

- [ ] **Step 3: Implement immutable session transitions**

Define:

```python
class SessionStage(str, Enum):
    INTAKE = "intake"
    PROFILE_CONFIRMED = "profile_confirmed"
    PREFLIGHT_COMPLETE = "preflight_complete"
    QUERY_PLAN_READY = "query_plan_ready"
    RESEARCH_IN_PROGRESS = "research_in_progress"
    EVIDENCE_FINALIZED = "evidence_finalized"
    CALCULATION_COMPLETE = "calculation_complete"
    REPORT_PUBLISHED = "report_published"

@dataclass(frozen=True)
class PlanningSession:
    session_id: str
    revision: int
    stage: SessionStage
    profile_digest: str
    preflight_digest: str | None
    query_plan_digest: str | None
    completed_task_ids: tuple[str, ...]
    unavailable_task_ids: tuple[str, ...]
    evidence_manifest_hash: str | None
    calculation_digest: str | None
    report_digest: str | None
```

All constructors are private/factory-only; transitions return new instances and verify exact dependency hashes.

- [ ] **Step 4: Implement atomic session persistence**

`PlanningSessionStore` writes a private same-directory temporary file, flushes and fsyncs it, then exclusively publishes it. It stores only canonical machine state; questionnaire source text and local paths stay outside the snapshot. Cleanup failures never hide the primary error.

- [ ] **Step 5: Implement the safe CLI surface**

The CLI accepts explicit command arguments internally but emits only JSON with `stage`, `coverage`, `next_actions`, `degradations`, and machine IDs. Exit codes remain `0` success, `2` invalid/session/evidence, `3` missing optional capability. `next` returns typed task payloads; it never executes network operations.

- [ ] **Step 6: Run focused CLI and session tests**

Run: `python -m unittest tests.test_planning_session tests.test_cli_smoke -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/planning_session.py schemas/planning-session.schema.json tests/test_planning_session.py tests/test_cli_smoke.py
git commit -m "feat: orchestrate recoverable planning sessions"
```

---

### Task 5: Build Rank and Province Research Snapshots from Adapter Results

**Files:**
- Create: `scripts/adapters/rank_bridge.py`
- Create: `scripts/research_snapshot.py`
- Modify: `scripts/adapters/__init__.py`
- Modify: `scripts/rank_locator.py`
- Modify: `scripts/validate_data.py`
- Create: `tests/test_rank_evidence_bridge.py`
- Create: `tests/test_research_snapshot.py`

**Interfaces:**
- Consumes: exact `ExtractedTable`/`ExtractedRow`, `QueryTask`, source-policy result, provenance, authenticated evidence facts.
- Produces: `RankEvidenceBridge`, `bridge_rank_evidence()`, `ProvinceResearchSnapshot`, `build_research_snapshot()`.

- [ ] **Step 1: Write RED bridge tests with real adapter objects**

```python
def test_exact_score_table_row_becomes_authenticated_rank_channel(self):
    extracted = extract_html_table(score_table_fixture())
    bridge = bridge_rank_evidence(
        extracted_row=extracted.rows[0],
        task=score_table_task(year=2027),
        evidence_status=EvidenceStatus.OFFICIAL,
        source_ids=("official-score",),
        coverage_status=EvidenceStatus.OFFICIAL,
    )
    self.assertEqual(bridge.fact.field, "rank_channel:score-table-2027")
    self.assertEqual(bridge.fact.value["kind"], "official_score_table")
```

Add RED cases for score/max-score mismatch, wrong subject key, school/class mismatch, masked boundary, stale year beyond Y-3, hand-built dicts, non-exact extraction, and hash mutation.

- [ ] **Step 2: Write RED research-snapshot tests**

```python
def test_snapshot_builds_runtime_dataset_without_province_json(self):
    snapshot = build_research_snapshot(profile(), query_plan(), validated_evidence())
    self.assertEqual(snapshot.config.province, "湖北")
    self.assertEqual(snapshot.score_rows[0].year, 2027)
    self.assertEqual(snapshot.admission_rows[0].school_code, "SYN-A01")
    self.assertEqual(snapshot.policy_id, "decision-policy-v1")
```

Reject mixed province, subject, query-plan digest, policy version, manifest, and directory identity.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_rank_evidence_bridge tests.test_research_snapshot -v`

Expected: FAIL because both modules are absent.

- [ ] **Step 4: Implement the rank bridge**

Use a factory-only `RankEvidenceBridge` parallel to `AdmissionEvidenceBridge`. It must derive IDs and hashes from the exact row/task/provenance; callers cannot set `field`, `method`, or source binding independently. Score tables produce official score-table channels; joint exam and school data produce bounded channels/anchors only with cohort coverage.

- [ ] **Step 5: Implement the research snapshot builder**

`build_research_snapshot(profile, plan, evidence, decision_policy)` validates all facts against the profile digest and query-plan task IDs, then constructs in-memory `ProvinceConfig`/validated rows. No dynamic file is required. Reuse public row validators and `admission_row_hash`; do not add a bypass for facts created outside bridges.

- [ ] **Step 6: Run rank, dataset, and scenario regressions**

Run: `python -m unittest tests.test_rank_evidence_bridge tests.test_research_snapshot tests.test_rank_locator tests.test_scenario_recommendations tests.test_validate_data -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/adapters/rank_bridge.py scripts/research_snapshot.py scripts/adapters/__init__.py scripts/rank_locator.py scripts/validate_data.py tests/test_rank_evidence_bridge.py tests/test_research_snapshot.py
git commit -m "feat: authenticate rank and province research snapshots"
```

---

### Task 6: Convert Extracted Public Policy Material into Strict Pathway Facts

**Files:**
- Create: `scripts/adapters/pathway_extraction.py`
- Modify: `scripts/adapters/pathway_bridge.py`
- Modify: `schemas/pathway-policy.schema.json`
- Modify: `tests/test_pathway_evidence_bridge.py`
- Modify: `tests/test_structured_adapters.py`
- Modify: `tests/test_unstructured_adapters.py`

**Interfaces:**
- Consumes: adapter extraction rows/blocks, matching `QueryTask`, source-policy status, field coverage and provenance.
- Produces: `PathwayPolicyProjection`, `extract_pathway_policy()`, `bridge_pathway_policy_evidence()`; existing `bridge_pathway_policies()` remains evidence-to-domain only.

- [ ] **Step 1: Write a hand-built-policy bypass RED**

```python
def test_policy_fact_requires_authenticated_extraction_projection(self):
    extraction = pathway_html_extraction()
    projected = extract_pathway_policy(
        extraction=extraction,
        task=pathway_task("strong_foundation", 2027),
        field_map=verified_field_map(),
    )
    bridged = bridge_pathway_policy_evidence(
        projected,
        evidence_status=EvidenceStatus.CORROBORATED,
        source_ids=("traceable-b1", "traceable-b2"),
    )
    self.assertEqual(bridged.fact.field, f"pathway_policy:{projected.policy_id}")
    self.assertEqual(bridged.fact.value["projection_hash"], projected.digest)
```

RED matrix: missing eligibility, date, professional option, training, transition, outcome, service, exit, fee, timeline or action; mixed locators; OCR masked field; wrong year/province/mode; direct/replace forgery; three C vs one C; conflicting B values.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_pathway_evidence_bridge tests.test_structured_adapters tests.test_unstructured_adapters -v`

Expected: FAIL because extraction projection and its authenticated bridge are absent.

- [ ] **Step 3: Implement the strict projection**

```python
@dataclass(frozen=True)
class PathwayPolicyProjection:
    policy_id: str
    pathway_type: str
    title: str
    institution: str
    province: str
    subject_mode: str
    target_year: int
    data_year: int
    eligibility_requirements: tuple[str, ...]
    disqualifying_facts: tuple[str, ...]
    professional_options: tuple[str, ...]
    training_arrangements: str | None
    transition_rules: str | None
    outcomes: str | None
    service_employment_obligations: str | None
    penalty_exit_rules: str | None
    fees_and_subsidies: str | None
    timeline: tuple[str, ...]
    preparation_actions: tuple[str, ...]
    field_provenance: tuple[FieldProvenance, ...]
    digest: str
```

Critical-field absence produces a partial projection with nullable value and explicit warnings; it cannot become an exact `PathwayPolicy`. Complete reference/corroborated projections retain their evidence label.

- [ ] **Step 4: Require projection hashes in the existing bridge**

`bridge_pathway_policies()` verifies each evidence fact came from `bridge_pathway_policy_evidence()` by checking projection hash, task context, provenance count and coverage. Delete tests/helpers that directly manufacture production-accepted pathway values.

- [ ] **Step 5: Run pathway and adapter regressions**

Run: `python -m unittest tests.test_pathway_evidence_bridge tests.test_path_recommend_generic tests.test_structured_adapters tests.test_unstructured_adapters -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/adapters/pathway_extraction.py scripts/adapters/pathway_bridge.py schemas/pathway-policy.schema.json tests/test_pathway_evidence_bridge.py tests/test_structured_adapters.py tests/test_unstructured_adapters.py
git commit -m "feat: authenticate extracted pathway policies"
```

---

### Task 7: Make School and Pathway Decisions Depend on the Full Profile

**Files:**
- Modify: `scripts/school_recommend.py`
- Modify: `scripts/path_recommend.py`
- Modify: `scripts/decision_policy.py`
- Modify: `scripts/generate_report.py`
- Modify: `tests/test_personalized_planning.py`
- Modify: `tests/test_school_recommend_generic.py`
- Modify: `tests/test_path_recommend_generic.py`
- Modify: `tests/test_pathway_atlas_blackbox.py`

**Interfaces:**
- Consumes: `PlanningProfile`, `DecisionPolicySnapshot`, authenticated `RankScenario`, `RecommendationResult`, `PathwayPolicy`.
- Produces: `DecisionReason`, personalized school ordering, personalized `PathwayItem` with eight decision dimensions.

- [ ] **Step 1: Write same-rank/different-person RED cases**

```python
def test_same_rank_different_constraints_change_pathway_decision(self):
    accepts_service = run_planning(profile(service_commitment="accept"))
    rejects_service = run_planning(profile(service_commitment="reject"))
    self.assertEqual(accepts_service.pathway("public_teacher").investment_decision, "主攻")
    self.assertEqual(rejects_service.pathway("public_teacher").investment_decision, "不建议")

def test_same_rank_different_region_and_major_change_school_order(self):
    computing_wuhan = run_planning(profile(target_majors=("计算机",), target_regions=("武汉",)))
    medicine_shanghai = run_planning(profile(target_majors=("临床医学",), target_regions=("上海",)))
    self.assertNotEqual(computing_wuhan.recommendation_ids, medicine_shanghai.recommendation_ids)
```

Add cases for awards/readiness, budget, institution type, adjustment, risk, excluded region, future plan, concerns, and unknown values. Require reason-code differences, not only reordered labels.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_personalized_planning tests.test_school_recommend_generic tests.test_path_recommend_generic -v`

Expected: FAIL because current pathway evaluation receives only rank/province/mode/year/free-text eligibility and school ordering ignores several constraints.

- [ ] **Step 3: Add finite reason codes**

```python
@dataclass(frozen=True)
class DecisionReason:
    dimension: str
    code: str
    effect: str
    explanation: str
    source_ids: tuple[str, ...] = ()
```

Allowed dimensions are exactly `eligibility`, `academic_fit`, `interest_fit`, `readiness`, `urgency`, `burden`, `strategic_value`, `evidence_quality`. `effect` is `supports|blocks|uncertain`. Policy order, not arithmetic addition, resolves decisions.

- [ ] **Step 4: Personalize school filtering and ordering**

Exclude forbidden regions and disallowed institution types before ranking. Preserve rank-scenario safety as the primary tier; within a tier order by major match, target school, target region, school/major priority, adjustment preference, evidence quality, then stable identifiers. Risk preference changes tier caps, not historical admission facts.

- [ ] **Step 5: Personalize pathway decisions**

Pass the complete `PlanningProfile` decision projection into `evaluate_pathways()`. Apply hard blocks first, then readiness/urgency/value/burden and evidence rules. Official evidence no longer automatically means `主攻`; historical evidence no longer automatically means `重点准备`. Every output contains all eight dimensions as supporting, blocking or explicitly not applicable.

- [ ] **Step 6: Run blackbox and renderer-model regressions**

Run: `python -m unittest tests.test_personalized_planning tests.test_school_recommend_generic tests.test_path_recommend_generic tests.test_pathway_atlas_blackbox -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/school_recommend.py scripts/path_recommend.py scripts/decision_policy.py scripts/generate_report.py tests/test_personalized_planning.py tests/test_school_recommend_generic.py tests/test_path_recommend_generic.py tests/test_pathway_atlas_blackbox.py
git commit -m "feat: personalize school and pathway decisions"
```

---

### Task 8: Generate Time- and Value-Ranked Action Plans and Shared Reports

**Files:**
- Create: `scripts/action_plan.py`
- Create: `tests/test_action_plan.py`
- Modify: `scripts/report_model.py`
- Modify: `scripts/generate_report.py`
- Modify: `scripts/docx_export.py`
- Modify: `tests/test_docx_semantic_parity.py`
- Modify: `tests/test_pathway_atlas_blackbox.py`

**Interfaces:**
- Consumes: profile decision trace, rank scenario, school recommendations, pathway decisions, evidence coverage.
- Produces: `ActionItem`, `build_action_plan()`, `ReportModel.priority_actions`, `ReportModel.action_timeline`.

- [ ] **Step 1: Write action-order RED tests**

```python
def test_hard_deadline_and_blocker_precede_generic_review(self):
    plan = build_action_plan(profile(), rank_scenario(), recommendations(), pathways(), evidence())
    self.assertEqual(plan[0].reason_code, "deadline_window")
    self.assertTrue(plan[1].blocking)
    self.assertLess(plan.index(by_code(plan, "long_lead_readiness")), plan.index(by_code(plan, "final_official_review")))

def test_actions_are_personalized_and_have_completion_criteria(self):
    plan = build_action_plan(profile(grade="高二", awards=(), concerns=("强基准备",)), ...)
    readiness = by_code(plan, "strong_foundation_readiness_gap")
    self.assertEqual(readiness.phase, "本学期")
    self.assertTrue(readiness.completion_criteria)
    self.assertIn("strong-foundation", readiness.pathway_ids)
```

Test deduplication, dependency cycles, unknown deadlines, same-day stable order, merged source IDs, effort without numeric addition, and profile changes.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_action_plan tests.test_pathway_atlas_blackbox -v`

Expected: FAIL because current actions are fixed strings.

- [ ] **Step 3: Implement immutable actions and sequential ordering**

```python
@dataclass(frozen=True)
class ActionItem:
    action_id: str
    title: str
    completion_criteria: tuple[str, ...]
    phase: str
    deadline: str | None
    urgency: str
    strategic_value: str
    effort: str
    blocking: bool
    depends_on: tuple[str, ...]
    school_ids: tuple[str, ...]
    pathway_ids: tuple[str, ...]
    reason_code: str
    reason: str
    consequence: str
    evidence_status: EvidenceStatus
    source_ids: tuple[str, ...]
```

`build_action_plan()` derives candidate actions from profile gaps, rank/recommendation evidence gaps, and pathway timeline/preparation fields. Deduplicate by canonical action identity and merge targets/provenance. Topologically validate dependencies, then apply deadline, blocker, long-lead value, uncertainty reduction, phase/effort/stable-ID ordering.

- [ ] **Step 4: Replace fixed report actions**

Remove `_canonical_action_items()` string templates. `ReportModel` stores the complete action tuple, validates the deterministic digest, and exposes the first 3–7 as `priority_actions`; the remainder are grouped by phase in `action_timeline`.

- [ ] **Step 5: Update Markdown and DOCX from the same model**

Render sections in this order: conclusion/disclaimer, priority actions, rank scenario, ordinary-batch representatives, pathway matrix, detailed pathway gaps, phased timeline, evidence disclosure. DOCX remains a pure projection and retains atomic output/path-neutral error rules.

- [ ] **Step 6: Run report parity tests**

Run: `python -m unittest tests.test_action_plan tests.test_pathway_atlas_blackbox tests.test_docx_semantic_parity tests.test_generate_report_evidence -v`

Expected: PASS with zero DOCX skips in the documents runtime.

- [ ] **Step 7: Commit**

```bash
git add scripts/action_plan.py scripts/report_model.py scripts/generate_report.py scripts/docx_export.py tests/test_action_plan.py tests/test_pathway_atlas_blackbox.py tests/test_docx_semantic_parity.py
git commit -m "feat: prioritize personalized planning actions"
```

---

### Task 9: Close the Skill, Host, and True End-to-End Contract

**Files:**
- Modify: `SKILL.md`
- Modify: `references/retrieval-playbook.md`
- Modify: `references/hosts/generic.md`
- Modify: `references/hosts/codex.md`
- Modify: `references/hosts/claude-code.md`
- Modify: `references/hosts/kimi.md`
- Modify: `README.md` only after the fixed prefix
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_instruction_contracts.py`
- Modify: `tests/test_pathway_atlas_blackbox.py`
- Create: `tests/test_end_to_end_planning.py`

**Interfaces:**
- Consumes: all public interfaces from Tasks 2–8.
- Produces: one six-stage Skill workflow backed by `planning_session`, a fake-host deterministic blackbox, and a manual real-Agent acceptance script.

- [ ] **Step 1: Write the natural-language-to-report RED**

The fake host implements the same boundary as real Agents:

```python
class FakeHost:
    capabilities = frozenset({"search", "browse", "vision", "local_exec", "file_output"})

    def execute(self, task: QueryTask) -> tuple[HostCandidate, ...]:
        return self.fixture_candidates[task.task_id]

def test_user_answers_reach_report_without_internal_json(self):
    result = run_agent_workflow(
        user_answers=anonymous_twenty_answer_payload(),
        host=FakeHost(official_and_reference_fixtures()),
    )
    self.assertTrue(result.report.priority_actions)
    self.assertTrue(result.report.recommendations)
    self.assertTrue(result.report.pathways)
    self.assertEqual(result.user_visible_internal_paths, ())
    self.assertEqual(result.user_created_files, ())
```

The helper must drive public CLI/API transitions rather than constructing profile, dataset, evidence or pathway facts directly.

- [ ] **Step 2: Add end-to-end scenario REDs**

Cover official current year, historical fallback, two B, three C, school-rank inference, 3+1+2, 3+3, offline partial output, conflicting sources, different profiles with equal rank, and one main/backup/not-recommended pathway set. Assert every displayed numeric/policy field has a source/status/coverage trail.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_end_to_end_planning tests.test_skill_contract tests.test_instruction_contracts -v`

Expected: FAIL until Skill and host guides drive the session state machine and all old direct-file instructions are removed.

- [ ] **Step 4: Rewrite the Skill as a thin state-machine orchestrator**

Keep automatic trigger phrases and the 20-question first-response rule. Replace manual script/file sequencing with: normalize answers internally, call session `init/confirm`, preflight, loop over `next`, execute tasks with declared host tools, `ingest`, finalize, compute, publish. The Skill must forbid asking users for JSON or paths and must require a direct decision plus action list.

- [ ] **Step 5: Update retrieval and host guides**

Guides map only real host capabilities to the shared loop. They do not duplicate thresholds. Add explicit controls: open source pages rather than trust snippets, run exact adapters, record unavailable reasons, never invent `province.json`, and resume from session state after interruption.

- [ ] **Step 6: Update README after the fixed prefix**

Explain the unified session, dynamic current-query model, profile-sensitive decisions, action priorities, and public-preview limitations. Re-run the byte-exact prefix test before and after the edit.

- [ ] **Step 7: Run end-to-end and instruction tests**

Run: `python -m unittest tests.test_end_to_end_planning tests.test_skill_contract tests.test_instruction_contracts tests.test_readme_contract -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add SKILL.md README.md references/retrieval-playbook.md references/hosts tests/test_skill_contract.py tests/test_instruction_contracts.py tests/test_pathway_atlas_blackbox.py tests/test_end_to_end_planning.py
git commit -m "refactor: close the planning skill workflow"
```

---

### Task 10: Verify, Review, and Run Real-Agent Acceptance Before Publication

**Files:**
- Create: `.superpowers/sdd/2026-08-29-pathway-atlas-end-to-end/final-report.md` (ignored, do not commit)
- Modify only if a gate exposes a scoped defect; every fix gets its own RED/GREEN commit.

**Interfaces:**
- Consumes: committed Tasks 1–9.
- Produces: verification evidence and a publication stop/go recommendation; no external publication.

- [ ] **Step 1: Run focused tests in default and real Python 3.10**

Run in both interpreters:

```bash
python -m unittest \
  tests.test_planning_profile \
  tests.test_query_plan \
  tests.test_planning_session \
  tests.test_rank_evidence_bridge \
  tests.test_research_snapshot \
  tests.test_pathway_evidence_bridge \
  tests.test_personalized_planning \
  tests.test_action_plan \
  tests.test_end_to_end_planning \
  tests.test_skill_contract \
  tests.test_instruction_contracts -v
```

Expected: PASS; only documented platform capability skips are allowed.

- [ ] **Step 2: Run all-extras full verification**

Use the bundled documents/spreadsheet/PDF runtime:

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
git diff --check
git status --short
```

Expected: zero failures/errors; zero DOCX skips; only existing platform-specific symlink/dir-fd skips; tracked tree clean apart from explicitly user-owned untracked files.

- [ ] **Step 3: Verify clean installation and deterministic reports**

Create an LF-preserving `git clone --no-local --no-hardlinks`, install `.[all,test]`, run the full suite, then run identical fake-host sessions under two `PYTHONHASHSEED` values. Compare semantic JSON, Markdown bytes and DOCX SHA-256.

Expected: clone status clean; full suite PASS; semantic JSON and both report formats byte-identical.

- [ ] **Step 4: Run privacy and commercial-content gates**

Scan tracked text, fixtures, snapshots and generated reports for the supplied document's student/provider identifiers, local paths, product prices, service-package wording, phone/email/ID/token patterns and private keys.

Expected: zero matches except explicitly redacted mutation literals inside tests.

- [ ] **Step 5: Run two real-Agent acceptance sessions**

From a clean installation, run one generic/Codex-class host and one Kimi/Claude-class host. Give only a natural-language first question and anonymous answers. Record whether each host:

1. auto-triggers without naming the Skill;
2. asks/confirms at most 20 questions;
3. never asks for JSON/path input;
4. searches and opens actual sources;
5. follows Y..Y-3 and source-tier gates;
6. returns representative schools, decisive pathways and 3–7 priority actions;
7. gives different, explainable outputs for a changed profile.

Expected: both sessions pass every item. Any failure blocks publication and becomes a scoped TDD fix.

- [ ] **Step 6: Request independent code review**

Review the fixed implementation range on both Standards and Spec axes. Resolve every Critical/Important and every direct-scope Minor with independent RED/GREEN commits; rerun the affected and full gates after each round.

- [ ] **Step 7: Write the ignored final report**

Record commits, exact commands/counts, skips, clean-clone hash, two Agent transcripts with student details redacted, privacy scan, remaining limitations and publication verdict. Confirm the report is ignored and not staged.

- [ ] **Step 8: Stop before publication**

Do not push, publish a release, update GitHub/Gitee default branches, or submit external Skill directories. Present the completed evidence to the user and request separate publication authorization.

---

## Plan Self-Review Results

- **Spec coverage:** All design sections map to Tasks 1–10: README/privacy (1,10), session (4), retrieval (3,9), bridges (5,6), full profile (2,7), actions/report (8), host orchestration and real-Agent acceptance (9,10).
- **Placeholder scan:** Every step contains exact behavior, commands, expected outcomes and concrete interfaces; no deferred or ambiguous work remains.
- **Type consistency:** `PlanningProfile`, `DecisionPolicySnapshot`, `ResearchContext`, `PlanningSession`, bridge outputs, `DecisionReason`, and `ActionItem` are introduced before their consumers. Existing domain types remain the compatibility boundary until their named migration task.
- **Scope:** The tasks are ordered by dependency and each ends in a reviewable passing commit. Publication remains explicitly out of scope.
