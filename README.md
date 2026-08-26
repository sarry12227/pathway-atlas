> **复制给 AI：**「请调用 `pathway-atlas`（多元星图）Skill。先询问我的省份、选科、分数或位次和升学意向；不要索取姓名、电话、身份证、住址或本地文件路径。请基于可验证的公开来源，先验证证据再计算，为我分析普通批冲稳保及适合的多元升学路径，并在每项建议旁标注来源、证据状态、覆盖范围和不确定性。」

<p align="center"><img src="assets/brand/pathway-atlas-horizontal.svg" alt="多元星图 PathwayAtlas" width="100%"></p>

# 多元星图 · PathwayAtlas

**不只一条升学路，每条路都有据可循。**

多元星图（PathwayAtlas）是面向全国新高考省份的开源 AI 升学规划 Skill：实时检索并交叉验证公开数据，通过本地确定性管线生成可追溯的普通批冲稳保与多元升学方案。

当前版本是 **v0.1.0 公开预览**。它提供可审计的工作流和合成回放，不是生产就绪服务，也不随仓库分发全国实时录取数据库。

## 为什么做这个项目

升学信息散落在省级考试机构、高校招生网站、网页附件、图片表格和公开转载中。只让 Agent 搜索，数字容易缺少出处、混入重复转载或在不同会话中得到不同计算；只运行本地脚本，又无法获得当年的公开信息。

多元星图把两者分开：

- **Agent 实时检索**：Agent 宿主负责搜索、打开网页、读取公开附件和必要的视觉识别，逐项记录候选来源并交叉验证。
- **本地确定性管线**：证据先归一化和校验，再由 Python 执行位次、选科过滤、普通批和多元路径计算，最后生成报告。计算阶段不访问网络，也不会让 Agent 凭记忆补数字。

任何进入推荐的数字都必须先写入证据包并通过门禁。能力不足、来源冲突或覆盖不完整时，结果会明确降级或停止，而不是补造精确值。

## 用户旅程

一次完整会话遵循仓库根目录 [SKILL.md](SKILL.md) 的六阶段协议：

1. **信息采集**：先确认省份，再采集选科、分数或排名、学校全称及匿名意向；不要求姓名、电话、身份证或住址。
2. **能力预检**：Agent 检查当前宿主实际可用的搜索、浏览、视觉、本地执行和文件输出能力，再运行 `preflight.py` 保存能力档与降级项。
3. **查询计划**：`query_plan.py` 按省份配置、考试年份与目标路径产生确定性任务；Agent 按 [检索流程](references/retrieval-playbook.md) 分任务执行。
4. **证据归一化**：网页、XLSX、PDF、OCR 或 QR 发现的公开材料进入对应适配器；数据与证据分别通过 `validate_data.py`、`validate_evidence.py`。
5. **确定性计算**：只读取已验证的数据集、匿名画像、证据快照和省份策略；缺失或冲突事实不会进入精确推荐。
6. **报告输出**：默认生成匿名 Markdown；安装文档依赖后可从同一报告模型生成 DOCX，并逐项展示字段级来源、证据状态、覆盖范围和降级原因。

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

生成可选 DOCX 文件：

```bash
python scripts/docx_export.py --dataset tests/fixtures/provinces/demo-312 --profile tests/fixtures/profiles/demo.json --evidence tests/fixtures/evidence/three-source-consensus --output anonymous-admission-report.docx
```

这个最小证据样例只证明证据门禁和报告降级行为；它没有足够的投档行证据，因此报告会如实显示缺失覆盖，而不会制造院校推荐。`demo-33` 另行覆盖 `3+3` 科目组合。更多离线 QR、OCR、屏蔽值和转载去重场景位于 `tests/fixtures/replay/`。

### 当前公开 CLI

| 入口 | 作用 |
| --- | --- |
| [`scripts/preflight.py`](scripts/preflight.py) | 输出能力档、可选模块和降级项 |
| [`scripts/query_plan.py`](scripts/query_plan.py) | 从匿名画像、省份配置与年份生成查询计划 |
| [`scripts/validate_data.py`](scripts/validate_data.py) | 校验标准化省份数据集 |
| [`scripts/validate_evidence.py`](scripts/validate_evidence.py) | 校验已完成证据包与来源独立性 |
| [`scripts/generate_report.py`](scripts/generate_report.py) | 从已验证输入生成 Markdown |
| [`scripts/docx_export.py`](scripts/docx_export.py) | 从同一报告模型生成 DOCX |
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
