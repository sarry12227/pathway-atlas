# 多元星途 · PathwayAtlas 品牌迁移规格

日期：2026-08-26（2026-08-27 修订）
状态：品牌方向已确认，待实施计划复核
当前仓库：`sarry12227/shengxue-skill` / `sarry1/shengxue-skill`  
目标仓库：`sarry12227/pathway-atlas` / `sarry1/pathway-atlas`

## 1. 目标

将当前公开项目从临时工程名 `shengxue-skill` 统一迁移为面向学生和家长的双语品牌：

- 中文品牌：**多元星途**
- 英文品牌：**PathwayAtlas**
- 仓库、Skill 与 Python distribution 标识：`pathway-atlas`
- 品牌口号：**点亮多种升学路径，走出个性升学星途。**

迁移必须让用户进入 GitHub 或 Gitee 默认分支时立即看到新品牌、Logo 和可复制调用提示词；不得要求用户切换分支或自行猜测 Skill 名称。

## 2. 品牌理念

Logo 在已选第 4 版的基础上渐进升级。保留五条路径、北大红、深靛蓝和证据金的识别基础，但将偏静态的闭合“星图/花瓣”结构改为向前延伸的多条星轨：不同起点可以汇合，也可以分流，沿途经过可核验的金色证据节点，共同朝向远方的引导星。它表达的是持续规划与行动过程，而不是一张替学生预设答案的静态地图。

品牌调性从“展示升学地图”转向“陪伴学生和家长看清多种可能、验证关键证据、走出个性路径”。文字与图形应兼具温度和可信度：不使用机械的 AI 光效、通用机器人符号、过度科技霓虹或学校官方标志；视觉重点是人的选择、路径的展开和证据带来的确定感。

标准色：

- 北大红：`#94070A`
- 深靛蓝：`#14213D`
- 证据金：`#C9A227`

不得使用北京大学标志、校名字体或其他受保护识别元素；这里只使用公开标准色值。正式资产以仓库 `assets/brand/` 下的 SVG 为语义源，PNG 仅作平台预览与兼容输出。

## 3. README 首屏

README 第 1 行必须是以下可直接复制给 AI 的提示词，位于标题、Logo、徽章和项目介绍之前。提示词必须形成“安装 SkillHub → 安装 Skill → 重新加载 → 调用 Skill”的完整闭环，而不是假设用户已经安装：

> **复制给 AI：**「请先阅读 https://skillhub.cn/install/skillhub.md：如果尚未安装 SkillHub，请按文档完成安装；然后使用 SkillHub 搜索并将 `pathway-atlas`（多元星途）安装到你当前 Agent 的 Skills 目录，确认安装成功并重新加载。安装后请调用 `pathway-atlas`：先询问我的省份、选科、分数或位次和升学意向；不要索取姓名、电话、身份证、住址或本地文件路径。请基于可验证的公开来源，先验证证据再计算，为我分析普通批冲稳保及适合的多元升学路径，并在每项建议旁标注来源、证据状态、覆盖范围和不确定性。」

README 随后展示横版 Logo、`多元星途 · PathwayAtlas` 标题、口号“点亮多种升学路径，走出个性升学星途。”与当前公开预览说明。安装示例统一使用 `pathway-atlas` 目录和 Skill 标识。

README 的安装说明应补充可检查的命令示例：先检查 `skillhub --version`，再运行 `skillhub search pathway-atlas`，最后以 `skillhub install pathway-atlas --dir <当前 Agent 的 Skills 目录>` 安装。`--dir` 不得省略，因为 SkillHub 默认的 `./skills/` 不一定会被 Agent 识别。若搜索结果不能唯一确认发布者和版本，AI 必须先向用户展示候选与风险，不得安装名称相似的替代 Skill。

## 4. 迁移策略

采用一次性干净切换，不保留第二个可调用 Skill 名称或旧 Python distribution 别名：

1. 更新当前用户可见品牌、安装路径、命令提示、社区模板和安全说明。
2. 更新 `SKILL.md` frontmatter `name` 为 `pathway-atlas`。
3. 更新 `pyproject.toml` distribution 名称、可选依赖安装提示、下载 User-Agent、发布 ZIP 根目录和文件名。
4. 更新 Schema `$id`、CI/release workflow、release checker、release policy 与对应测试。
5. 更新 GitHub/Gitee 当前仓库 URL、Issue 模板链接、DATA_SOURCES 与安全入口。
6. 将 GitHub 和 Gitee 仓库重命名为 `pathway-atlas`，保持默认分支为 `main`；更新本地 remotes。
7. 将 `pathway-atlas` 发布或同步到 SkillHub，确认搜索结果、发布者身份、安装命令和安装后 Skill 标识均与 README 一致。
8. GitHub 保持当前可见性不变；Gitee 保持公开。改变 GitHub 可见性不属于本次授权。

不采用以下方案：

- 只改展示名：会让 Skill、安装目录与发布包继续暴露旧工程名。
- 长期双别名：项目尚未正式发布，兼容层会制造两个品牌入口并增加维护成本。

## 5. 名称边界

以下当前接口必须全部迁移：

- README、SKILL、CONTRIBUTING、SECURITY 与当前社区模板中的产品名
- GitHub/Gitee 仓库 slug、当前远程链接和默认分支入口
- Python distribution、安装示例与 generated egg-info 名称规则
- 发布 ZIP 文件名、ZIP 根目录、校验和、workflow artifact 路径
- Schema `$id` 中的项目命名空间
- HTTP User-Agent 中的产品名
- 当前测试、release policy 和品牌契约

以下历史记录保留原文，不作为残留旧品牌缺陷：

- 已提交的 `docs/superpowers/plans/` 历史计划
- `.scratch/shengxue-skill-open-source/spec.md` 原始立项规格
- Git 历史、既有 commit message 与本地私有归档恢复路径
- 当前工作树物理目录名；远程迁移完成后可在不破坏 worktree 的独立维护步骤中重建本地目录

公开运行时、当前文档和发布产物不得再把旧名称作为有效入口。

## 6. Logo 资产

本次纳入并验证：

- `assets/brand/pathway-atlas-mark.svg`
- `assets/brand/pathway-atlas-horizontal.svg`
- `assets/brand/pathway-atlas-monochrome.svg`
- `assets/brand/pathway-atlas-mark.png`
- `assets/brand/pathway-atlas-horizontal.png`

SVG 必须是有效 XML、无外部资源、无脚本、无嵌入路径或个人信息。PNG 必须带 alpha 通道。README 使用相对路径引用横版 SVG；平台不支持 SVG 时使用 PNG。

横版 Logo 的中文主标必须为“多元星途”，英文 `PathwayAtlas` 作为辅助识别；口号不嵌入小尺寸图标，只进入横版品牌资产和 README。单图标在小尺寸下仍需清楚呈现“多路径、证据节点、向前引导”三层语义。黑白版必须保留路径层级，不得仅靠红、蓝、金区分含义。

## 7. 外部仓库切换

本地迁移提交通过测试后执行：

1. 推送 `codex/` 分支到 GitHub，并通过 PR 合入默认 `main`。
2. 在 GitHub 将仓库重命名为 `pathway-atlas`，更新 description、topics、README/social preview；确认默认分支仍为 `main`。
3. 将同一内容树快进同步到 Gitee `main`，再将 Gitee 仓库重命名为 `pathway-atlas`，更新简介和 Logo；确认公开访问与默认分支。
4. 更新本地 `origin`、`gitee` URL，核对两个远端默认分支的 tree hash 一致。
5. 按 SkillHub 的发布流程提交 `pathway-atlas`，必要时由用户完成平台要求的登录、实名或授权；在独立临时 Skills 目录中执行搜索和安装，确认安装包内容来自已验证发布树。
6. 分别以未登录访问检查仓库首页、README 第一行提示词、Logo 和默认分支，并复核 SkillHub 的公开详情页。

若任一平台重命名失败，不删除旧仓库、不强推、不创建内容不同的替代主分支。保持已验证提交可恢复，并在继续前报告平台状态。

## 8. 测试与验收

先更新判别性测试，再修改生产与文档：

- README 第 1 行必须精确包含 SkillHub 安装说明、可复制调用提示词、`pathway-atlas` 和“多元星途”。
- 在临时 Skills 目录中按 README 命令安装后，Agent 必须能发现 `pathway-atlas`；不得把 SkillHub 默认 `./skills/` 当成已成功安装。
- 当前用户可见表面不得继续展示“多元星图”；历史品牌设计提交不重写。
- `SKILL.md`、`pyproject.toml`、release builder/checker/workflow 必须使用新标识。
- 当前公共表面不得出现旧标识；历史计划目录使用显式排除清单。
- Logo SVG 解析、PNG alpha、README 相对链接必须通过。
- 发布包文件名和 ZIP 根目录必须为 `pathway-atlas-<version>.zip` / `pathway-atlas/`。
- GitHub/Gitee 当前 URL、默认分支和 README 首页必须可访问。
- 运行受影响测试后，只运行一次新的 all-extras 全量测试；不复用旧的 761 项结论，也不无意义重复同一全量门禁。
- tracked compliance、release rehearsal、diff check、敏感信息扫描和 deterministic build 必须通过。

## 9. 完成标准

只有同时满足以下条件才宣告迁移完成：

1. 本地当前公共接口和发布产物统一为 `pathway-atlas`。
2. README 第一行提示词可直接复制，且“多元星途”Logo 与口号在默认分支首屏显示。
3. GitHub 默认 `main` 和 Gitee 默认 `main` 展示同一内容树。
4. GitHub/Gitee 新仓库 URL 可访问，本地 remotes 指向新 URL。
5. SkillHub 能搜索并安装唯一的 `pathway-atlas`，安装后 Skill 标识与 README 调用指令一致。
6. 受影响测试、一次全量测试、compliance 与 release gate 均给出新鲜证据。
7. 未修改用户拥有的无关文件，未改变 GitHub 可见性。
