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

迁移必须让用户进入 GitHub 或 Gitee 默认分支时立即看到新品牌、Logo 和可复制调用提示词；不得要求用户切换分支或自行猜测 Skill 名称。GitHub 与 Gitee 最终均公开，GitHub 是唯一可信发布源，Gitee 是中国大陆公开镜像。

## 2. 品牌理念

Logo 在已选第 4 版的基础上渐进升级。保留五条路径、北大红、深靛蓝和证据金的识别基础，但将偏静态的闭合“星图/花瓣”结构改为向前延伸的多条星轨：不同起点可以汇合，也可以分流，沿途经过可核验的金色证据节点，共同朝向远方的引导星。它表达的是持续规划与行动过程，而不是一张替学生预设答案的静态地图。

品牌调性从“展示升学地图”转向“陪伴学生和家长看清多种可能、验证关键证据、走出个性路径”。文字与图形应兼具温度和可信度：不使用机械的 AI 光效、通用机器人符号、过度科技霓虹或学校官方标志；视觉重点是人的选择、路径的展开和证据带来的确定感。

标准色：

- 北大红：`#94070A`
- 深靛蓝：`#14213D`
- 证据金：`#C9A227`

不得使用北京大学标志、校名字体或其他受保护识别元素；这里只使用公开标准色值。正式资产以仓库 `assets/brand/` 下的 SVG 为语义源，PNG 仅作平台预览与兼容输出。

## 3. README 首屏

README 第 1 行必须是以下可直接复制给 AI 的提示词，位于标题、Logo、徽章和项目介绍之前。提示词模仿“一段话交给 AI 即可执行”的表达方式，但安装源是项目自己的 GitHub 与 Gitee 仓库，不依赖或上架任何第三方 Skill 商店：

> **复制给 AI：**「请使用你当前环境的 Skill 安装能力，从 GitHub `https://github.com/sarry12227/pathway-atlas` 安装 `pathway-atlas`（多元星途）；如果 GitHub 无法访问，请改用 Gitee 镜像 `https://gitee.com/sarry1/pathway-atlas`。若环境没有专用安装工具，请将仓库克隆或下载到当前 Agent 的 Skills 目录，确认根目录存在 `SKILL.md` 且其中 `name` 为 `pathway-atlas`，然后重新加载并调用它。调用后先询问我的省份、选科、分数或位次和升学意向；不要索取姓名、电话、身份证、住址或本地文件路径。请基于可验证的公开来源，先验证证据再计算，为我分析普通批冲稳保及适合的多元升学路径，并在每项建议旁标注来源、证据状态、覆盖范围和不确定性。」

README 随后展示横版 Logo、`多元星途 · PathwayAtlas` 标题、口号“点亮多种升学路径，走出个性升学星途。”与当前公开预览说明。安装示例统一使用 `pathway-atlas` 目录和 Skill 标识。

README 的安装说明应补充 GitHub 主源和 Gitee 镜像源的可检查命令示例，并说明不同 Agent 的 Skills 目录不同。安装完成必须核验目标目录、`SKILL.md` frontmatter、当前提交或版本，再重新加载 Agent；不得仅下载文件后就宣称 Skill 已可调用。GitHub 需要登录或访问受限时，AI 应自动切换到公开 Gitee 镜像，而不是要求非技术用户自行排查。

## 4. 迁移策略

采用一次性干净切换，不保留第二个可调用 Skill 名称或旧 Python distribution 别名：

1. 更新当前用户可见品牌、安装路径、命令提示、社区模板和安全说明。
2. 更新 `SKILL.md` frontmatter `name` 为 `pathway-atlas`。
3. 更新 `pyproject.toml` distribution 名称、可选依赖安装提示、下载 User-Agent、发布 ZIP 根目录和文件名。
4. 更新 Schema `$id`、CI/release workflow、release checker、release policy 与对应测试。
5. 更新 GitHub/Gitee 当前仓库 URL、Issue 模板链接、DATA_SOURCES 与安全入口。
6. 将 GitHub 和 Gitee 仓库重命名为 `pathway-atlas`，保持默认分支为 `main`；更新本地 remotes。
7. 在所有本地发布门禁通过后，将 GitHub 仓库从私有改为公开；Gitee 保持公开。
8. 以 GitHub 的已验证版本为唯一可信源，向 Skill 发现与推荐平台分发；平台副本不得产生独立代码分支或不同版本内容。

不采用以下方案：

- 只改展示名：会让 Skill、安装目录与发布包继续暴露旧工程名。
- 长期双别名：项目尚未正式发布，兼容层会制造两个品牌入口并增加维护成本。
- 为每个平台维护独立 ZIP 或手工改写版：会造成版本、许可证、安全修复和安装说明漂移。

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

## 7. 多平台分发

分发采用“GitHub 唯一可信源 + Gitee 公开镜像 + 平台索引或认领”的方式。首发覆盖：

| 平台 | 角色 | 预期方式 | 登录或授权 |
| --- | --- | --- | --- |
| GitHub | 唯一可信发布源 | 公开 `main`、GitHub Skill 校验与版本 Release | 已登录；公开可见性已获用户授权 |
| Gitee | 中国大陆公开镜像 | 同步 GitHub 已验证内容树与 Release 信息 | 已登录 |
| SkillsMP | 发现目录 | 等待或触发公开 GitHub `SKILL.md` 索引，核验详情页 | 通常不需要 |
| skills.sh | 安装与排行 | 使用 `npx skills add sarry12227/pathway-atlas` 安装并核验目录页 | 通常不需要；若要求提交则使用 GitHub 身份 |
| skills.homes | 能力目录 | 核验自动索引；未收录时仅使用其官方联系或提交入口 | 视平台要求 |
| skillhub.club | 发现与安装目录 | 先核验 GitHub 自动索引；需要时登录认领或上传同一发布包 | 可能需要登录 |
| SkillHub.cn | 国内 Skill 平台 | 从 GitHub 导入同一发布版本并完成平台审核 | 需要登录、GitHub 绑定或实名认证时由用户亲自完成 |
| SkillsCat | 补充分发目录 | 通过官方网页或 CLI 提交 GitHub 仓库 URL | 视平台要求 |
| ClawHub | OpenClaw 分发 | 使用官方 CLI 发布同一版本并核验安装 | 需要 CLI 登录 |

若实施期间发现其他仍活跃的 Skill 推荐网站，只有在满足以下条件时才纳入：有明确官方提交或自动索引机制；接受项目 MIT 许可证；允许回链唯一源码；不要求上传密钥、私人材料或创建内容不同的副本。仅有搜索聚合页、失效站点或无法确认运营方的站点不宣称已分发。

仓库新增公开分发状态清单，记录平台、官方 URL、提交方式、版本或提交哈希、状态、公开详情页、最后验证日期和限制。状态只能使用 `pending`、`submitted`、`indexed`、`rejected`、`unavailable`；未直接验证不得写成 `indexed`。

任何登录、实名认证、验证码或服务条款确认均由用户在平台页面亲自完成。Agent 不索取密码、验证码、身份证件或认证截图；只在操作到达阻塞点时说明平台、所需动作和继续后的影响。

## 8. 外部仓库切换

本地迁移提交通过测试后执行：

1. 推送 `codex/` 分支到 GitHub，并通过 PR 合入默认 `main`。
2. 在 GitHub 将仓库重命名为 `pathway-atlas`，更新 description、topics、README/social preview，将仓库改为公开，并确认默认分支仍为 `main`。
3. 将同一内容树快进同步到 Gitee `main`，再将 Gitee 仓库重命名为 `pathway-atlas`，更新简介和 Logo；确认公开访问与默认分支。
4. 更新本地 `origin`、`gitee` URL，核对两个远端默认分支的 tree hash 一致。
5. 在两个独立临时 Skills 目录中分别按 README 提示词从 GitHub 和 Gitee 安装，核验根目录、`SKILL.md` 标识和内容树；GitHub 因权限不可匿名访问时如实记录，并确认 Gitee 公共回退可用。
6. 分别以默认入口检查仓库首页、README 第一行提示词、Logo 和默认分支。
7. 按第 7 节矩阵逐个平台自动索引、提交或认领；每次操作都更新分发状态清单，并核验详情页安装来源仍指向唯一可信版本。

若任一平台重命名失败，不删除旧仓库、不强推、不创建内容不同的替代主分支。保持已验证提交可恢复，并在继续前报告平台状态。

## 9. 测试与验收

先更新判别性测试，再修改生产与文档：

- README 第 1 行必须精确包含 GitHub 主源、Gitee 镜像源、安装与重新加载步骤、可复制调用提示词、`pathway-atlas` 和“多元星途”。
- 在临时 Skills 目录中按 README 提示词安装后，Agent 必须能发现 `pathway-atlas`；安装目录错误、缺少 `SKILL.md` 或 frontmatter 名称不符都必须失败。
- 当前用户可见表面不得继续展示“多元星图”；历史品牌设计提交不重写。
- `SKILL.md`、`pyproject.toml`、release builder/checker/workflow 必须使用新标识。
- 当前公共表面不得出现旧标识；历史计划目录使用显式排除清单。
- Logo SVG 解析、PNG alpha、README 相对链接必须通过。
- 发布包文件名和 ZIP 根目录必须为 `pathway-atlas-<version>.zip` / `pathway-atlas/`。
- GitHub/Gitee 当前 URL、默认分支和 README 首页必须可访问。
- GitHub 必须可匿名访问；Gitee 必须保持公开；两个默认分支的内容树必须一致。
- 每个写为 `indexed` 的第三方平台都必须有当日直接验证的公开详情页或 API 证据；平台安装不得生成与 GitHub 发布树不同的 Skill 内容。
- 运行受影响测试后，只运行一次新的 all-extras 全量测试；不复用旧的 761 项结论，也不无意义重复同一全量门禁。
- tracked compliance、release rehearsal、diff check、敏感信息扫描和 deterministic build 必须通过。

## 10. 完成标准

只有同时满足以下条件才宣告迁移完成：

1. 本地当前公共接口和发布产物统一为 `pathway-atlas`。
2. README 第一行提示词可直接复制，且“多元星途”Logo 与口号在默认分支首屏显示。
3. GitHub 与 Gitee 均公开，默认 `main` 展示同一内容树。
4. GitHub/Gitee 新仓库 URL 可访问，本地 remotes 指向新 URL。
5. GitHub 主源和 Gitee 镜像源均能按 README 提示词安装 `pathway-atlas`；GitHub 权限受限时，Gitee 公共回退仍可完成安装与调用。
6. 受影响测试、一次全量测试、compliance 与 release gate 均给出新鲜证据。
7. 首发平台矩阵均有可审计状态；自动索引仍在等待或平台审核未完成时如实报告，不伪造“全网已收录”。
8. 未修改用户拥有的无关文件；GitHub 仅按本规格从私有改为公开，不改变其他安全设置。
