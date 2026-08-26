# PathwayAtlas Brand Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename every current public project surface to “多元星图 · PathwayAtlas”, add the copy-to-AI README entry point and approved brand assets, then publish the same default-branch content to GitHub and Gitee under `pathway-atlas`.

**Architecture:** Treat `pathway-atlas` as the sole current machine identifier and keep the Chinese/English display brand in documentation. Enforce the cutover with one focused brand contract, then migrate docs, runtime metadata, release machinery, and remote repositories in independently testable commits. Historical plans, Git history, recovery paths, and the physical worktree directory remain immutable provenance.

**Tech Stack:** Markdown, SVG/PNG, Python 3.10+, standard-library `unittest`, JSON Schema, GitHub Actions YAML, Git, GitHub and Gitee repository settings.

**Spec:** `.scratch/pathway-atlas-brand-migration/spec.md`

## Global Constraints

- Chinese brand: `多元星图`.
- English brand: `PathwayAtlas`.
- Repository, Skill, and Python distribution identifier: `pathway-atlas`.
- README line 1 is the exact copy-to-AI prompt from the spec.
- Brand colors are `#94070A`, `#14213D`, and `#C9A227`.
- GitHub visibility remains unchanged; Gitee remains public; both default branches remain `main`.
- Do not rewrite `docs/superpowers/plans/`, `.scratch/shengxue-skill-open-source/spec.md`, Git history, private recovery paths, or the physical worktree path.
- Use TDD for every current-interface change and run one new all-extras full suite only after focused gates pass.
- Do not modify unrelated user-owned or ignored files.

---

### Task 1: Lock the brand contract and assets

**Files:**
- Create: `tests/test_brand_identity.py`
- Create: `assets/brand/pathway-atlas-mark.svg`
- Create: `assets/brand/pathway-atlas-horizontal.svg`
- Create: `assets/brand/pathway-atlas-monochrome.svg`
- Create: `assets/brand/pathway-atlas-mark.png`
- Create: `assets/brand/pathway-atlas-horizontal.png`

**Interfaces:**
- Consumes: the approved brand palette and exact README prompt from the spec.
- Produces: `BRAND_SLUG = "pathway-atlas"`, a reusable current-surface scan, valid SVG sources, and RGBA PNG platform assets.

- [ ] **Step 1: Write the failing brand test**

Create `tests/test_brand_identity.py` with exact assertions equivalent to:

```python
PROMPT = (
    "> **复制给 AI：**「请调用 `pathway-atlas`（多元星图）Skill。"
    "先询问我的省份、选科、分数或位次和升学意向；不要索取姓名、电话、身份证、住址或本地文件路径。"
    "请基于可验证的公开来源，先验证证据再计算，为我分析普通批冲稳保及适合的多元升学路径，"
    "并在每项建议旁标注来源、证据状态、覆盖范围和不确定性。」"
)

def test_readme_starts_with_copyable_prompt():
    assert (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0] == PROMPT

def test_brand_assets_are_safe_and_parseable():
    for name in ("pathway-atlas-mark.svg", "pathway-atlas-horizontal.svg", "pathway-atlas-monochrome.svg"):
        root = ElementTree.fromstring((BRAND / name).read_bytes())
        assert root.tag.endswith("svg")
        assert b"<script" not in (BRAND / name).read_bytes().lower()
    for name in ("pathway-atlas-mark.png", "pathway-atlas-horizontal.png"):
        data = (BRAND / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert data[25] == 6
```

Add a `current_public_files()` helper that includes current docs, `SKILL.md`, `pyproject.toml`, schemas, `.github`, `scripts`, and current tests but explicitly excludes the historical paths in Global Constraints. Assert those current surfaces contain no `shengxue-skill` or `shengxue_skill` after the migration.

- [ ] **Step 2: Run the new test and verify RED**

Run: `python -m unittest tests.test_brand_identity -v`

Expected: failures for the current README first line, missing tracked brand assets, and current old-name occurrences.

- [ ] **Step 3: Add the approved vector and raster assets**

Add the five approved files under `assets/brand/`. Keep SVG text exact, use no external resources or scripts, and preserve PNG color type 6 (RGBA).

- [ ] **Step 4: Run only asset-specific tests**

Run: `python -m unittest tests.test_brand_identity.BrandIdentityTest.test_brand_assets_are_safe_and_parseable -v`

Expected: PASS.

- [ ] **Step 5: Commit the asset contract**

```powershell
git add -- assets/brand tests/test_brand_identity.py
git commit -m "test: define PathwayAtlas brand contract"
```

### Task 2: Migrate README, Skill, and community-facing documentation

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `CONTRIBUTING.md`
- Modify: `SECURITY.md`
- Modify: `.github/ISSUE_TEMPLATE/bug.yml`
- Modify: `.github/ISSUE_TEMPLATE/config.yml`
- Modify: `tests/test_readme_contract.py`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_community_files.py`
- Modify: `tests/test_brand_identity.py`

**Interfaces:**
- Consumes: exact prompt and assets from Task 1.
- Produces: the user-facing brand, direct AI invocation, current installation paths, and final GitHub links.

- [ ] **Step 1: Extend focused tests before editing docs**

Assert:

```python
assert readme.splitlines()[0] == PROMPT
assert "assets/brand/pathway-atlas-horizontal.svg" in readme
assert "# 多元星图 · PathwayAtlas" in readme
assert "不只一条升学路，每条路都有据可循。" in readme
assert frontmatter["name"] == "pathway-atlas"
assert "skills/pathway-atlas" in readme
```

Update community URL expectations to:

```python
"https://github.com/sarry12227/pathway-atlas/security/advisories/new"
"https://github.com/sarry12227/pathway-atlas/blob/main/DATA_SOURCES.md"
```

- [ ] **Step 2: Run focused docs tests and verify RED**

Run: `python -m unittest tests.test_brand_identity tests.test_readme_contract tests.test_skill_contract tests.test_community_files -v`

Expected: failures identify the old README title, Skill name, installation paths, and repository links.

- [ ] **Step 3: Implement the current documentation cutover**

Make README line 1 exactly `PROMPT`, then add the relative horizontal SVG and heading. Replace current user-facing `shengxue-skill` with `多元星图`, `PathwayAtlas`, or `pathway-atlas` according to whether the sentence describes the brand, English name, or machine identifier. Preserve evidence thresholds and safety language unchanged.

- [ ] **Step 4: Run focused docs tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit current documentation**

```powershell
git add -- README.md SKILL.md CONTRIBUTING.md SECURITY.md .github/ISSUE_TEMPLATE tests/test_readme_contract.py tests/test_skill_contract.py tests/test_community_files.py tests/test_brand_identity.py
git commit -m "docs: introduce PathwayAtlas brand"
```

### Task 3: Migrate package, runtime, and schema identifiers

**Files:**
- Modify: `pyproject.toml`
- Modify: `scripts/downloader.py`
- Modify: `scripts/docx_export.py`
- Modify: `schemas/capability-report.schema.json`
- Modify: `schemas/evidence-bundle.schema.json`
- Modify: `schemas/pathway-policy.schema.json`
- Modify: `schemas/province.schema.json`
- Modify: `schemas/query-plan.schema.json`
- Modify: `schemas/rank-anchor.schema.json`
- Modify: `tests/test_project_metadata.py`
- Modify: `tests/test_score_to_tiers.py`
- Modify: `tests/test_brand_identity.py`

**Interfaces:**
- Consumes: machine identifier `pathway-atlas`.
- Produces: distribution metadata, install guidance, User-Agent `pathway-atlas-downloader/0.1`, and `pathway-atlas` schema namespaces.

- [ ] **Step 1: Update tests to require new identifiers**

Require:

```python
assert metadata["project"]["name"] == "pathway-atlas"
assert "pip install 'pathway-atlas[documents]'" in docx_source
assert '"pathway-atlas-downloader/0.1"' in downloader_source
assert all("pathway-atlas" in schema["$id"] for schema in schemas)
```

- [ ] **Step 2: Run metadata/schema tests and verify RED**

Run: `python -m unittest tests.test_project_metadata tests.test_brand_identity tests.test_score_to_tiers -v`

Expected: failures name the old distribution, schema IDs, install hint, and module docstring.

- [ ] **Step 3: Replace current runtime identifiers**

Set `[project].name = "pathway-atlas"`; update the DOCX optional-dependency message, downloader User-Agent, schema IDs, and current test description. Do not rename the import package `scripts`, because it is the stable runtime module namespace rather than a brand string.

- [ ] **Step 4: Run focused metadata and validation regression tests**

Run:

```powershell
python -m unittest tests.test_project_metadata tests.test_brand_identity tests.test_downloader tests.test_docx_semantic_parity tests.test_validate_data tests.test_validate_evidence -v
```

Expected: PASS with only existing platform capability skips.

- [ ] **Step 5: Commit package and schema identifiers**

```powershell
git add -- pyproject.toml scripts/downloader.py scripts/docx_export.py schemas tests/test_project_metadata.py tests/test_score_to_tiers.py tests/test_brand_identity.py
git commit -m "refactor: rename runtime identity to pathway-atlas"
```

### Task 4: Migrate deterministic release and compliance machinery

**Files:**
- Modify: `scripts/build_release.py`
- Modify: `scripts/release_check.py`
- Modify: `.github/workflows/release.yml`
- Modify: `docs/release-process.md`
- Modify: `release-policy.json`
- Modify: `tests/test_build_release.py`
- Modify: `tests/test_release_check.py`
- Modify: `tests/test_workflows.py`
- Modify: `tests/test_brand_identity.py`

**Interfaces:**
- Consumes: distribution identifier `pathway-atlas` and current tracked-file policy.
- Produces: `pathway-atlas-<version>.zip`, ZIP root `pathway-atlas/`, matching `SHA256SUMS`, and release checks that reject old current artifacts.

- [ ] **Step 1: Change release tests before production code**

Replace expected names with:

```python
"pathway-atlas-0.1.0.zip"
"pathway-atlas/README.md"
"pathway-atlas/SKILL.md"
"ARCHIVE_PATH": "dist/pathway-atlas-${{ steps.metadata.outputs.version }}.zip"
```

Add a negative assertion that a newly built archive contains no member starting with `shengxue-skill/`.

- [ ] **Step 2: Run release tests and verify RED**

Run: `python -m unittest tests.test_build_release tests.test_release_check tests.test_workflows tests.test_brand_identity -v`

Expected: failures expose the old accepted project name, archive name/root, wheel pattern, workflow path, and release-process commands.

- [ ] **Step 3: Implement deterministic release rename**

Update the exact project-name checks, ZIP member prefix, archive filename, release artifact regexes, generated egg-info name handling, workflow path, and rehearsal commands. Preserve timestamps, ordering, file allowlists, atomic publication, and privacy behavior.

- [ ] **Step 4: Refresh only mechanical policy hashes**

Run the repository’s existing hash calculation for governed changed files and update only corresponding `file_sha256` values in `release-policy.json`; do not loosen allowlists or scanning rules.

- [ ] **Step 5: Verify focused release behavior and two-build determinism**

Run:

```powershell
python -m unittest tests.test_build_release tests.test_release_check tests.test_workflows tests.test_brand_identity -v
python scripts/compliance_scan.py --tracked --policy release-policy.json
```

Build twice into separate empty directories using `scripts/build_release.py`, then assert ZIP bytes and `SHA256SUMS` bytes are identical and the archive root is exactly `pathway-atlas/`.

Expected: PASS; compliance findings `0`; both SHA-256 values match.

- [ ] **Step 6: Commit release machinery**

```powershell
git add -- scripts/build_release.py scripts/release_check.py .github/workflows/release.yml docs/release-process.md release-policy.json tests/test_build_release.py tests/test_release_check.py tests/test_workflows.py tests/test_brand_identity.py
git commit -m "build: publish pathway-atlas artifacts"
```

### Task 5: Run final local gates and prepare the publishing commit range

**Files:**
- Modify only if a focused gate identifies a migration defect in a previously approved file.

**Interfaces:**
- Consumes: Tasks 1–4 commits.
- Produces: a clean, reviewable local commit range ready for remote publication.

- [ ] **Step 1: Scan current surfaces for residual names**

Run `tests.test_brand_identity`, then run `rg` over current surfaces with explicit historical exclusions. Any current hit is a failure; hits only in the four historical categories from Global Constraints are allowed.

- [ ] **Step 2: Run default and Python 3.10 focused suites**

Run the brand/docs/metadata/release tests from Tasks 1–4 under the default interpreter and `C:\Users\hp\.local\bin\python3.10.exe`.

Expected: PASS with only documented platform capability skips.

- [ ] **Step 3: Run one fresh all-extras full suite**

Use the existing all-extras environment and run `python -m unittest discover -s tests -q` exactly once after the final focused change.

Expected: PASS; record run count, skip count, and DOCX loaded/run/skipped count. Do not rerun unless a subsequent code change invalidates the result.

- [ ] **Step 4: Run static and release gates**

Run `py_compile`, package/flat imports, schema parsing, `git diff --check`, tracked compliance, deterministic release build, and `scripts/release_check.py --ci --version 0.1.0` in a clean tracked snapshot.

Expected: all substantive gates PASS; ambient ignored/user-owned files are reported separately and never staged.

- [ ] **Step 5: Review the exact commit range**

Confirm every changed path is named in this plan or is a mechanical `release-policy.json` hash update. Confirm `git status --short` contains no unrelated staged file.

### Task 6: Publish GitHub and Gitee default branches

**Files:**
- No new local product files unless remote URL verification finds a current documented URL missed by Task 2.

**Interfaces:**
- Consumes: the verified local commit range and explicit user authorization to rename both repositories.
- Produces: GitHub `sarry12227/pathway-atlas`, Gitee `sarry1/pathway-atlas`, default `main` content parity, and updated local remotes.

- [ ] **Step 1: Push the verified branch to GitHub**

Push `codex/open-source-v0.1`, open a PR into `main`, and wait for every required CI job. The PR title is `refactor: launch 多元星图 PathwayAtlas` and the body summarizes the clean identifier cutover, first-line prompt, assets, tests, and unchanged visibility.

- [ ] **Step 2: Merge only after required checks pass**

Merge through GitHub without force-push. Confirm GitHub default branch remains `main` and capture the resulting tree hash.

- [ ] **Step 3: Rename and configure GitHub repository**

Rename `sarry12227/shengxue-skill` to `sarry12227/pathway-atlas`; set the bilingual description and topics; preserve visibility and repository security settings. Add the approved horizontal brand image as repository social preview if GitHub accepts it.

- [ ] **Step 4: Synchronize and rename Gitee repository**

Fast-forward the verified local content to Gitee `main`, rename `sarry1/shengxue-skill` to `sarry1/pathway-atlas`, keep it public, and update its description and logo where supported. Do not force-push or rewrite history.

- [ ] **Step 5: Update remotes and verify parity**

Set:

```powershell
git remote set-url origin https://github.com/sarry12227/pathway-atlas.git
git remote set-url gitee https://sarry1@gitee.com/sarry1/pathway-atlas.git
```

Confirm both remote default branches resolve to `main` and their committed trees are equal even if GitHub’s merge wrapper produces a different commit ID.

- [ ] **Step 6: Verify public entry points**

Open both repository home pages without relying on a non-default branch. Confirm the first visible README line is the copy-to-AI prompt, the logo loads, links use the new slug, Gitee is public, and GitHub visibility is unchanged.

- [ ] **Step 7: Report completion**

Provide final GitHub/Gitee URLs, local commit range, CI status, full-suite count, release artifact hash, and any platform limitation. Do not claim a remote setting or visual was updated without directly verifying it.
