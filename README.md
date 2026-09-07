一句话让AI调用此skill：

```bash
请使用你当前环境的 Skill 安装能力，从 GitHub `https://github.com/sarry12227/pathway-atlas` 安装或更新 `pathway-atlas`（多元星途）到最新版；如果 GitHub 无法访问，请改用 Gitee 镜像 `https://gitee.com/sarry1/pathway-atlas`。若没有专用安装工具，请将完整仓库克隆或下载到当前 Agent 可识别的 Skills 目录，保留已有咨询记录，确认根目录存在 `SKILL.md` 且其中 `name` 为 `pathway-atlas`，再按当前环境支持的方式重新加载并调用它。成功加载后，沿用我已提供的信息，每轮只问一道题并展示当前选项，等待我回答；我确认个人情况后，再检索和核验公开资料。最后直接在对话中详细说明有依据的结论、理由、优先行动和待核验事项，文件作为补充。若无法安装或加载，请如实说明具体阻碍和最少下一步，不要声称已调用。
```

<p align="center"><img src="assets/brand/pathway-atlas-logo.png" alt="多元星途 PathwayAtlas：展开的书页中，三条路径汇向星光。陪你看清选择。" width="420"></p>

# 多元星途 · PathwayAtlas

**陪你看清选择。** 点亮多种升学路径，走出个性升学星途。

多元星途（PathwayAtlas）是给学生和家长用的开源 AI 升学规划 Skill。它让支持 Skill 的 AI 一步一步了解孩子的情况，查证公开招生信息，把选学校、选专业和多元升学路径整理成看得懂、能行动的规划。

**看清有哪些选择，知道下一步怎么走。** 无需先学会招生术语，也无需一次准备完所有材料；从你现在最关心的一个问题开始。

[你会得到什么](#你会得到什么) · [认识升学路径](#多元升学到底是什么) · [三步开始](#三步开始) · [常见问题](#常见问题)

## 你是不是也有这些困惑

- “只知道孩子的分数，不知道该怎样看学校和专业。”
- “听过强基、综评、专项，但不知道哪些和我们有关。”
- “选科、外语、城市和预算都要考虑，信息太多，越看越乱。”
- “孩子还在高一、高二，现在准备什么，才不至于临近报考才发现遗漏？”

多元星途把这些问题放进同一份个人规划，结合成绩口径、兴趣、家庭条件与未来打算，解释选择的理由、限制和准备顺序。学生、家长或老师都可以发起咨询。

## 你会得到什么

| 你关心的问题 | 规划会给你的帮助 |
| --- | --- |
| **现在的水平，该怎样看学校？** | 有可靠依据时给出位次区间与“冲、稳、保、观察”院校分析，并解释专业、地域和预算如何影响选择 |
| **哪些升学路径值得投入？** | 梳理普通批之外的相关选择，区分主攻、重点准备、备选、观察与不建议，并说明已满足和待核验的条件 |
| **哪些限制会影响决定？** | 核对选科、外语、费用、体检及服务义务等因素，说明哪些已明确、哪些还需要查证 |
| **现在先做什么？** | 给出3–7项优先行动，写清做什么、为什么、完成标准，以及下一阶段何时复盘 |

**结论直接写在对话里。** 你会读到总体判断、院校与专业分析、路径比较、分阶段计划和风险依据；有材料支持的数字和政策附对应来源。Markdown 文件作为补充下载，环境支持时可提供 Word（DOCX），用户无需先打开文件才能理解规划。

资料不足时，先交付已成立的结论或基于已确认情况的准备建议，并具体说明缺口。比如校内月考成绩缺少可靠校准依据时，不把它直接换算成高考省排或学校录取判断。

## 多元升学到底是什么

可以把它理解为：**除了按高考成绩研究普通批志愿，也了解与自己条件相关的其他招生和培养选择。** 不必先决定报哪一种，Agent 会逐项解释、查证，再帮助你判断投入顺序。

| 常见名称 | 先用一句话理解 | 多元星途帮你弄清什么 |
| --- | --- | --- |
| 普通批志愿 | 根据高考成绩、位次与招生规则选择学校和专业 | 怎样安排冲稳保，学校线与专业要求有什么区别 |
| 强基计划 | 围绕基础学科人才选拔与培养的招生途径 | 兴趣和培养方向是否匹配，报名类别、选科及考核要求是什么 |
| 综合评价／三位一体 | 结合高考成绩及其他考核信息进行评价的招生方式 | 本省有哪些相关项目，各校怎样评价，需要准备哪些材料 |
| 国家／地方／高校专项 | 面向符合相应地区、户籍、学籍等条件考生的专项招生 | 三类计划分别要核对什么，不能只凭“农村户籍”直接认定资格 |
| 公费师范／定向培养 | 将培养、资助与履约等要求放在一起考虑的选择 | 费用、毕业去向、服务期和地域约束能否接受 |
| 港澳高校／中外合作等 | 学习地点、培养方式或授课语言可能不同的选择 | 录取方式、语言要求、费用和个人目标如何匹配 |
| 军警及其他特殊方向 | 需要进一步核对特定招生条件的方向 | 哪些体检、考察、校测或其他要求适用，仍有哪些信息待确认 |

这些是需要核验的选择，不代表每个学生都符合资格。具体以生源省份和院校当年要求为准。术语可进一步阅读[教育部强基说明](https://www.moe.gov.cn/jyb_xwfb/gzdt_gzdt/s5987/202001/t20200115_415579.html)、[浙江三位一体说明](https://www.zjzs.net/col/col392/index.html)、[教育部高考专项说明](https://www.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/2026/2026_zt08/)和[部属师范大学公费教育办法](https://www.moe.gov.cn/jyb_xxgk/moe_1777/moe_1778/202406/t20240614_1135654.html)。历史说明用于认识概念，个人规划仍需核对申请年度规则。

## 三步开始

1. **把本页开头的提示词发给 Agent。** 使用支持安装和运行 Skills 的环境，例如 Codex、Claude Code、Kimi Code 或兼容 Agent。把整段中文发到聊天框即可，安装目录和加载步骤交由 Agent 处理。
2. **像聊天一样，一题一题回答。** Agent 会沿用你已提供的信息，每轮只问一道题、展示当前选项，等你回答后继续。暂时不清楚可以说“不确定”，说错了可以随时修改；最后由你确认汇总的个人情况。
3. **阅读结论，按优先级行动。** 确认后 Agent 开始查证资料，在对话里解释完整规划。后续有新的考试、目标或限制时，在保留原记录的会话里补充变化，更新受影响的判断。

已经安装时，可以直接这样开场：

> 请使用多元星途帮我做升学规划。我现在读高二，还不知道适合什么专业，请一步一步问我。

> 请使用多元星途帮我们梳理普通批、强基和综评。我不了解这些路径，请先了解孩子的情况。

上面是可复制的开场方式，不是示例学生的评估结果。**你不用一次回答20题，也不用创建JSON、填写内部文件路径或执行研究命令。**

## 常见问题

**还没高考、没有省排名，也能开始吗？**

可以先梳理目标、选科、兴趣和准备方向。学校或联考材料足以校准时，才给带依据的位次区间；依据不足时先给准备建议，明确下一次定位需要什么材料。

**完全不懂政策，或者暂时没有理想大学呢？**

都可以开始。未知项会被记录，Agent 按问题逐步引导，不要求你先把招生政策研究一遍。

**没有竞赛、外语考日语，是否就少了很多选择？**

不能仅凭一个标签下结论。规划会分别核验项目类别、选科、报名语种、校测和授课要求，解释实际适用的条件与准备成本。

**需要额外购买搜索API或修好浏览器吗？**

优先使用 Agent 当前已有能力。一个浏览器或某个解析工具失败时，会尝试其他可用的原文读取方式；资料仍不足时交付部分结论或准备建议，说明哪些暂不能判断。实时查证需要可用的公开资料访问能力。

**复制提示词后一定能安装吗？**

取决于你使用的 Agent 是否开放安装、加载和本地执行能力。Skill 是提供给 Agent 的工作方法与配套工具；当前环境不能安装时，应由 Agent 如实说明具体阻碍和最少下一步。已安装旧版时可用开头的提示词更新，保留原咨询记录。

**会替我报名、填志愿或保证录取吗？**

它帮助你理解、比较和准备。报名、正式填报及最终决策仍需本人按官方渠道办理，本项目不保证录取。

**在哪里查看最新版？**

[GitHub 主源](https://github.com/sarry12227/pathway-atlas)与[Gitee 镜像](https://gitee.com/sarry1/pathway-atlas)同步维护。当前为 **v0.1.7 公开预览**；第三方目录可能有缓存，已收录不等于已更新，逐项状态见[分发记录](DISTRIBUTION.md)。

## 技术与部署

<details>
<summary>给开发者与进阶用户：安装命令、宿主接入、工作流程与证据规则</summary>

下面的命令由开发者或具备执行能力的 Agent 使用；学生和家长可直接按“三步开始”发起咨询。

## 为什么做这个项目

升学信息散落在省级考试机构、高校招生网站、网页附件、图片表格和公开转载中。只让 Agent 搜索，数字容易缺少出处、混入重复转载或在不同会话中得到不同计算；只运行本地脚本，又无法获得当年的公开信息。

多元星途把两者分开：

- **Agent 实时检索**：Agent 宿主负责搜索、打开网页、读取公开附件和必要的视觉识别，逐项记录候选来源并交叉验证。
- **本地确定性管线**：证据先归一化和校验，再由 Python 执行位次、选科过滤、普通批和多元路径计算，最后生成报告。计算阶段不访问网络，也不会让 Agent 凭记忆补数字。

任何进入推荐的数字都必须先写入证据包并通过门禁。能力不足、来源冲突或覆盖不完整时，只停止依赖缺失事实的判断，继续可支持的部分并交付准备建议，不补造精确值。故障处理见[研究恢复指南](references/research-recovery.md)。

## 用户旅程

一次完整会话遵循仓库根目录 [SKILL.md](SKILL.md) 的六阶段协议，并由 `planning_session.py` 保存唯一、可恢复的状态。用户不接触内部 JSON、文件路径或命令：

1. **画像确认**：Agent 从首条消息自动回填已知信息，逐题引导完成匿名规划：每轮只问一道题并展示当前选项，等待回答再继续；复杂条件分步询问，每题可以明确回答不知道。不要求姓名、电话、身份证或住址，只有用户明确确认后才开始检索。
2. **会话启动与恢复**：Agent 使用 `host_workflow.py start` 完成能力预检与查询计划初始化；`next` 从原检查点继续。问卷归一化、内部文件、命令和会话记录全部由 Agent 处理，家长不需要配置程序。
3. **研究循环**：`query_plan.py` 从已确认画像与省份配置生成 canonical QueryPlan。当前查询年按会话日期动态确定，各数据族独立按 `Y → Y-1 → Y-2 → Y-3` 回查；宿主反复调用 `next`，打开候选正文、运行对应适配器，再以 `ingest` 记录完成结果或不可用原因。
4. **真实资料入库**：`ingest` 接收保存的网页表格、XLS/XLSX、PDF、公开正文或宿主已核对的 OCR 行，自动提取、校验并保存证据。正文引用保留原文位置，缺字段不补造；进程重启后仍能重放。
5. **计算与报告生成**：任务结束后，`finish` 自动完成证据最终化、计算和报告写入。相同位次下，专业、地域、预算与风险偏好参与判断；没有官方位次时，先利用可验证的学校或联考锚点形成位次区间。Markdown 与可选 DOCX 来自同一个报告模型。
6. **对话结论与行动**：Agent 直接在对话中详细解释总体结论、位次定位、院校和专业选择、各条升学路径、优先行动、分阶段计划及风险依据；用户不用打开文件即可阅读所有结论。报告给出冲稳保典型学校，对多元路径明确作出主攻、重点准备、备选、观察或不建议判断，并把 3–7 项“当前最需要做的事”按时间与价值排序。每项都展示来源、证据状态、覆盖范围和不确定性；Markdown 或可选 DOCX 在正文之后作为附带下载。当前仍是公开预览，结果不是录取承诺，也不替代当年官方政策与正式升学建议。

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
| **标准档（standard）** | 可打开原文即可从已知官方入口查证；缺少搜索、视觉或某个解析库时使用已有替代能力 | 只处理实际可读的格式；没有可靠视觉时不猜图片表格，并标明缺失覆盖 |
| **离线档（offline）** | 只使用用户提供或本地已有且可验证的材料 | 不声称已完成当前或全国实时检索 |

能力损失只会降低覆盖，不会降低信源门槛。`preflight.py` 即使发现能力缺失，也会输出包含 `tier` 和 `degradations` 的降级 JSON 并返回退出码 `0`。DOCX 是下游可选输出；能力缺失时保留已经生成的 Markdown、不创建 DOCX，`docx_export.py` 返回退出码 `3`。

## 安装

需要 Python 3.10 或更高版本。下载或克隆仓库后，请保留整个目录；不能只复制 `SKILL.md`，因为流程还依赖 `scripts/`、`schemas/` 和 `references/`。

核心安装不引入运行时第三方包：

```bash
python -m pip install -e .
```

若要运行全部合成演示、测试以及 XLS/XLSX/PDF/DOCX 能力：

```bash
python -m pip install -e ".[all,test]"
```

可选依赖组与 `pyproject.toml` 保持一致：`documents` 提供 DOCX，`spreadsheets` 提供 XLS/XLSX，`pdf` 提供 pdfplumber 与 pypdf 解析能力，`all` 汇总这三组，`test` 提供测试工具。仓库没有伪装成 OCR 引擎的依赖组；OCR 依赖当前宿主实际执行并核验的可靠视觉提取。

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

对话正文是面向用户的主要交付。Markdown 是默认文件导出；DOCX 是安装 `documents` extra 后的可选呈现，两者共享同一个报告模型。报告会显式区分 `official`、`corroborated`、`reference`、`inferred`、`conflict`、`missing`、`masked` 和 `partial`，并显示覆盖范围与主要降级。证据不足时，“当前已验证覆盖范围内未找到”不等于真实空档。

## QR、OCR 与屏蔽值限制

- **QR 只用于发现公开附件**：解码后的 URL 仍必须经过安全下载边界和来源校验。默认不把图片上传到第三方服务；确需外部 QR/OCR 服务时，必须先得到用户明确授权，并在证据中披露第三方处理。
- **OCR 不是精确事实捷径**：必须核对页数或声明实际覆盖页，复核分布在不同位置的锚点，并执行结构与单调性检查。局部 OCR 或区间采样只能形成 `partial`。
- **屏蔽值保持屏蔽**：诸如“某分以上”或“前若干名”的边界记为 `masked`，不得当作精确投档值排序。
- **没有可靠视觉能力就降级**：标准档会寻找 HTML、XLSX、PDF 或文本替代；找不到时标记缺失，不把图片表格猜成数字。

</details>

## 隐私与数据许可

- 默认匿名：学生姓名、电话、身份证、住址、通信 ID 和凭证都不是运行所需字段，不进入查询词、证据包、缓存或日志。报告默认使用匿名文件名。
- 证据默认留在本地临时工作目录；原始网页、附件和生成报告不提交到 Git。使用外部 OCR/QR 服务前还要确认内容不含个人信息。
- 仓库中的代码和明确标记的虚构测试数据按 [MIT 许可证](LICENSE)提供。MIT 不自动授予第三方数据的再分发权；真实或外部数据仍受其来源条款约束，未确认许可时不随仓库发布。
- 数据来源、许可审查、更正和删除边界见 [DATA_SOURCES.md](DATA_SOURCES.md)；提交数据或省份规则前请读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 限制与免责声明

**AI 生成仅供参考。本项目不保证录取，不替代省级考试机构、高校招生部门或其他官方渠道的当年政策，也不提供法律、财务或教育决策承诺。**

项目自 v0.1.0 起公开预览，当前版本见上方说明与[变更记录](CHANGELOG.md)。网站可访问性、当年数据是否发布、宿主能力和来源许可会影响可验证范围，仓库不附带全国实时录取数据库。任何正式填报或路径申报都应回到省级考试机构和高校当年官方信息复核。发现安全问题时请按 [SECURITY.md](SECURITY.md) 私下报告；一般问题与改进建议可通过 GitHub Issues 或[贡献指南](CONTRIBUTING.md)提交，切勿附带真实学生数据。

## 测试

```bash
python -m unittest discover -s tests -v
```
