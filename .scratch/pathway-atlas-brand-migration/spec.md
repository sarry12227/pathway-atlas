# 多元星图 · PathwayAtlas 品牌迁移规格

日期：2026-08-26  
状态：待用户书面确认后实施  
当前仓库：`sarry12227/shengxue-skill` / `sarry1/shengxue-skill`  
目标仓库：`sarry12227/pathway-atlas` / `sarry1/pathway-atlas`

## 1. 目标

将当前公开项目从临时工程名 `shengxue-skill` 统一迁移为面向学生和家长的双语品牌：

- 中文品牌：**多元星图**
- 英文品牌：**PathwayAtlas**
- 仓库、Skill 与 Python distribution 标识：`pathway-atlas`
- 传播语：**不只一条升学路，每条路都有据可循。**

迁移必须让用户进入 GitHub 或 Gitee 默认分支时立即看到新品牌、Logo 和可复制调用提示词；不得要求用户切换分支或自行猜测 Skill 名称。

## 2. 品牌理念

Logo 使用五条闭合星轨表达多种升学路径，金色节点表达可核验的证据锚点，中心金星表达学生自己的目标。轨道之间保留开放空间，表示方案提供可复核的选择，而不是替学生预设唯一答案。

标准色：

- 北大红：`#94070A`
- 深靛蓝：`#14213D`
- 证据金：`#C9A227`

不得使用北京大学标志、校名字体或其他受保护识别元素；这里只使用公开标准色值。正式资产以仓库 `assets/brand/` 下的 SVG 为语义源，PNG 仅作平台预览与兼容输出。

## 3. README 首屏

README 第 1 行必须是以下可直接复制给 AI 的提示词，位于标题、Logo、徽章和项目介绍之前：

> **复制给 AI：**「请调用 `pathway-atlas`（多元星图）Skill。先询问我的省份、选科、分数或位次和升学意向；不要索取姓名、电话、身份证、住址或本地文件路径。请基于可验证的公开来源，先验证证据再计算，为我分析普通批冲稳保及适合的多元升学路径，并在每项建议旁标注来源、证据状态、覆盖范围和不确定性。」

README 随后展示横版 Logo、`多元星图 · PathwayAtlas` 标题、传播语与当前公开预览说明。安装示例统一使用 `pathway-atlas` 目录和 Skill 标识。

## 4. 迁移策略

采用一次性干净切换，不保留第二个可调用 Skill 名称或旧 Python distribution 别名：

1. 更新当前用户可见品牌、安装路径、命令提示、社区模板和安全说明。
2. 更新 `SKILL.md` frontmatter `name` 为 `pathway-atlas`。
3. 更新 `pyproject.toml` distribution 名称、可选依赖安装提示、下载 User-Agent、发布 ZIP 根目录和文件名。
4. 更新 Schema `$id`、CI/release workflow、release checker、release policy 与对应测试。
5. 更新 GitHub/Gitee 当前仓库 URL、Issue 模板链接、DATA_SOURCES 与安全入口。
6. 将 GitHub 和 Gitee 仓库重命名为 `pathway-atlas`，保持默认分支为 `main`；更新本地 remotes。
7. GitHub 保持当前可见性不变；Gitee 保持公开。改变 GitHub 可见性不属于本次授权。

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

## 7. 外部仓库切换

本地迁移提交通过测试后执行：

1. 推送 `codex/` 分支到 GitHub，并通过 PR 合入默认 `main`。
2. 在 GitHub 将仓库重命名为 `pathway-atlas`，更新 description、topics、README/social preview；确认默认分支仍为 `main`。
3. 将同一内容树快进同步到 Gitee `main`，再将 Gitee 仓库重命名为 `pathway-atlas`，更新简介和 Logo；确认公开访问与默认分支。
4. 更新本地 `origin`、`gitee` URL，核对两个远端默认分支的 tree hash 一致。
5. 分别以未登录访问检查仓库首页、README 第一行提示词、Logo 和默认分支。

若任一平台重命名失败，不删除旧仓库、不强推、不创建内容不同的替代主分支。保持已验证提交可恢复，并在继续前报告平台状态。

## 8. 测试与验收

先更新判别性测试，再修改生产与文档：

- README 第 1 行必须精确包含可复制提示词和 `pathway-atlas`。
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
2. README 第一行提示词可直接复制，且 Logo 在默认分支首屏显示。
3. GitHub 默认 `main` 和 Gitee 默认 `main` 展示同一内容树。
4. GitHub/Gitee 新仓库 URL 可访问，本地 remotes 指向新 URL。
5. 受影响测试、一次全量测试、compliance 与 release gate 均给出新鲜证据。
6. 未修改用户拥有的无关文件，未改变 GitHub 可见性。
