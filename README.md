一句话让AI调用此skill：

```bash
请使用你当前环境的 Skill 安装能力，从 GitHub `https://github.com/sarry12227/pathway-atlas` 安装 `pathway-atlas`（多元星途）；如果 GitHub 无法访问，请改用 Gitee 镜像 `https://gitee.com/sarry1/pathway-atlas`。若环境没有专用安装工具，请将仓库克隆或下载到当前 Agent 的 Skills 目录，确认根目录存在 `SKILL.md` 且其中 `name` 为 `pathway-atlas`，然后重新加载并调用它。
```

<p align="center"><img src="assets/brand/pathway-atlas-horizontal.svg" alt="多元星途 PathwayAtlas" width="100%"></p>

# 多元星途 · PathwayAtlas

**点亮多种升学路径，走出个性升学星途。**

多元星途（PathwayAtlas）是面向全国新高考省份的开源 AI 升学规划 Skill：实时检索并交叉验证公开数据，通过本地确定性管线生成可追溯的普通批冲稳保与多元升学方案。

当前版本是 **v0.1.0 公开预览**。它提供可审计的工作流和合成回放，不是生产就绪服务，也不随仓库分发全国实时录取数据库。

## 为什么做这个项目

升学信息散落在省级考试机构、高校招生网站、网页附件、图片表格和公开转载中。只让 Agent 搜索，数字容易缺少出处、混入重复转载或在不同会话中得到不同计算；只运行本地脚本，又无法获得当年的公开信息。

多元星途把两者分开：

- **Agent 实时检索**：Agent 宿主负责搜索、打开网页、读取公开附件和必要的视觉识别，逐项记录候选来源并交叉验证。
- **本地确定性管线**：证据先归一化和校验，再由 Python 执行位次、选科过滤、普通批和多元路径计算，最后生成报告。计算阶段不访问网络，也不会让 Agent 凭记忆补数字。

任何进入推荐的数字都必须先写入证据包并通过门禁。能力不足、来源冲突或覆盖不完整时，结果会明确降级或停止，而不是补造精确值。

## 用户旅程

一次完整会话遵循仓库根目录 [SKILL.md](SKILL.md) 的六阶段协议，并由 `planning_session.py` 保存唯一、可恢复的状态。用户不接触内部 JSON、文件路径或命令：

1. **画像确认**：Agent 从首条消息自动回填已知信息，逐题引导完成匿名规划：每轮只问一道题并展示当前选项，等待回答再继续；复杂条件分步询问，每题可以明确回答不知道。不要求姓名、电话、身份证或住址，只有用户明确确认后才开始检索。
2. **会话启动与恢复**：Agent 使用 `host_workflow.py start` 完成能力预检与查询计划初始化；`next` 从原检查点继续。问卷归一化、内部文件、命令和会话记录全部由 Agent 处理，家长不需要配置程序。
3. **研究循环**：`query_plan.py` 从已确认画像与省份配置生成 canonical QueryPlan。当前查询年按会话日期动态确定，各数据族独立按 `Y → Y-1 → Y-2 → Y-3` 回查；宿主反复调用 `next`，打开候选正文、运行对应适配器，再以 `ingest` 记录完成结果或不可用原因。
4. **真实资料入库**：`ingest` 接收保存的网页表格、XLSX、公开正文或宿主已核对的 OCR 行，自动提取、校验并保存证据。正文引用保留原文位置，缺字段不补造；进程重启后仍能重放。
5. **计算与文件输出**：任务结束后，`finish` 自动完成证据最终化、计算和报告写入。相同位次下，专业、地域、预算与风险偏好参与判断；没有官方位次时，先利用可验证的学校或联考锚点形成位次区间。Markdown 与可选 DOCX 来自同一个报告模型。
6. **结果与行动**：报告给出冲稳保典型学校，对多元路径明确作出主攻、重点准备、备选、观察或不建议判断，并把 3–7 项“当前最需要做的事”按时间与价值排序。每项都展示来源、证据状态、覆盖范围和不确定性；默认输出匿名 Markdown，可选 DOCX。v0.1.0 仍是公开预览，结果不是录取承诺，也不替代当年官方政策与正式升学建议。

宿主接入的可执行命令和输入示例见 [Host workflow guide](references/host-workflow.md)。用户安装整个 Skill 后，直接说“请使用多元星途帮我做升学规划”即可开始问卷。

## 信源与交叉验证

仓库使用三级信源模型：

- **A 级原始来源**：省级考试机构、教育部门、高校招生网等可确认发布主体的原始渠道。
- **B 级权威整理**：能说明上游出处的权威媒体或可靠升学信息整理。
- **C 级独立整理**：自媒体、论坛、个人或机构整理内容。

只有 C 级信息时，关键数字至少要有 **3 个独立发布者**一致，还要通过结构、一致性和异常检查；同稿转载、同一发布者的多个 URL 或共同引用同一上游稿件不增加独立来源数。冲突数字不取平均，也不挑选方便值。完整的采纳、去重、证据状态和冲突规则以 [信源规范](references/source-policy.md) 为唯一权威，README 不复制其他可能演进的门槛。

## 能力档

`preflight.py` 不猜测宿主工具；Agent 只传入当前会话确实可调用的 `search`、`browse`、`vision`。本地执行与文件输出是另行记录的工作流门禁。

| 档位 | 可做什么 | 必须披露的限制 |
| --- | --- | --- |
| **完整档（full）** | 搜索、网页读取、公开附件解析和可靠视觉识别均可用，且所需可选 Python 模块已安装 | 仍受来源可访问性、发布时效和证据门禁约束 |
| **标准档（standard）** | 可联网处理文本或结构化附件，但缺少可靠 OCR/视觉或部分可选解析能力 | 跳过仅以图片发布的表格，并标明缺失覆盖 |
| **离线档（offline）** | 只使用用户提供或本地已有且可验证的材料 | 不声称已完成当前或全国实时检索 |

能力损失只会降低覆盖，不会降低信源门槛。`preflight.py` 即使发现能力缺失，也会输出包含 `tier` 和 `degradations` 的降级 JSON 并返回退出码 `0`。DOCX 是下游可选输出；能力缺失时保留已经生成的 Markdown、不创建 DOCX，`docx_export.py` 返回退出码 `3`。

## 安装

需要 Python 3.10 或更高版本。下载或克隆仓库后，请保留整个目录；不能只复制 `SKILL.md`，因为流程还依赖 `scripts/`、`schemas/` 和 `references/`。

核心安装不引入运行时第三方包：

```bash
python -m pip install -e .
```

若要运行全部合成演示、测试以及 XLSX/PDF/DOCX 能力：

```bash
python -m pip install -e ".[all,test]"
```

可选依赖组与 `pyproject.toml` 保持一致：`documents` 提供 DOCX，`spreadsheets` 提供 XLSX，`pdf` 提供 PDF，`all` 汇总这三组，`test` 提供测试工具。仓库没有伪装成 OCR 引擎的依赖组；OCR 依赖当前宿主的可靠视觉能力或用户提供的结构化识别结果。

### 作为 Agent Skill 安装

把整个仓库目录放入宿主扫描的 Skill 根目录，使最终结构为 `<skills-root>/pathway-atlas/SKILL.md`。也可以在宿主明确支持时使用指向本仓库的目录符号链接。

支持 `skills` CLI 的宿主可以直接从 GitHub 安装：

```bash
npx skills add sarry12227/pathway-atlas --skill pathway-atlas
```

没有专用安装器时，将 GitHub 主源克隆到当前宿主的 Skill 根目录；GitHub 访问受限时改用公开 Gitee 镜像：

```bash
git clone https://github.com/sarry12227/pathway-atlas.git <skills-root>/pathway-atlas
git clone https://gitee.com/sarry1/pathway-atlas.git <skills-root>/pathway-atlas
```

安装后检查 `<skills-root>/pathway-atlas/SKILL.md` 的 frontmatter `name` 为 `pathway-atlas`，再重新加载宿主。两条 `git clone` 命令是主源与回退源，不要在同一目标目录重复执行。

公开仓库、镜像和第三方 Skill 目录的逐项核验结果见 [分发状态](DISTRIBUTION.md)。只有能够直接打开并核对来源的详情页才会标记为已收录；等待审核不会写成发布成功。

| 宿主 | 推荐位置或注册方式 | 本仓库适配 | 权威说明 |
| --- | --- | --- | --- |
| **Generic Agent** | 使用兼容开放 Agent Skills 规范的 Skill 根目录 | [Generic Agent 映射](references/hosts/generic.md) | [Agent Skills 规范](https://agentskills.io/specification) |
| **Codex** | 用户级 `$HOME/.agents/skills/pathway-atlas`，或仓库级 `.agents/skills/pathway-atlas` | [Codex 映射](references/hosts/codex.md) | [OpenAI：Build skills](https://developers.openai.com/codex/skills) |
| **Claude Code** | 用户级 `~/.claude/skills/pathway-atlas`，或项目级 `.claude/skills/pathway-atlas` | [Claude Code 映射](references/hosts/claude-code.md) | [Anthropic：Extend Claude with skills](https://code.claude.com/docs/en/skills) |
| **Kimi Code** | `$KIMI_CODE_HOME/skills/pathway-atlas`；未设置时为 `~/.kimi-code/skills/pathway-atlas`，也支持共享的 `~/.agents/skills` | [Kimi 映射](references/hosts/kimi.md) | [Kimi Code：Agent Skills](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html) |

重开会话后，可直接复制本页第一行提示词；也可以按宿主文档显式选择 `pathway-atlas`。宿主是否具备搜索、浏览和视觉能力仍需在每次会话中重新预检。

## 合成演示

以下固定样例均为**虚构测试数据**，不联网、不代表任何真实省份、学校或录取结果。先完成带全部 extras 的安装，然后在仓库根目录运行。

查看当前 shell 的离线能力报告：

```bash
python scripts/preflight.py
```

校验虚构的 `3+1+2` 省份数据与三方 C 级共识证据包：

```bash
python scripts/validate_data.py tests/fixtures/provinces/demo-312
python scripts/validate_evidence.py tests/fixtures/evidence/three-source-consensus
```

用同一数据集、匿名画像和证据包生成 Markdown 到标准输出：

```bash
python scripts/generate_report.py --dataset tests/fixtures/provinces/demo-312 --profile tests/fixtures/profiles/demo.json --evidence tests/fixtures/evidence/three-source-consensus
```

DOCX 由宿主在完成问卷、检索和证据归一化后，从同一 immutable
报告模型导出；用户不需要也不应手写画像 JSON、证据包路径或
`canonical QueryPlan`。`docx_export.py` 的 v3 重放入口是宿主内部工具：
它只接受当前画像、canonical QueryPlan 与新鲜认证证据包已绑定的上下文。
在实际会话中直接要求 Agent 使用本 Skill 生成 DOCX；在没有该完整上下文时，
工具会安全拒绝，而不是用演示 fixture 补造报告。

这个最小证据样例只证明证据门禁和报告降级行为；它没有足够的投档行证据，因此报告会如实显示缺失覆盖，而不会制造院校推荐。`demo-33` 另行覆盖 `3+3` 科目组合。更多离线 QR、OCR、屏蔽值和转载去重场景位于 `tests/fixtures/replay/`。

### 当前公开 CLI

文档能力就绪时，宿主可检查 v3 DOCX 导出入口；真实会话仍由统一状态机在内部绑定画像、查询计划与证据包：

```text
python scripts/docx_export.py --help
```

| 入口 | 作用 |
| --- | --- |
| [`scripts/planning_session.py`](scripts/planning_session.py) | 宿主内部管理 `status/init/confirm/next/ingest/finalize/compute` 状态转换 |
| [`scripts/preflight.py`](scripts/preflight.py) | 输出能力档、可选模块和降级项 |
| [`scripts/query_plan.py`](scripts/query_plan.py) | 从匿名画像、省份配置与年份生成查询计划 |
| [`scripts/validate_data.py`](scripts/validate_data.py) | 校验标准化省份数据集 |
| [`scripts/validate_evidence.py`](scripts/validate_evidence.py) | 校验已完成证据包与来源独立性 |
| [`scripts/generate_report.py`](scripts/generate_report.py) | 从已验证输入生成 Markdown |
| [`scripts/docx_export.py`](scripts/docx_export.py) | 宿主内部：从 v3 已绑定报告模型生成 DOCX（不要求用户提供 JSON 或路径） |
| [`scripts/compliance_scan.py`](scripts/compliance_scan.py) | 扫描报告文本的合规风险 |
| [`scripts/live_smoke.py`](scripts/live_smoke.py) | 维护者可选的有界、只读官方入口健康检查；不更新事实，也不参与确定性正确性 |

## 证据与报告长什么样

一次运行将检索候选、发布主体、URL、检索时间、提取方式、内容哈希、采纳状态以及每个事实的字段级来源组织成证据包。报告中的关键数字引用安全的来源编号与状态，而不是只在文末堆 URL。

Markdown 是正式默认产物；DOCX 是安装 `documents` extra 后的可选呈现，两者共享同一个报告模型。报告会显式区分 `official`、`corroborated`、`reference`、`inferred`、`conflict`、`missing`、`masked` 和 `partial`，并显示覆盖范围与主要降级。证据不足时，“当前已验证覆盖范围内未找到”不等于真实空档。

## QR、OCR 与屏蔽值限制

- **QR 只用于发现公开附件**：解码后的 URL 仍必须经过安全下载边界和来源校验。默认不把图片上传到第三方服务；确需外部 QR/OCR 服务时，必须先得到用户明确授权，并在证据中披露第三方处理。
- **OCR 不是精确事实捷径**：必须核对页数或声明实际覆盖页，复核分布在不同位置的锚点，并执行结构与单调性检查。局部 OCR 或区间采样只能形成 `partial`。
- **屏蔽值保持屏蔽**：诸如“某分以上”或“前若干名”的边界记为 `masked`，不得当作精确投档值排序。
- **没有可靠视觉能力就降级**：标准档会寻找 HTML、XLSX、PDF 或文本替代；找不到时标记缺失，不把图片表格猜成数字。

## 隐私与数据许可

- 默认匿名：学生姓名、电话、身份证、住址、通信 ID 和凭证都不是运行所需字段，不进入查询词、证据包、缓存或日志。报告默认使用匿名文件名。
- 证据默认留在本地临时工作目录；原始网页、附件和生成报告不提交到 Git。使用外部 OCR/QR 服务前还要确认内容不含个人信息。
- 仓库中的代码和明确标记的虚构测试数据按 [MIT 许可证](LICENSE)提供。MIT 不自动授予第三方数据的再分发权；真实或外部数据仍受其来源条款约束，未确认许可时不随仓库发布。
- 数据来源、许可审查、更正和删除边界见 [DATA_SOURCES.md](DATA_SOURCES.md)；提交数据或省份规则前请读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 限制与免责声明

**AI 生成仅供参考。本项目不保证录取，不替代省级考试机构、高校招生部门或其他官方渠道的当年政策，也不提供法律、财务或教育决策承诺。**

v0.1.0 是公开预览：网站可访问性、当年数据是否发布、宿主能力和来源许可都可能让流程降级。任何正式填报或路径申报都应回到省级考试机构和高校当年官方信息复核。发现安全问题时请按 [SECURITY.md](SECURITY.md) 私下报告；一般问题与改进建议可通过 GitHub Issues 或 [贡献指南](CONTRIBUTING.md) 提交，切勿附带真实学生数据。

## 测试

```bash
python -m unittest discover -s tests -v
```
