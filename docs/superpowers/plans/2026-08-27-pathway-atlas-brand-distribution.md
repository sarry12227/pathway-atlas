# PathwayAtlas Brand and Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Launch “多元星途 · PathwayAtlas” with the approved progressive logo, a copy-to-AI GitHub/Gitee install prompt, deterministic `pathway-atlas` releases, public default branches, and auditable distribution across active Agent Skill directories.

**Architecture:** GitHub `main` is the sole trusted release source and Gitee `main` is a byte-equivalent public mirror. Every directory listing points back to that source or its deterministic release artifact; no platform-specific code fork is allowed. Local migration is test-first, followed by one fresh all-extras suite, remote publication, then platform-by-platform verification recorded in a public distribution ledger.

**Tech Stack:** Markdown, SVG/PNG, Python 3.10+, standard-library `unittest`, GitHub CLI, Git, Gitee, platform web forms/CLIs, JSON release policy.

**Spec:** `.scratch/pathway-atlas-brand-migration/spec.md`

## Global Constraints

- Display brand is exactly `多元星途 · PathwayAtlas`.
- Repository, Skill, Python distribution, release root, and package identifier remain exactly `pathway-atlas`.
- Slogan is exactly `点亮多种升学路径，走出个性升学星途。`.
- Palette remains `#94070A`, `#14213D`, and `#C9A227` without using Peking University marks or protected typefaces.
- README line 1 is the exact repository-install prompt in the spec and precedes title, logo, badges, and description.
- GitHub becomes public only after local release gates pass; Gitee remains public; both default branches are `main`.
- GitHub is the sole trusted release source; Gitee and directory listings may not diverge from it.
- Never request passwords, verification codes, identity documents, API secrets, or authentication screenshots.
- Preserve the existing uncommitted release-migration changes in `.github/workflows/release.yml`, `docs/release-process.md`, `release-policy.json`, `scripts/build_release.py`, `scripts/release_check.py`, and their tests.
- Do not modify historical plans, Git history, private recovery paths, or unrelated user-owned files.
- Run focused gates after each task and exactly one fresh all-extras full suite after the last local content change.

---

### Task 1: Replace the approved logo with the 多元星途 progression mark

**Files:**
- Modify: `assets/brand/pathway-atlas-mark.svg`
- Modify: `assets/brand/pathway-atlas-horizontal.svg`
- Modify: `assets/brand/pathway-atlas-monochrome.svg`
- Modify: `assets/brand/pathway-atlas-mark.png`
- Modify: `assets/brand/pathway-atlas-horizontal.png`
- Modify: `tests/test_brand_identity.py`

**Interfaces:**
- Consumes: approved colors, slogan, and “multiple origins → evidence nodes → guiding star” visual direction.
- Produces: safe SVG semantic sources and RGBA PNG previews used by README and repository social/brand surfaces.

- [ ] **Step 1: Change the brand asset contract to 多元星途**

Update the test constants to require `多元星途`, reject current-surface `多元星图`, require the three palette colors in the color SVGs, and require semantic SVG element IDs `path-origin`, `evidence-node`, and `guiding-star`.

- [ ] **Step 2: Run the asset contract and verify RED**

Run: `python -m unittest tests.test_brand_identity -v`

Expected: FAIL because the current wordmark still says `多元星图` and the old closed-orbit mark lacks the progression IDs.

- [ ] **Step 3: Generate the revised visual reference**

Inspect the existing mark PNG, then use the built-in image generation tool with the current mark as reference. Preserve the palette and five-path DNA; replace the closed flower/orbit with forward-moving routes, evidence nodes, and a guiding star. Reject generic AI glows, robot imagery, university emblems, and decorative pseudo-text.

- [ ] **Step 4: Rebuild deterministic vector and raster assets**

Encode the approved geometry in the three SVG files with no scripts, external resources, embedded local paths, or personal data. Render the two PNG files from the SVG source with transparent backgrounds; the horizontal asset contains `多元星途`, `PathwayAtlas`, and the exact slogan, while the small mark contains no slogan.

- [ ] **Step 5: Visually and structurally verify assets**

Open both PNG files at original detail, verify legibility at 64 px and 320 px widths, parse every SVG as XML, and run:

```powershell
python -m unittest tests.test_brand_identity -v
```

Expected: PASS.

- [ ] **Step 6: Commit the revised assets**

```powershell
git add -- assets/brand tests/test_brand_identity.py
git commit -m "feat: evolve PathwayAtlas into 多元星途"
```

### Task 2: Put the repository-install prompt and new tone on every public surface

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `CONTRIBUTING.md`
- Modify: `SECURITY.md`
- Modify: `.github/ISSUE_TEMPLATE/bug.yml`
- Modify: `.github/ISSUE_TEMPLATE/config.yml`
- Modify: `tests/test_brand_identity.py`
- Modify: `tests/test_readme_contract.py`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_community_files.py`

**Interfaces:**
- Consumes: Task 1 assets and the exact prompt from the spec.
- Produces: first-screen installation, invocation, privacy, evidence, and fallback behavior for students and parents.

- [ ] **Step 1: Write exact failing documentation assertions**

Require README line 1 to include both repository URLs, `pathway-atlas`, `SKILL.md`, installation/reload language, and the exact privacy/evidence clauses. Require the heading and slogan:

```python
assert "# 多元星途 · PathwayAtlas" in readme
assert "点亮多种升学路径，走出个性升学星途。" in readme
assert "assets/brand/pathway-atlas-horizontal.svg" in readme
```

Add a current-public-surface assertion that `多元星图` is absent outside historical brand migration commits/spec provenance.

- [ ] **Step 2: Run focused documentation tests and verify RED**

Run:

```powershell
python -m unittest tests.test_brand_identity tests.test_readme_contract tests.test_skill_contract tests.test_community_files -v
```

Expected: FAIL on the old Chinese name, slogan, and README prompt.

- [ ] **Step 3: Update README and community surfaces**

Make README line 1 exactly the approved prompt. Follow it with the horizontal logo, bilingual title, slogan, a short parent-facing description, direct GitHub/Gitee clone examples, and `npx skills add sarry12227/pathway-atlas --skill pathway-atlas` where supported. Replace current-facing `多元星图` with `多元星途` without changing evidence thresholds or privacy rules.

- [ ] **Step 4: Run focused documentation tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit the public brand surfaces**

```powershell
git add -- README.md SKILL.md CONTRIBUTING.md SECURITY.md .github/ISSUE_TEMPLATE tests/test_brand_identity.py tests/test_readme_contract.py tests/test_skill_contract.py tests/test_community_files.py
git commit -m "docs: launch 多元星途 PathwayAtlas"
```

### Task 3: Finish deterministic `pathway-atlas` release migration

**Files:**
- Modify: `scripts/build_release.py`
- Modify: `scripts/release_check.py`
- Modify: `.github/workflows/release.yml`
- Modify: `docs/release-process.md`
- Modify: `release-policy.json`
- Modify: `tests/test_build_release.py`
- Modify: `tests/test_release_check.py`
- Modify: `tests/test_workflows.py`
- Modify: `tests/test_project_metadata.py`

**Interfaces:**
- Consumes: current uncommitted release-migration work and `pathway-atlas` distribution metadata already committed in `4e6467e`.
- Produces: `pathway-atlas-0.1.0.zip`, ZIP root `pathway-atlas/`, matching checksums, and strict current-name release gates.

- [ ] **Step 1: Inspect and preserve the existing working-tree diff**

Run:

```powershell
git diff -- .github/workflows/release.yml docs/release-process.md release-policy.json scripts/build_release.py scripts/release_check.py tests/test_build_release.py tests/test_project_metadata.py tests/test_release_check.py tests/test_workflows.py
```

Confirm the diff only changes current artifact names, archive roots, policy hashes/paths, and their tests.

- [ ] **Step 2: Run focused release tests and record remaining RED**

Run:

```powershell
python -m unittest tests.test_build_release tests.test_release_check tests.test_workflows tests.test_project_metadata tests.test_brand_identity -v
```

Expected: any remaining failure identifies a stale old artifact name or policy manifest mismatch, not an unrelated behavior change.

- [ ] **Step 3: Complete the smallest release fixes**

Require exact archive name `pathway-atlas-0.1.0.zip`, exact ZIP prefix `pathway-atlas/`, workflow artifact `dist/pathway-atlas-${{ steps.metadata.outputs.version }}.zip`, and generated metadata directory `pathway_atlas.egg-info`. Preserve ordering, timestamps, atomic publication, and sensitive-file scanning.

- [ ] **Step 4: Refresh only mechanical release-policy entries**

Update governed `file_sha256` values and declare the two approved PNG assets as binary release files. Do not loosen path allowlists, PII rules, price rules, or binary-content checks.

- [ ] **Step 5: Verify release tests and double-build determinism**

Run the focused command from Step 2, tracked compliance, and two builds into separate empty temporary directories. Assert ZIP bytes and `SHA256SUMS` bytes match exactly and all ZIP members start with `pathway-atlas/`.

- [ ] **Step 6: Commit release machinery**

```powershell
git add -- scripts/build_release.py scripts/release_check.py .github/workflows/release.yml docs/release-process.md release-policy.json tests/test_build_release.py tests/test_release_check.py tests/test_workflows.py tests/test_project_metadata.py
git commit -m "build: publish pathway-atlas artifacts"
```

### Task 4: Add an auditable distribution ledger

**Files:**
- Create: `DISTRIBUTION.md`
- Create: `tests/test_distribution_contract.py`
- Modify: `README.md`
- Modify: `release-policy.json`

**Interfaces:**
- Consumes: canonical GitHub/Gitee URLs and the platform matrix from the spec.
- Produces: a public, reviewable record using only `pending`, `submitted`, `indexed`, `rejected`, or `unavailable`.

- [ ] **Step 1: Write a failing distribution contract**

Parse the Markdown table and require one unique row for GitHub, Gitee, SkillsMP, skills.sh, skills.homes, skillhub.club, SkillHub.cn, SkillsCat, and ClawHub. Require columns `Platform`, `Official URL`, `Method`, `Version/Commit`, `Status`, `Listing URL`, `Last verified`, and `Notes`. Reject unknown statuses, non-HTTPS URLs, local paths, PII, secrets, or an `indexed` row without a public listing URL and ISO date.

- [ ] **Step 2: Run the contract and verify RED**

Run: `python -m unittest tests.test_distribution_contract -v`

Expected: FAIL because `DISTRIBUTION.md` does not exist.

- [ ] **Step 3: Add the ledger with honest initial states**

Create all required rows. GitHub and Gitee remain `pending` until default-branch verification; third-party platforms remain `pending` until direct evidence exists. Link README to the ledger without claiming completion.

- [ ] **Step 4: Run distribution and compliance tests**

Run:

```powershell
python -m unittest tests.test_distribution_contract tests.test_brand_identity tests.test_readme_contract -v
python scripts/compliance_scan.py --tracked --policy release-policy.json
```

Expected: PASS and zero findings after mechanical policy hash updates.

- [ ] **Step 5: Commit the distribution ledger**

```powershell
git add -- DISTRIBUTION.md README.md tests/test_distribution_contract.py release-policy.json
git commit -m "docs: track PathwayAtlas distribution"
```

### Task 5: Run final local gates once

**Files:**
- Modify only an approved file if a focused gate exposes a migration defect.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: one verified local commit range ready for public publication.

- [ ] **Step 1: Run current-surface residual scans**

Run brand and distribution contracts, then `rg` for `多元星图`, `shengxue-skill`, and `shengxue_skill` with explicit historical exclusions. Any current interface hit fails.

- [ ] **Step 2: Run focused suites under default Python and Python 3.10**

Run brand, README, Skill, community, metadata, build-release, release-check, workflows, and distribution tests under the current interpreter and `C:\Users\hp\.local\bin\python3.10.exe`.

- [ ] **Step 3: Run one fresh all-extras suite**

Run `python -m unittest discover -s tests -q` once in the existing all-extras environment after the final local content change. Record run count, skip count, and DOCX loaded/run/skipped count. Do not rerun unless a subsequent code change invalidates this result.

- [ ] **Step 4: Run static, compliance, and release gates**

Run `py_compile`, package/flat imports, JSON schema parsing, `git diff --check`, tracked compliance, deterministic double build, and `scripts/release_check.py --ci --version 0.1.0` in a clean tracked snapshot.

- [ ] **Step 5: Audit scope and worktree ownership**

Confirm every changed path belongs to this plan or is a mechanical policy hash update. Preserve unrelated untracked/user-owned files.

### Task 6: Publish GitHub and Gitee default branches

**Files:**
- Update remote URLs only; do not create divergent product files.

**Interfaces:**
- Consumes: Task 5 verified commit range and the user's explicit authorization to make GitHub public.
- Produces: public `sarry12227/pathway-atlas` and `sarry1/pathway-atlas`, both defaulting to `main` with equal trees.

- [ ] **Step 1: Push the verified branch and open the GitHub PR**

Push `codex/open-source-v0.1`, open a PR titled `refactor: launch 多元星途 PathwayAtlas`, and include the logo, prompt, identifier cutover, test evidence, and distribution plan. Do not force-push.

- [ ] **Step 2: Merge after required checks pass**

Merge into `main`, verify the default branch, then rename the repository to `pathway-atlas` and make it public without weakening other security settings.

- [ ] **Step 3: Update GitHub metadata**

Set the bilingual description, topics, homepage information, and social preview derived from the approved horizontal logo. Verify the anonymous repository homepage shows README line 1 and the logo from `main`.

- [ ] **Step 4: Synchronize Gitee**

Fast-forward the same verified tree to Gitee `main`, rename the repository to `pathway-atlas`, keep it public, and update its description/logo. Never force-push or rewrite history.

- [ ] **Step 5: Update remotes and verify parity**

Set the local remote URLs to the new GitHub/Gitee URLs. Compare committed tree hashes, not only commit IDs, and run temporary-directory installation probes from both sources.

- [ ] **Step 6: Update and commit verified ledger states**

Change GitHub/Gitee rows from `pending` to `indexed` only after direct public verification, record version/commit and date, rerun the distribution contract, and commit.

### Task 7: Distribute to active Skill directories

**Files:**
- Modify: `DISTRIBUTION.md`
- Modify: `README.md` only for verified listing badges/links
- Modify: `release-policy.json` only for mechanical hashes

**Interfaces:**
- Consumes: public canonical GitHub release, Gitee mirror, and Task 6 verified ledger.
- Produces: truthful listings and installation evidence without divergent platform copies.

- [ ] **Step 1: Trigger no-login discovery surfaces**

Install with `npx skills add sarry12227/pathway-atlas --skill pathway-atlas`, query skills.sh/SkillsMP/skills.homes/skillhub.club for the canonical repository, and use only documented official submission/contact routes when automatic indexing has not occurred.

- [ ] **Step 2: Submit to SkillsCat and other no-credential official endpoints**

Submit the GitHub repository URL through each official web/CLI interface, capture the returned listing or submission status, and leave the ledger as `submitted` until a public page is directly verified.

- [ ] **Step 3: Hand off interactive authentication one platform at a time**

For SkillHub.cn, skillhub.club, and ClawHub, stop at the login/real-name/terms boundary and tell the user exactly which platform action is needed. After the user completes it, continue with GitHub import or the canonical release artifact. Never receive credentials in chat.

- [ ] **Step 4: Verify every platform result**

For each platform, open the public detail page or official API, confirm name, description, source, license, version/commit, and install command. Install into a fresh temporary Skills directory where supported and compare the installed `SKILL.md`/content hash with the canonical release.

- [ ] **Step 5: Update the ledger and README badges**

Set `indexed`, `submitted`, `rejected`, or `unavailable` based only on direct evidence. Add README badges/links only for `indexed` platforms; do not present pending moderation as publication.

- [ ] **Step 6: Run final focused checks and commit status**

Run distribution, README, brand, links, compliance, and diff checks. Commit only the ledger, verified README links, and mechanical policy hashes with message `docs: publish PathwayAtlas across skill directories`.

- [ ] **Step 7: Report completion and remaining moderation**

Return the GitHub/Gitee URLs, release hash, each directory URL/status, login actions completed by the user, and any pending moderation or unavailable platform. Never summarize `submitted` as “indexed everywhere.”
