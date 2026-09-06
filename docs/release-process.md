# v0.1.0 发布排练

本页记录 `v0.1.0` 公开预览版的本地发布排练。排练必须针对一个已提交、不可变的 Git commit，在关闭自动换行转换的真实 clean clone 中完成。所有报告产物放在 clone 外的临时目录；builder 产物只写入 clone 内 policy 已声明的 `dist/`，比较后删除。记录中不得包含临时绝对路径、环境变量值、凭证或个人信息。

## 完整检查清单

### 1. 固定源快照并创建 LF clean clone

- [ ] 源分支没有 tracked 修改，记录待排练 commit。
- [ ] 用 `--no-local --no-hardlinks` 创建真实 clone，不复用源 worktree 的 index、venv 或 ignored 文件。
- [ ] clone 与后续 checkout 均使用 `core.autocrlf=false`，并验证 tracked 文本保持 LF。
- [ ] detached HEAD 与待排练 commit 完全相同；首次 `git status --short` 为空。

```powershell
$sourceRepository = (git rev-parse --path-format=absolute --show-toplevel).Trim()
$rehearsalCommit = (git rev-parse HEAD).Trim()
$rehearsalRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("pathway-atlas-release-rehearsal-" + [guid]::NewGuid().ToString("N"))
$clonePath = Join-Path $rehearsalRoot "repo"
$artifactPath = Join-Path $rehearsalRoot "artifacts"
New-Item -ItemType Directory -Path $rehearsalRoot, $artifactPath | Out-Null
git -c core.autocrlf=false clone --no-local --no-hardlinks $sourceRepository $clonePath
git -C $clonePath config core.autocrlf false
git -C $clonePath checkout --detach $rehearsalCommit
if ((git -C $clonePath rev-parse HEAD).Trim() -ne $rehearsalCommit) { throw "rehearsal commit mismatch" }
if (git -C $clonePath status --short) { throw "clean clone is dirty before rehearsal" }
```

### 2. 新建 Python 环境并安装全部依赖

- [ ] 使用受支持的 Python 在 clone 外、本次排练根目录内新建 venv，记录实际 OS 与 Python patch 版本。
- [ ] 优先使用本地包缓存执行 editable all/test 安装；如果缺包才允许访问受控依赖源。
- [ ] 安装后再次确认 `git status --short` 为空。

```powershell
$venvPath = Join-Path $rehearsalRoot "venv"
& $python310 -m venv $venvPath
$releasePython = Join-Path $venvPath "Scripts\python.exe"
Push-Location $clonePath
& $releasePython -m pip install -e ".[all,test]"
& $releasePython --version
git status --short
Pop-Location
```

CI 另行在 Ubuntu、Windows、macOS 上覆盖 Python 3.10 与 3.13；本地排练记录实际执行的单一环境，不能替代六项远端矩阵结果。

### 3. 预检、fixture 与真实报告产物

- [ ] `preflight.py` 成功并如实报告离线降级。
- [ ] `demo-312` 与 `demo-33` 两种省份模式都通过数据校验。
- [ ] 已提交的三独立来源证据 fixture 通过校验。
- [ ] 从已提交 dataset/profile/evidence 生成真实 Markdown。
- [ ] 由宿主内部 v3 工作流（当前画像、canonical QueryPlan 与新鲜认证
  证据包）从同一报告模型生成真实 DOCX，文件非空；所有报告仍在 clone 外。

```powershell
Push-Location $clonePath
& $releasePython scripts\preflight.py
& $releasePython scripts\validate_data.py tests\fixtures\provinces\demo-312
& $releasePython scripts\validate_data.py tests\fixtures\provinces\demo-33
& $releasePython scripts\validate_evidence.py tests\fixtures\evidence\three-source-consensus
$markdownReport = Join-Path $artifactPath "anonymous-admission-report.md"
& $releasePython scripts\generate_report.py --dataset tests\fixtures\provinces\demo-312 --profile tests\fixtures\profiles\demo.json --evidence tests\fixtures\evidence\three-source-consensus | Out-File -LiteralPath $markdownReport -Encoding utf8NoBOM
# The public skill/host workflow, not a hand-authored JSON/path command, must
# replay the canonical v3 context and place its DOCX in $artifactPath.
if ((Get-Item -LiteralPath $markdownReport).Length -eq 0) { throw "empty rehearsal Markdown report" }
Pop-Location
```

### 4. 全量质量与发布门禁

- [ ] `unittest discover` 全量通过，记录精确 test count 与 skip count。
- [ ] DOCX 契约测试的 skip count 为 0。
- [ ] tracked compliance scan 零 findings。
- [ ] strict `release_check.py` 返回成功；记录 check count、full-test count、deterministic-boundary count 与 DOCX count。

```powershell
Push-Location $clonePath
& $releasePython -m unittest discover -s tests -v
& $releasePython scripts\compliance_scan.py --tracked
& $releasePython scripts\release_check.py --expected-version 0.1.0
Pop-Location
```

### 5. 双构建与字节级比较

- [ ] 对同一 commit 调用 builder 两次，使用不同的 `dist/` 子目录。
- [ ] 两份 ZIP 的 bytes 完全相同，两份 `SHA256SUMS` 的 bytes 完全相同。
- [ ] 记录 ZIP SHA-256、`SHA256SUMS` SHA-256、ZIP bytes 与 archive entry count。
- [ ] ZIP 内容满足 required paths/prefixes，且不含 Git 元数据、venv、缓存、真实数据或生成报告。

```powershell
Push-Location $clonePath
New-Item -ItemType Directory -Path dist | Out-Null
& $releasePython scripts\build_release.py --version 0.1.0 --output dist/rehearsal-one
& $releasePython scripts\build_release.py --version 0.1.0 --output dist/rehearsal-two
$zipOne = Join-Path $clonePath "dist\rehearsal-one\pathway-atlas-0.1.0.zip"
$zipTwo = Join-Path $clonePath "dist\rehearsal-two\pathway-atlas-0.1.0.zip"
$sumsOne = Join-Path $clonePath "dist\rehearsal-one\SHA256SUMS"
$sumsTwo = Join-Path $clonePath "dist\rehearsal-two\SHA256SUMS"
if (-not [System.Linq.Enumerable]::SequenceEqual([IO.File]::ReadAllBytes($zipOne), [IO.File]::ReadAllBytes($zipTwo))) { throw "ZIP byte mismatch" }
if (-not [System.Linq.Enumerable]::SequenceEqual([IO.File]::ReadAllBytes($sumsOne), [IO.File]::ReadAllBytes($sumsTwo))) { throw "checksum byte mismatch" }
Get-FileHash -Algorithm SHA256 -LiteralPath $zipOne, $sumsOne
Pop-Location
```

### 6. 清理与最终状态

- [ ] 记录结果后删除 clone 内 `dist/` 和 clone 外报告临时目录。
- [ ] 删除前确认目标是本次排练创建的精确目录，且位于本次排练根目录内。
- [ ] 清理后 `git status --short` 为空；源 worktree 也没有 tracked 修改。
- [ ] ignored 的本地 QA/venv 不作为发布 gate 证据；权威结果来自本页所述真实 clean clone。

## 2026-08-25 v0.1.0 排练结果

状态：通过。

- 排练 commit：`eecfcb8189f27317fe9aca7411aa44a37c20c2fc`（独立 checklist commit；关闭 `core.autocrlf` 的 detached clean clone，136 个 tracked archive entries）。
- 环境：`Microsoft Windows NT 10.0.26100.0`，CPython `3.10.20`；新 venv 位于 clone 外。初次本地缓存解析缺少 PyYAML，随后经批准由 pip 安装声明的 `.[all,test]` 依赖。
- 预检与输入：offline 降级符合预期；`demo-312`、`demo-33` 和三独立来源证据 fixture 均有效。
- 真实报告：Markdown 2,705 bytes，SHA-256 `314187d0afa167d5826fb731afd7f028b7d1538dfaee947efb6ce836e159af37`；DOCX 40,464 bytes，SHA-256 `9207e656661b08e780515fb51c0bb6cc1d239984158ea1f67caeba3ad576f99c`。
- 全量测试：741 tests，0 failures/errors，14 个 Windows 平台能力 skips；独立 DOCX suite 20/20，0 skips。
- compliance scan：134 个 tracked 文本文件、2 个已声明二进制文件、0 findings。
- strict release check：18/18 checks，0 failures；full tests 741，DOCX 20/20、0 skips，deterministic boundary 93 tests、13 个已武装网络 canaries、0 次测试期网络尝试。
- ZIP：525,515 bytes、136 entries，SHA-256 `399ec22c63b1a4aaa9a26c878407a1d396258b06d5f993193b6de0ed2f107de8`。
- `SHA256SUMS`：91 bytes，SHA-256 由 `c7d1ecbf18` 与 `852438692b71ebaffdf1e316ae38c762fbf213c376e4990708725b` 无分隔拼接；内容引用同一 ZIP digest。
- 双构建字节比较：两个不同输出目录的 ZIP bytes 相同，两个 `SHA256SUMS` bytes 相同。
- 清理后 clone 状态：首次检查为空；删除 builder 的 `dist/` 后再次为空。
- 排练中验证的 fail-closed 行为：若把 venv 建在 clone 内，第三方包自带的 DOCX/PEM 会被 untracked-sensitive gate 拒绝；因此正式 checklist 明确要求 venv 和普通报告位于 clone 外。builder 还要求安全的中间输出父目录预先存在。
- 已知限制：本地单环境排练不替代 GitHub Actions 的三操作系统、Python 3.10/3.13 六项矩阵；14 个 skips 是本机不可用的 POSIX/symlink 等能力，不包含 DOCX skip；实时来源健康检查是非权威维护遥测，不参与确定性正确性。
