# GitHub Open-Source and v0.1.0 Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package, audit, publish, and document `shengxue-skill` as the independent public repository `sarry12227/shengxue-skill`, then create a reproducible `v0.1.0` public-preview release.

**Architecture:** The repository is self-contained and installable from a clean clone. Documentation states the evidence model and limitations; CI reproduces deterministic tests on three operating systems; a release checker enforces privacy, licensing, data, source, and version gates before any public push or tag. Live-source monitoring is isolated from deterministic correctness.

**Tech Stack:** Git/GitHub, Python 3.10–3.13, `unittest`, GitHub Actions, Markdown community health files, ZIP and SHA-256 release artifacts.

**Spec:** `.scratch/shengxue-skill-open-source/spec.md`

**Depends on:** `2026-08-22-01-evidence-foundation.md`, `2026-08-22-02-engine-hardening.md`, `2026-08-22-03-agent-orchestration.md`

## Approved Public Identity

- Repository: `sarry12227/shengxue-skill`
- Release: `v0.1.0` public preview
- GitHub description: `面向全国新高考省份的开源 AI 升学规划 Skill：实时检索并交叉验证公开数据，通过本地确定性管线生成可追溯的普通批冲稳保与多元升学方案。`
- Suggested topics: `ai-skill`, `gaokao`, `new-gaokao`, `college-admissions`, `education`, `china`, `python`, `evidence-based`, `codex`, `claude-code`
- Code license: MIT
- Data policy: synthetic fixtures under the repository license; external/real data remains subject to its source terms and is not redistributed without confirmed permission.

## Global Constraints

- Publish only the contents and Git history of `C:\KIMI\AI\shengxue-skill`, never the parent `shengxue-ai-planner` repository.
- Do not include real student reports, names, phones, IDs, addresses, credentials, tokens, cookies, `.env` files, private-system exports, sales/pricing copy, or unconfirmed real datasets.
- Do not describe the project as guaranteeing admission, replacing official policy, or being production-ready.
- Do not publish before every deterministic test and release gate passes in a clean environment.
- Do not force-push, rewrite public history, or expose an authenticated browser token in shell output.
- Create the GitHub repository privately for the final remote audit, then change visibility to public only after the release gate passes.
- Use TDD for scripts and workflows; commit each documentation/infrastructure task independently.

## File Map

- `README.md`: product introduction, trust model, installation, workflow, examples, capability tiers, limitations, and disclaimer.
- `CONTRIBUTING.md`: contribution and evidence/data rules.
- `LICENSE`: MIT license for code and synthetic fixtures only.
- `DATA_SOURCES.md`: source tiers, redistribution boundary, takedown/update process.
- `SECURITY.md`: supported version and private reporting guidance.
- `CODE_OF_CONDUCT.md`: Contributor Covenant-compatible conduct rules.
- `CHANGELOG.md`: v0.1.0 changes and preview status.
- `ROADMAP.md`: bounded post-preview work.
- `.github/workflows/`: deterministic CI, scheduled live smoke, and tag release.
- `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md`: contribution intake.
- `scripts/release_check.py`: local/publication release gate.
- `scripts/compliance_scan.py`: reusable content scanner upgraded from price-only matching.

## Approved-Spec Coverage Matrix

| Acceptance criterion | Primary implementation and verification |
| --- | --- |
| AC1 full-tier end-to-end or explicit failure | Plan 03 Tasks 1, 7, 8; Plan 02 Tasks 6, 9 |
| AC2 cross-platform deterministic results | Plan 01 Task 6; Plan 04 Tasks 4, 8 |
| AC3 three independent C sources and conflict exclusion | Plan 01 Tasks 3, 7 |
| AC4 reposts do not increase independence | Plan 01 Task 3; Plan 03 Task 8 |
| AC5 province-configured `3+1+2` and `3+3` | Plan 02 Tasks 1, 2 |
| AC6 masked/partial/OCR data cannot become exact | Plan 02 Tasks 3, 6; Plan 03 Tasks 4, 8 |
| AC7 joy-report interval and stop condition | Plan 02 Task 4 |
| AC8 subject-first filtering and truthful empty states | Plan 02 Task 3 |
| AC9 no unsupported pathway precision | Plan 02 Task 5 |
| AC10 field-level provenance in reports | Plan 01 Tasks 2, 6; Plan 02 Task 6 |
| AC11 DOCX dependencies and semantic parity | Plan 02 Task 7; Plan 04 Task 4 |
| AC12 SSRF, redirects, size, and path safety | Plan 01 Task 4; Plan 03 Task 4 |
| AC13 no PII, sales content, secrets, or unlicensed data | Plan 02 Task 8; Plan 04 Tasks 2, 3 |
| AC14 clean installation and fixed replay | Plan 03 Task 8; Plan 04 Task 8 |
| AC15 complete public GitHub repository and release | Plan 04 Tasks 1–10 |

---

### Task 1: README, Repository Introduction, and User Journey

**Files:**
- Rewrite: `README.md`
- Create: `tests/test_readme_contract.py`

**Interfaces:**
- Consumes: approved architecture and all public CLI names.
- Produces: one accurate landing page for users, contributors, and evaluators.

- [ ] **Step 1: Write failing README-contract tests**

```python
class ReadmeContractTest(unittest.TestCase):
    def test_readme_explains_realtime_and_deterministic_halves(self):
        text = README.read_text("utf-8")
        for phrase in ("实时检索", "交叉验证", "确定性", "证据", "能力预检"):
            self.assertIn(phrase, text)

    def test_readme_discloses_limits_and_data_rights(self):
        text = README.read_text("utf-8")
        for phrase in ("不保证录取", "AI 生成仅供参考", "虚构测试数据", "DATA_SOURCES.md"):
            self.assertIn(phrase, text)

    def test_readme_does_not_claim_zero_network(self):
        self.assertNotIn("零网络依赖", README.read_text("utf-8"))
```

- [ ] **Step 2: Run the test and expose current contradictions**

Run: `python -m unittest tests.test_readme_contract -v`  
Expected: FAIL because the current README describes an offline-only/zero-network product or omits the evidence workflow.

- [ ] **Step 3: Rewrite the README around the approved identity**

Start with this exact short introduction:

> 面向全国新高考省份的开源 AI 升学规划 Skill：实时检索并交叉验证公开数据，通过本地确定性管线生成可追溯的普通批冲稳保与多元升学方案。

Then cover: why the project exists; Agent retrieval versus deterministic local calculation; source tiers and three-independent-publisher rule; full/standard/offline capabilities; supported workflow; Python and optional extras installation; installation as a Skill in generic Agents, Codex, Claude Code, and Kimi; synthetic demo commands; evidence/report example; QR/OCR limitations; privacy-by-default; non-guarantee disclaimer; repository/data licensing boundary; contributing and security links. Label `v0.1.0` as public preview.

- [ ] **Step 4: Verify every documented command in a clean shell**

Run:

```bash
python -m pip install -e ".[all,test]"
python scripts/preflight.py
python scripts/validate_data.py tests/fixtures/provinces/demo-312
python -m unittest tests.test_readme_contract -v
```

Expected: commands succeed and README test passes; offline preflight explicitly reports offline mode rather than failing.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme_contract.py
git commit -m "docs: introduce the evidence-first admission skill"
```

### Task 2: Licensing, Data Rights, Conduct, Security, and Roadmap

**Files:**
- Modify: `LICENSE`
- Rewrite: `CONTRIBUTING.md`
- Create: `DATA_SOURCES.md`
- Create: `SECURITY.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `CHANGELOG.md`
- Create: `ROADMAP.md`
- Create: `tests/test_community_files.py`

**Interfaces:**
- Consumes: MIT code choice and the no-unconfirmed-real-data rule.
- Produces: unambiguous contributor, vulnerability, data, and project-governance boundaries.

- [ ] **Step 1: Write failing community-file tests**

```python
class CommunityFilesTest(unittest.TestCase):
    def test_required_files_exist(self):
        for name in ("LICENSE", "CONTRIBUTING.md", "DATA_SOURCES.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md", "ROADMAP.md"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_data_rights_are_not_implied_by_mit(self):
        text = (ROOT / "DATA_SOURCES.md").read_text("utf-8")
        self.assertIn("MIT 不自动授予第三方数据的再分发权", text)
        self.assertIn("删除请求", text)

    def test_changelog_marks_preview(self):
        self.assertIn("v0.1.0", (ROOT / "CHANGELOG.md").read_text("utf-8"))
        self.assertIn("公开预览", (ROOT / "CHANGELOG.md").read_text("utf-8"))
```

- [ ] **Step 2: Verify missing files fail**

Run: `python -m unittest tests.test_community_files -v`  
Expected: FAIL listing absent files or unclear data terms.

- [ ] **Step 3: Write complete community documents**

Keep MIT copyright attribution accurate for 2026 and the repository owner. `CONTRIBUTING.md` requires synthetic fixtures, source IDs, rights statements for proposed data, TDD, and all release checks. `DATA_SOURCES.md` defines A/B/C sources, URL/fact/hash storage, snapshot redistribution review, correction/takedown handling, and freshness. `SECURITY.md` supports `0.1.x`, asks reporters to use GitHub private vulnerability reporting once enabled, and forbids posting real student data in public issues. Roadmap items must be outcome-based and must not promise unsupported provinces or dates.

- [ ] **Step 4: Run tests and inspect links**

Run: `python -m unittest tests.test_community_files -v`  
Expected: PASS; all relative Markdown links resolve in the later documentation link test.

- [ ] **Step 5: Commit**

```bash
git add LICENSE CONTRIBUTING.md DATA_SOURCES.md SECURITY.md CODE_OF_CONDUCT.md CHANGELOG.md ROADMAP.md tests/test_community_files.py
git commit -m "docs: define community security and data rights"
```

### Task 3: Comprehensive Compliance and Release Checker

**Files:**
- Rewrite: `scripts/compliance_scan.py`
- Create: `scripts/release_check.py`
- Create: `release-policy.json`
- Create: `tests/test_compliance_scan.py`
- Create: `tests/test_release_check.py`

**Interfaces:**
- Consumes: tracked repository files and Git metadata.
- Produces: JSON check results and exit code 0 only when privacy, secrets, data boundary, license, version, source catalog, tests, and repository-scope gates pass.

- [ ] **Step 1: Write failing scanner tests**

```python
class ComplianceScanTest(unittest.TestCase):
    def test_detects_secret_and_student_pii(self):
        findings = scan_text("token=ghp_abcdefghijklmnopqrstuvwxyz123456\n学生姓名：张三\n手机号：13800138000")
        self.assertEqual({f.kind for f in findings}, {"secret", "student_pii", "phone"})

    def test_disclaimer_phrase_is_allowlisted_not_hidden(self):
        self.assertEqual(scan_text("AI 生成仅供参考"), [])

class ReleaseCheckTest(unittest.TestCase):
    def test_rejects_tracked_real_data_directory(self):
        result = check_tracked_paths(["data/hubei/xibao.csv"])
        self.assertFalse(result.ok)
```

- [ ] **Step 2: Verify the price-only scanner fails coverage**

Run: `python -m unittest tests.test_compliance_scan tests.test_release_check -v`  
Expected: FAIL because the existing scanner cannot detect the required categories and release checker is absent.

- [ ] **Step 3: Implement bounded, explainable checks**

Scan tracked text files only, skipping known binary fixtures by extension. Detect common GitHub/API/private-key/token patterns, Chinese phone/ID formats, PII labels, parent private-system references, pricing/sales copy, absolute local attachment paths, and forbidden tracked directories. Use `release-policy.json` for exact synthetic test allowlists and documented false positives. `release_check.py` also checks `pyproject.toml`/tag/version consistency, MIT/data docs, clean Git status, no untracked sensitive files, province catalog schema, Markdown links, full tests, zero skipped DOCX tests, and absence of network calls in deterministic tests. Never print full secret values.

- [ ] **Step 4: Run negative fixtures and the repository scan**

```bash
python -m unittest tests.test_compliance_scan tests.test_release_check -v
python scripts/compliance_scan.py --tracked
python scripts/release_check.py --expected-version 0.1.0
```

Expected: tests PASS; repository check is allowed to report remaining work from later tasks but must identify it precisely without leaking matched secrets.

- [ ] **Step 5: Commit**

```bash
git add scripts/compliance_scan.py scripts/release_check.py release-policy.json tests/test_compliance_scan.py tests/test_release_check.py
git commit -m "feat: enforce privacy and release compliance gates"
```

### Task 4: Cross-Platform Deterministic CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `tests/test_workflows.py`

**Interfaces:**
- Consumes: pushes and pull requests.
- Produces: test results on Ubuntu, Windows, and macOS for Python 3.10 and 3.13; installs document/spreadsheet/PDF dependencies and runs the release checker without live web.

- [ ] **Step 1: Write failing workflow tests**

```python
class WorkflowTest(unittest.TestCase):
    def test_ci_has_three_operating_systems_and_python_edges(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
        for value in ("ubuntu-latest", "windows-latest", "macos-latest", "'3.10'", "'3.13'"):
            self.assertIn(value, text)

    def test_ci_installs_all_extras_and_runs_release_check(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
        self.assertIn(".[all,test]", text)
        self.assertIn("scripts/release_check.py", text)
```

- [ ] **Step 2: Verify workflow absence**

Run: `python -m unittest tests.test_workflows -v`  
Expected: FAIL because CI is missing.

- [ ] **Step 3: Add the CI matrix**

Use official `actions/checkout` and `actions/setup-python`, pip cache keyed by `pyproject.toml`, UTF-8 environment, editable installation with all/test extras, full `unittest` discovery, fixture validators, compliance scan, and release check in a CI mode that does not require a clean worktree. Upload test diagnostics only on failure; never upload evidence raw downloads.

- [ ] **Step 4: Run local contract tests and validate YAML visually**

Run: `python -m unittest tests.test_workflows -v`  
Expected: PASS. After the first remote push, all six OS/Python matrix jobs must pass before merge.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml tests/test_workflows.py
git commit -m "ci: test the skill across supported platforms"
```

### Task 5: Scheduled Source Health Workflow

**Files:**
- Create: `.github/workflows/source-health.yml`
- Create: `.github/ISSUE_TEMPLATE/source-health.yml`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Consumes: weekly schedule or manual dispatch.
- Produces: bounded `live_smoke.py` health artifact and an alert issue on persistent official-root failures; never blocks deterministic CI or updates data automatically.

- [ ] **Step 1: Add failing schedule/permission tests**

Assert `schedule` and `workflow_dispatch` exist, permissions are limited to `contents: read` and `issues: write`, timeout is at most 15 minutes, concurrency prevents duplicate runs, and the workflow calls only `scripts/live_smoke.py` for network health.

- [ ] **Step 2: Verify failure**

Run: `python -m unittest tests.test_workflows -v`  
Expected: FAIL because the scheduled workflow is missing.

- [ ] **Step 3: Add non-authoritative monitoring**

Run weekly, retain the JSON health artifact for 14 days, and create/update one labeled issue only when the script reports `redirect_review` or repeated `unavailable`. State in the issue template that maintainers must manually verify official domains; the workflow must not modify catalog facts, evidence, or release status.

- [ ] **Step 4: Run workflow contract tests**

Run: `python -m unittest tests.test_workflows -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/source-health.yml .github/ISSUE_TEMPLATE/source-health.yml tests/test_workflows.py
git commit -m "ci: monitor official source roots without changing facts"
```

### Task 6: Issue, Pull Request, Funding, and Dependency Hygiene

**Files:**
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/ISSUE_TEMPLATE/bug.yml`
- Create: `.github/ISSUE_TEMPLATE/data-correction.yml`
- Create: `.github/ISSUE_TEMPLATE/source-request.yml`
- Create: `.github/pull_request_template.md`
- Create: `.github/dependabot.yml`
- Modify: `tests/test_community_files.py`

**Interfaces:**
- Consumes: public contributor reports.
- Produces: structured, privacy-safe intake and weekly dependency updates.

- [ ] **Step 1: Add failing template-contract tests**

Assert public issues warn against personal student data; data corrections require province, year, fact, source URL, publisher, and capture date; source requests require rights/redistribution notes; PRs check tests, synthetic fixtures, evidence/source IDs, documentation, and release scan; blank issues are disabled; Dependabot covers `pip` and `github-actions` weekly.

- [ ] **Step 2: Verify missing templates fail**

Run: `python -m unittest tests.test_community_files -v`  
Expected: FAIL.

- [ ] **Step 3: Add templates and dependency configuration**

Use Chinese user-facing labels and descriptions. Do not collect phone, identity number, exact student name, or private report attachments. Point security reports to `SECURITY.md` and deletion/data-rights requests to `DATA_SOURCES.md`.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_community_files -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/ISSUE_TEMPLATE .github/pull_request_template.md .github/dependabot.yml tests/test_community_files.py
git commit -m "chore: add privacy-safe github contribution templates"
```

### Task 7: Tag Release Workflow and Artifact Integrity

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `scripts/build_release.py`
- Create: `tests/test_build_release.py`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Consumes: an annotated tag matching `v<pyproject version>`.
- Produces: `shengxue-skill-<version>.zip`, `SHA256SUMS`, and a GitHub Release; the archive excludes work, caches, real data, Git metadata, and generated reports.

- [ ] **Step 1: Write failing artifact tests**

```python
class BuildReleaseTest(unittest.TestCase):
    def test_archive_contains_runtime_docs_and_synthetic_fixtures(self):
        names = self.build_and_list()
        self.assertIn("shengxue-skill/SKILL.md", names)
        self.assertIn("shengxue-skill/README.md", names)
        self.assertTrue(any(n.startswith("shengxue-skill/tests/fixtures/") for n in names))

    def test_archive_excludes_sensitive_runtime_paths(self):
        names = self.build_and_list()
        for fragment in ("/.git/", "/work/", "/output/", "/data/hubei/", "/.env"):
            self.assertFalse(any(fragment in f"/{n}" for n in names))
```

- [ ] **Step 2: Verify missing builder fails**

Run: `python -m unittest tests.test_build_release -v`  
Expected: ERROR because the builder is absent.

- [ ] **Step 3: Implement deterministic packaging and tag workflow**

Build from `git ls-files`, sort paths, normalize ZIP timestamps/permissions, run the release checker first, and generate SHA-256. The workflow triggers only on `v*` tags, verifies the tag equals `pyproject.toml` version, runs all tests, builds artifacts, and publishes release notes from the matching `CHANGELOG.md` section using GitHub's token with `contents: write`. It does not publish to PyPI in v0.1.0.

- [ ] **Step 4: Run artifact and workflow tests twice**

```bash
python -m unittest tests.test_build_release tests.test_workflows -v
python scripts/build_release.py --version 0.1.0 --output dist
python scripts/build_release.py --version 0.1.0 --output dist-second
```

Expected: PASS; both ZIP SHA-256 values are identical.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml scripts/build_release.py tests/test_build_release.py tests/test_workflows.py
git commit -m "ci: build reproducible github release artifacts"
```

### Task 8: Clean-Clone Release Rehearsal

**Files:**
- Create: `docs/release-process.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: current feature branch at a clean commit.
- Produces: recorded commands and evidence that a clean clone can install, preflight, validate fixtures, generate Markdown/DOCX, run all tests, and build identical release artifacts.

- [ ] **Step 1: Document the exact rehearsal checklist**

The checklist includes Python versions, virtual environment creation, install command, preflight, both province-mode validators, evidence validation, Markdown report, DOCX report, full unit tests, skipped-test count, compliance scan, release check, archive build, SHA comparison, and `git status --short`.

- [ ] **Step 2: Create a separate temporary clone from the local repository**

Run from outside the source working tree using a newly generated temporary directory; do not delete or alter the parent repository. In PowerShell:

```powershell
$releaseRehearsalPath = Join-Path $env:TEMP ("shengxue-skill-release-rehearsal-" + [guid]::NewGuid().ToString("N"))
git clone --no-local C:\KIMI\AI\shengxue-skill $releaseRehearsalPath
python -m venv (Join-Path $releaseRehearsalPath ".venv")
```

Expected: clean clone succeeds and contains only standalone repository history.

- [ ] **Step 3: Run the complete release gate inside the clone**

```powershell
$releasePython = Join-Path $releaseRehearsalPath ".venv\Scripts\python.exe"
& $releasePython -m pip install -e ($releaseRehearsalPath + "[all,test]")
Push-Location $releaseRehearsalPath
& $releasePython -m unittest discover -s tests -v
& $releasePython scripts\release_check.py --expected-version 0.1.0
Pop-Location
```

Expected: PASS, zero unexpected skips, and no live network requirement.

- [ ] **Step 4: Record results without machine-specific secrets or absolute temp paths**

Add a dated v0.1.0 rehearsal entry to `docs/release-process.md` with OS, Python, test count, skip count, release-check result, and artifact hashes. The document records results, not shell history or environment values.

- [ ] **Step 5: Commit**

```bash
git add docs/release-process.md CHANGELOG.md
git commit -m "docs: record the v0.1.0 release rehearsal"
```

### Task 9: Independent GitHub Repository Creation and Feature Push

**Files:**
- Modify only if remote review finds an issue: documentation or release-policy files already listed above.

**Interfaces:**
- Consumes: clean `codex/open-source-v0.1` branch and authenticated GitHub access.
- Produces: private staging repository `sarry12227/shengxue-skill`, correct metadata, a pull request to `main`, and passing remote CI.

- [ ] **Step 1: Verify local repository scope before any remote action**

Run inside `C:\KIMI\AI\shengxue-skill`:

```bash
git rev-parse --show-toplevel
git remote -v
git status --short
git log --oneline --decorate -10
python scripts/release_check.py --expected-version 0.1.0
```

Expected: top level is exactly `C:/KIMI/AI/shengxue-skill`; worktree clean; no parent-repo remote; release gate passes.

- [ ] **Step 2: Obtain authenticated GitHub control safely**

Prefer an already authenticated GitHub browser session. If unavailable, install/authenticate GitHub CLI only after user approval; use browser/device login and never paste a token into a command or plan. Recheck that the signed-in account owns `sarry12227` or has permission to create its repository.

- [ ] **Step 3: Create a private staging repository with approved metadata**

Create `sarry12227/shengxue-skill` without auto-generated README, license, or `.gitignore`. Set the exact approved description and topics. Enable Issues, vulnerability alerts, Dependabot alerts, and private vulnerability reporting. Keep Discussions and Wiki disabled for v0.1.0 unless the user explicitly requests them.

- [ ] **Step 4: Add the exact remote and push without history rewriting**

```bash
git remote add origin https://github.com/sarry12227/shengxue-skill.git
git push -u origin main
git push -u origin codex/open-source-v0.1
```

Expected: both branches push successfully; no force option is used. If `main` does not yet exist locally, create it from the standalone initialization commit before pushing and keep implementation commits on the feature branch.

- [ ] **Step 5: Open the feature pull request and wait for all CI jobs**

Title: `feat: publish shengxue-skill v0.1.0 preview`  
Body: summarize evidence architecture, deterministic engine hardening, cross-Agent support, synthetic-only public data, security/privacy changes, test matrix, and release rehearsal. Link the local spec path as repository-relative `.scratch/shengxue-skill-open-source/spec.md`. Do not merge until all OS/Python jobs pass and the diff contains only the standalone skill.

- [ ] **Step 6: Commit remote-review corrections if required**

Make corrections on `codex/open-source-v0.1`, rerun the relevant local test plus the full release gate, commit with a scoped message, and push normally. Never edit generated GitHub release artifacts by hand.

### Task 10: Final Review, Merge, Public Visibility, and v0.1.0 Release

**Files:**
- Modify: `CHANGELOG.md` only if the final reviewed notes differ from the prepared section.

**Interfaces:**
- Consumes: approved pull request with green CI and a passing release rehearsal.
- Produces: public `main`, annotated `v0.1.0` tag, GitHub Release, verified artifacts, and final repository health check.

- [ ] **Step 1: Request final code review before merge**

Invoke `superpowers:requesting-code-review` against the merge base. Resolve all high/medium findings through tests and commits. Re-run:

```bash
python -m unittest discover -s tests -v
python scripts/release_check.py --expected-version 0.1.0
git diff --check main...codex/open-source-v0.1
```

Expected: PASS; no whitespace errors; reviewer confirms spec and public-boundary compliance.

- [ ] **Step 2: Merge through GitHub and update local main**

Use a normal merge or squash according to the repository setting chosen during review; preserve a readable v0.1.0 history. Pull `main` without rewriting, confirm the merge commit contains all four plan outcomes, and rerun the release checker on `main`.

- [ ] **Step 3: Change repository visibility from private to public**

Immediately verify the public file tree, Git history, Actions logs/artifacts, Issues, and Releases for accidental personal paths, secrets, private data, or parent-repo content. If any exposure is found, stop before tagging and use GitHub's documented sensitive-data remediation process with the user.

- [ ] **Step 4: Create and push an annotated release tag**

```bash
git tag -a v0.1.0 -m "shengxue-skill v0.1.0 public preview"
git push origin v0.1.0
```

Expected: release workflow passes, creates one non-draft GitHub Release, and uploads ZIP plus `SHA256SUMS`.

- [ ] **Step 5: Verify the public release from a fresh download**

Download the published ZIP and checksum through the public release page, verify SHA-256 locally, extract into a new temporary directory, install `[all,test]`, run preflight, validate the demo datasets/evidence, generate Markdown and DOCX, and run the focused smoke tests. Compare the GitHub asset hash with the local rehearsal hash.

- [ ] **Step 6: Configure branch protection and repository presentation**

Protect `main`: require pull requests, require the CI status checks observed on the merged PR, dismiss stale approvals, block force pushes and deletion, and require conversation resolution. Set the repository social preview only if a rights-safe project image is available; otherwise leave it blank. Confirm description, topics, About links, license detection, security policy, and community health files render correctly.

- [ ] **Step 7: Publish final handoff record**

Record the public repository URL, release URL, tag commit, release asset SHA-256, CI run URL, supported Python versions, capability limitations, known preview limitations, and next roadmap milestone. Do not claim production readiness or admissions accuracy guarantees.
