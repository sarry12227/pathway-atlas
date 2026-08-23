# Evidence Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone repository foundation, environment preflight, secure retrieval boundary, evidence contracts, source deduplication, confidence policy, and reproducible local evidence bundles.

**Architecture:** Agent hosts discover and interpret sources, but all accepted facts cross a typed evidence boundary before calculation. The foundation is network-tool-agnostic: Python owns safety, provenance, deduplication, confidence decisions, serialization, and deterministic replay.

**Tech Stack:** Python 3.10–3.13 standard library, optional `python-docx`, `openpyxl`, and `pdfplumber`; JSON Schema files; `unittest`; Git.

**Spec:** `.scratch/shengxue-skill-open-source/spec.md`

## Global Constraints

- The public artifact is the independent `shengxue-skill` repository, not the parent planner repository.
- No central service is introduced; cloning or copying the repository remains the installation model.
- Search examines at most the first 10 accessible candidates per task and deduplicates them by publisher, citation chain, and content fingerprint.
- C-tier facts need three independent agreeing publishers before precise calculation.
- Conflict, missing, masked, and partial facts never masquerade as exact official data.
- Python 3.10 is the language floor; tests must pass on Windows, Linux, and macOS.
- Student names, phone numbers, identity numbers, and addresses never enter evidence caches or logs.
- Code is MIT-licensed; fetched or bundled data has a separate rights boundary.
- Use TDD and commit after every task. Do not stage `data/hubei/`, `output/`, caches, reports, databases, credentials, or private-system exports.

## File Map

- `.gitignore`: excludes generated work, credentials, local datasets, reports, and caches.
- `pyproject.toml`: records Python compatibility and optional dependency groups.
- `scripts/contracts.py`: shared enums and dataclasses for capabilities, sources, claims, facts, and manifests.
- `schemas/*.schema.json`: host-neutral serialized contracts.
- `scripts/source_policy.py`: URL/content normalization, independence deduplication, and evidence acceptance.
- `scripts/downloader.py`: bounded public HTTP/HTTPS downloader.
- `scripts/preflight.py`: capability detection and execution-tier selection.
- `scripts/evidence.py`: safe local evidence-bundle lifecycle.
- `scripts/validate_evidence.py`: CLI release gate for an evidence bundle.
- `tests/fixtures/evidence/`: synthetic source and claim replay fixtures.

---

### Task 1: Standalone Repository and Dependency Metadata

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `tests/test_project_metadata.py`
- Commit: `.scratch/shengxue-skill-open-source/spec.md`, `docs/superpowers/plans/*.md`

**Interfaces:**
- Consumes: approved spec and plan documents.
- Produces: an independent Git repository on branch `codex/open-source-v0.1`; optional dependency groups named `documents`, `spreadsheets`, `pdf`, and `test`.

- [ ] **Step 1: Write the failing metadata test**

```python
import pathlib
import unittest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

class ProjectMetadataTest(unittest.TestCase):
    def test_python_floor_and_optional_dependencies(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
        self.assertEqual(data["project"]["requires-python"], ">=3.10")
        extras = data["project"]["optional-dependencies"]
        self.assertIn("python-docx", " ".join(extras["documents"]))
        self.assertIn("openpyxl", " ".join(extras["spreadsheets"]))
        self.assertIn("pdfplumber", " ".join(extras["pdf"]))

    def test_sensitive_runtime_paths_are_ignored(self):
        text = (ROOT / ".gitignore").read_text("utf-8")
        for entry in ("output/", "work/", ".cache/", "data/hubei/", "*.env", "*.pem"):
            self.assertIn(entry, text)
```

- [ ] **Step 2: Run the test and verify the missing-file failure**

Run: `python -m unittest tests.test_project_metadata -v`  
Expected: ERROR because `pyproject.toml` and `.gitignore` do not exist.

- [ ] **Step 3: Create metadata and ignore rules**

Use project name `shengxue-skill`, version `0.1.0`, `requires-python = ">=3.10"`, no mandatory runtime dependencies, and these bounded extras:

```toml
[project.optional-dependencies]
documents = ["python-docx>=1.1,<2"]
spreadsheets = ["openpyxl>=3.1,<4"]
pdf = ["pdfplumber>=0.11,<1"]
test = ["coverage>=7,<8"]
all = ["python-docx>=1.1,<2", "openpyxl>=3.1,<4", "pdfplumber>=0.11,<1"]
```

Add `tomli>=2,<3; python_version < '3.11'` to the `test` extra. Declare a setuptools build backend and package discovery for `scripts*` so `python -m pip install -e ".[all,test]"` works from a clean clone on every supported Python version:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["scripts*"]
```

The ignore file must also cover `__pycache__/`, `.venv/`, `.env*`, keys/certificates, local databases, generated Markdown/DOCX reports, and evidence raw-download directories.

- [ ] **Step 4: Initialize the independent repository and rerun the test**

Run:

```bash
git init -b main
git switch -c codex/open-source-v0.1
python -m pip install -e ".[test]"
python -m unittest tests.test_project_metadata -v
```

Expected: PASS; `git status --short` does not list `data/hubei/` or `output/`.

- [ ] **Step 5: Commit only planning and foundation metadata**

```bash
git add .gitignore pyproject.toml tests/test_project_metadata.py .scratch docs/superpowers/plans
git commit -m "chore: initialize standalone skill repository"
```

### Task 2: Shared Contracts and JSON Schemas

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/contracts.py`
- Create: `schemas/capability-report.schema.json`
- Create: `schemas/evidence-bundle.schema.json`
- Create: `tests/test_contracts.py`

**Interfaces:**
- Consumes: Python 3.10 dataclasses and enums.
- Produces: `SourceTier`, `EvidenceStatus`, `CapabilityTier`, `SourceCandidate`, `FactClaim`, `EvidenceFact`, `CapabilityReport`, and `EvidenceManifest`; every dataclass exposes `to_dict()` with JSON-safe values.

- [ ] **Step 1: Write failing serialization tests**

```python
from scripts.contracts import EvidenceFact, EvidenceStatus, SourceTier

class ContractTest(unittest.TestCase):
    def test_fact_serializes_stable_enum_values(self):
        fact = EvidenceFact(
            fact_id="score-001", field="min_score", value=588, unit="分",
            status=EvidenceStatus.REFERENCE, source_ids=("s1", "s2", "s3"),
            method="three-source-consensus", notes="",
        )
        self.assertEqual(fact.to_dict()["status"], "reference")
        self.assertEqual(fact.to_dict()["source_ids"], ["s1", "s2", "s3"])

    def test_source_tier_values_are_host_neutral(self):
        self.assertEqual(SourceTier.A.value, "A")
```

- [ ] **Step 2: Verify the import fails**

Run: `python -m unittest tests.test_contracts -v`  
Expected: ERROR with `No module named scripts.contracts`.

- [ ] **Step 3: Implement exact contract types**

Use `class SourceTier(str, Enum)` rather than Python 3.11-only `StrEnum`. Define `EvidenceStatus` with exactly `official`, `corroborated`, `reference`, `inferred`, `conflict`, `missing`, `masked`, and `partial`, matching the approved spec. Make all dataclasses frozen. `SourceCandidate` contains `source_id`, `url`, `publisher`, `tier`, `published_at`, `retrieved_at`, `content_hash`, `citation_root`, and `summary`. `FactClaim` contains `field`, `value`, `unit`, `source_id`, and `method`. `EvidenceManifest` contains schema version `1.0`, a random session id, capability tier, candidate/fact filenames, rejected-count, and manifest hash.

The JSON Schemas must require the same serialized names and reject unknown top-level properties.

- [ ] **Step 4: Run contract tests**

Run: `python -m unittest tests.test_contracts -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/contracts.py schemas tests/test_contracts.py
git commit -m "feat: define evidence and capability contracts"
```

### Task 3: Independent-Source Deduplication and Acceptance Policy

**Files:**
- Create: `scripts/source_policy.py`
- Create: `tests/test_source_policy.py`

**Interfaces:**
- Consumes: `list[SourceCandidate]`, `list[FactClaim]`.
- Produces: `canonicalize_url(url: str) -> str`, `content_fingerprint(text: str) -> str`, `deduplicate_candidates(candidates) -> tuple[list[SourceCandidate], dict[str, str]]`, and `evaluate_claims(field, claims, sources) -> EvidenceFact`.

- [ ] **Step 1: Write failing policy tests**

```python
class SourcePolicyTest(unittest.TestCase):
    def test_same_article_on_three_urls_counts_once(self):
        unique, rejected = deduplicate_candidates(self.same_publisher_reposts())
        self.assertEqual(len(unique), 1)
        self.assertEqual(set(rejected.values()), {"same-publisher-or-citation-root"})

    def test_three_independent_c_sources_accept_exact_value(self):
        fact = evaluate_claims("min_score", self.claims(588, 588, 588), self.sources())
        self.assertEqual(fact.status, EvidenceStatus.REFERENCE)
        self.assertEqual(fact.value, 588)

    def test_conflicting_c_sources_never_average(self):
        fact = evaluate_claims("min_score", self.claims(588, 589, 588), self.sources())
        self.assertEqual(fact.status, EvidenceStatus.CONFLICT)
        self.assertIsNone(fact.value)
```

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_source_policy -v`  
Expected: ERROR because the policy module is missing.

- [ ] **Step 3: Implement the policy**

Canonical URLs remove fragments and known tracking parameters while retaining query parameters that identify documents. Independence collapses candidates sharing publisher, citation root, or content hash. Acceptance order is: valid A source → corroborated B sources → three independent agreeing C sources → conflict/missing. Exact numeric conflicts never use mean, median, or first-result preference.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_source_policy -v`  
Expected: PASS, including the repost and conflicting-number cases.

- [ ] **Step 5: Commit**

```bash
git add scripts/source_policy.py tests/test_source_policy.py
git commit -m "feat: enforce independent source consensus"
```

### Task 4: Secure Public Downloader

**Files:**
- Create: `scripts/downloader.py`
- Create: `tests/test_downloader.py`

**Interfaces:**
- Consumes: a public URL, a validated workspace directory, allowed media types, and byte/time limits.
- Produces: `validate_public_url(url: str) -> None` and `download_public_file(url, workspace, *, max_bytes=26_214_400, timeout=60) -> DownloadResult`.

- [ ] **Step 1: Write failing security tests**

```python
class DownloaderSecurityTest(unittest.TestCase):
    def test_blocks_loopback_and_private_networks(self):
        for url in ("http://127.0.0.1/x", "http://[::1]/x", "http://169.254.169.254/x"):
            with self.assertRaises(DownloadSecurityError):
                validate_public_url(url)

    def test_blocks_non_http_schemes(self):
        with self.assertRaises(DownloadSecurityError):
            validate_public_url("file:///etc/passwd")

    def test_aborts_when_stream_exceeds_limit(self):
        with self.assertRaises(DownloadTooLarge):
            download_public_file("https://example.test/a.xlsx", self.tmp, max_bytes=8)
```

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_downloader -v`  
Expected: ERROR because downloader contracts do not exist.

- [ ] **Step 3: Implement bounded downloading**

Resolve every redirect target and DNS result, reject loopback/private/link-local/reserved addresses with `ipaddress`, allow only HTTP/HTTPS, cap redirects at 5, stream in chunks, enforce the byte limit, generate the destination filename internally, and never execute downloaded files. Permit CSV, XLS/XLSX, PDF, HTML, JSON, and common image media types only.

- [ ] **Step 4: Run security tests**

Run: `python -m unittest tests.test_downloader -v`  
Expected: PASS with mocked network responses; no live network is used.

- [ ] **Step 5: Commit**

```bash
git add scripts/downloader.py tests/test_downloader.py
git commit -m "feat: add bounded public-source downloader"
```

### Task 5: Capability Preflight

**Files:**
- Create: `scripts/preflight.py`
- Create: `tests/test_preflight.py`

**Interfaces:**
- Consumes: host capability names `search`, `browse`, `vision`, and optional installed modules.
- Produces: `detect_capabilities(host_capabilities: set[str]) -> CapabilityReport`; CLI prints JSON and exits 0 for all tiers.

- [ ] **Step 1: Write failing tier-selection tests**

```python
class PreflightTest(unittest.TestCase):
    def test_full_requires_search_browse_vision_and_parsers(self):
        report = detect_capabilities({"search", "browse", "vision"}, module_probe=self.all_modules)
        self.assertEqual(report.tier, CapabilityTier.FULL)

    def test_missing_vision_is_standard_not_full(self):
        report = detect_capabilities({"search", "browse"}, module_probe=self.all_modules)
        self.assertEqual(report.tier, CapabilityTier.STANDARD)

    def test_no_network_capability_is_offline(self):
        report = detect_capabilities(set(), module_probe=self.no_modules)
        self.assertEqual(report.tier, CapabilityTier.OFFLINE)
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_preflight -v`  
Expected: ERROR because `scripts.preflight` is missing.

- [ ] **Step 3: Implement deterministic preflight**

Probe Python version and imports for `docx`, `openpyxl`, and `pdfplumber`. Do not claim to auto-detect host search/vision tools; require the Agent to pass them explicitly using repeatable `--host-capability` arguments. Report missing abilities and allowed degradations in JSON.

- [ ] **Step 4: Run unit and CLI tests**

Run:

```bash
python -m unittest tests.test_preflight -v
python scripts/preflight.py --host-capability search --host-capability browse
```

Expected: tests PASS; CLI emits a valid standard/offline/full capability report without traceback.

- [ ] **Step 5: Commit**

```bash
git add scripts/preflight.py tests/test_preflight.py
git commit -m "feat: detect runtime capability tiers"
```

### Task 6: Safe Evidence-Bundle Lifecycle

**Files:**
- Create: `scripts/evidence.py`
- Create: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `CapabilityReport`, candidates, facts, and a caller-selected workspace root.
- Produces: `EvidenceStore.create(root, capability_report)`, `add_candidate`, `add_fact`, `reject_candidate`, and `finalize() -> EvidenceManifest`.

- [ ] **Step 1: Write failing bundle tests**

```python
class EvidenceStoreTest(unittest.TestCase):
    def test_manifest_hash_is_stable_for_same_content(self):
        first = self.build_bundle(order=("s1", "s2"))
        second = self.build_bundle(order=("s2", "s1"))
        self.assertEqual(first.manifest_hash, second.manifest_hash)

    def test_pii_keys_are_rejected(self):
        store = EvidenceStore.create(self.root, self.report)
        with self.assertRaises(EvidencePrivacyError):
            store.add_context({"student_name": "真实姓名"})

    def test_generated_raw_path_cannot_escape_workspace(self):
        store = EvidenceStore.create(self.root, self.report)
        with self.assertRaises(EvidencePathError):
            store.raw_path_for("../../outside")
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_evidence -v`  
Expected: ERROR because the store is missing.

- [ ] **Step 3: Implement atomic, deterministic storage**

Write UTF-8 JSONL for candidates/facts/rejections, sort by stable IDs before final hashing, use atomic replace for the manifest, accept only generated session IDs, and reject keys matching `name`, `phone`, `mobile`, `id_card`, `address`, or Chinese equivalents. `raw_path_for(source_id)` accepts only an existing safe source ID and resolves below `raw/`; normalized facts live below `normalized/`.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_evidence -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/evidence.py tests/test_evidence.py
git commit -m "feat: persist reproducible evidence bundles"
```

### Task 7: Evidence Validation CLI and Replay Fixture

**Files:**
- Create: `scripts/validate_evidence.py`
- Create: `tests/fixtures/evidence/three-source-consensus/`
- Create: `tests/fixtures/evidence/repost-conflict/`
- Create: `tests/test_validate_evidence.py`

**Interfaces:**
- Consumes: a finalized evidence-bundle directory.
- Produces: JSON validation summary and exit code 0 for valid bundles, 2 for schema/policy/privacy failures.

- [ ] **Step 1: Write failing CLI tests**

```python
class ValidateEvidenceCliTest(unittest.TestCase):
    def test_consensus_fixture_passes(self):
        result = self.run_cli("three-source-consensus")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repost_conflict_fixture_fails(self):
        result = self.run_cli("repost-conflict")
        self.assertEqual(result.returncode, 2)
        self.assertIn("independent_sources", result.stdout)
```

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_validate_evidence -v`  
Expected: FAIL because the CLI and fixtures do not exist.

- [ ] **Step 3: Add synthetic bundles and validator**

The passing fixture contains three C-tier publishers with identical `min_score=588`, distinct publishers/citation roots/content hashes, and no PII. The failing fixture contains three URLs that collapse to one publisher plus a conflicting value. The CLI validates JSON structure, manifest hash, privacy keys, evidence status, source count, and source independence.

- [ ] **Step 4: Run the complete foundation suite**

Run:

```bash
python -m unittest tests.test_project_metadata tests.test_contracts tests.test_source_policy tests.test_downloader tests.test_preflight tests.test_evidence tests.test_validate_evidence -v
```

Expected: PASS with no live network calls.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_evidence.py tests/fixtures/evidence tests/test_validate_evidence.py
git commit -m "feat: validate evidence bundles before calculation"
```
