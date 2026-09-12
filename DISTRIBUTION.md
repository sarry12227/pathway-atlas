# 多元星途 · PathwayAtlas 分发状态

本页记录 `pathway-atlas` 的公开主源、镜像与第三方目录状态。GitHub `main` 是唯一可信发布源；Gitee 镜像同一提交。历史正式分发包固定到 `v0.1.0`（`5711107e45d3f679f60538d8e1904a465e887ccf`），不随 main 修改。平台安装包只做已列明的文件筛选与打包元数据适配，不维护不同的运行代码。

主源 `0.2.5` 开场直接问孩子的情况；必要加载、调试、测试与已授权本地维护在等待回答时静默进行，仅在确实需要本人操作时打断，准备完成后直接衔接，无需再选择是否开跑。无后台能力时分轮处理，不虚报任务。此前学校与联考划线收集继续保留，支持图片或逐轮文字，按可比线差或相邻点插值先估高考参考分数、再查省排；无读图能力或无可比划线时回退文字与喜报。全国院校和路径层次统一参照，各省分数分别校准，同层保留适配与备选。强基、综评比较面向生源省的全国候选，港澳同层香港优先。普通批继续优先本省和已选专业。没有可比划线时使用网上喜报进行快速参考：单份可读第三方文章、抖音分享或学校发布的分数段人数、上线率和层次人数均可形成披露出处与假设的校位锚点，估算省排并反查高考分数。固定本省冲3稳4保5、强基/综评/港澳各冲1稳1保1及行动安排，实际运行 `brief` 后直接在聊天中展示。首轮研究有预算，旧证据与深度研究任务继续保留；真实缺额与未校准部分分别说明。此前多专业摘要修复、逐题问答和暖色书页星途 Logo 均保留。完整介绍以 README 为准，部分平台展示 SKILL.md，两处均同步维护。下表逐行记录各平台最近一次单独核验的版本；条目已收录不表示缓存已刷新。腾讯兼容包使用平台版本 `1.1.5`，对应主源 `0.2.5`，需在已有条目中更新，独立技能图标也需单独上传。

状态只使用以下五种取值：`pending`（尚未提交或验证）、`submitted`（已提交，等待公开收录）、`indexed`（已直接验证公开详情页）、`rejected`（平台明确拒绝）、`unavailable`（平台或官方提交入口不可用）。`submitted` 不等同于已经公开收录；已有旧版本的条目也不代表本次更新已公开。

| Platform | Official URL | Method | Version/Commit | Status | Listing URL | Last verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub | https://github.com/sarry12227/pathway-atlas | Public repository and release | 0.2.5 / source 805e523 | indexed | https://github.com/sarry12227/pathway-atlas | 2026-09-12 | 直接提问、静默准备与自动衔接已推送；匿名回读11个关键文件与源提交逐字节一致。历史v0.1.0 Release保留原内容。 |
| Gitee | https://gitee.com/sarry1/pathway-atlas | Public mirror and tag | 0.2.5 / source 805e523 | indexed | https://gitee.com/sarry1/pathway-atlas | 2026-09-12 | 与GitHub同一源提交；匿名回读11个关键文件逐字节一致。 |
| SkillsMP | https://skillsmp.com | Public GitHub discovery | cached source | indexed | https://skillsmp.com/creators/sarry12227/pathway-atlas/skill | 2026-09-07 | 已核验公开名称、主源与安装方式；当前图片是作者头像，正文缓存尚未出现新版项目 Logo。官方自动采集，不提供手动上传。 |
| skills.sh | https://skills.sh | npx skills add discovery | cached source | indexed | https://www.skills.sh/sarry12227/pathway-atlas/pathway-atlas | 2026-09-07 | 公开详情存在；当前未展示项目图，配置无独立 Logo 字段。主源 SKILL 已加新版图，页面缓存尚未刷新。 |
| skills.homes | https://skills.homes | Official listing inquiry | v0.1.0 | pending | — | 2026-09-06 | 官方 contact 页面仅提供邮件收录咨询；申请草稿已准备，尚未发送。 |
| skillhub.club | https://www.skillhub.club | Official ClawHub synchronization | v0.1.0 | pending | — | 2026-09-06 | 官方说明同步 ClawHub。直接发布 CLI 的 100 KiB 单文件限制会跳过两个必要模块，因此等待完整上游收录。 |
| SkillHub.cn | https://skillhub.cn | Owner website version update | prepared 1.1.5 / source 0.2.5 | pending | https://skillhub.cn/skills/user_d9d3e443/pathway-atlas | 2026-09-12 | 1.1.5兼容包已完成字节核验，沿用用户自行上传方式，尚未提交本次更新。 |
| SkillsCat | https://skills.cat | Official repository submission | cached source | indexed | https://skills.cat/skills/sarry12227/pathway-atlas | 2026-09-07 | 本次官方 submit 返回条目已存在；公开详情仍为旧正文缓存、作者头像，尚未出现新版项目 Logo。 |
| ClawHub | https://clawhub.ai | Official registry publish | public latest 0.2.4 / target 0.2.5 | submitted | https://clawhub.ai/sarry12227/pathway-atlas | 2026-09-12 | 204文件包已获单次最终发布成功回执；尚未验证0.2.5公开，不重复提交。额外MIT-0授权沿用版权所有者确认。 |

## 安装包与平台差异

直接咨询与静默准备包固定到 `805e5236e48b4229ce8f303b80a765639762bc37`（主源 `0.2.5`）；用户真实聊天记录保留在私有工作区，不进入公开包：

- `pathway-atlas-clawhub-0.2.5.zip`：204文件，SHA-256为`f3a734de83aca424738dd2e36866cc7d4cb7a80d6c3de4765c9389e6d812603e`。官方文件指纹为`a1e085a94a028577ce7164205715ad4592b1e0652c2f3d66d5a5332764f8543c`。
- `pathway-atlas-skillhub-1.1.5-runtime.zip`：100文件，SHA-256为`6309e3fe2f8125319a1bc4f2f126d10f1016bda73db6ef2d2d41b12784d081c1`。78个运行、配置、参考及Skill入口文件与源逐字节相同，仅沿用下述腾讯包装适配。

本次为入口与文档调整，66项相关契约检查通过，9种独立模型首轮回复取样符合流程；全仓链接与发布文件扫描通过。数值运行代码和schema与0.2.4相同，本次未重跑全量数值测试；首轮取样不等同于真实安装或完整咨询全流程验证。

划线校准与全国院校顺序包固定到 `1ca3ea32324868f5b4676a3e643457fd1701e5cb`（主源 `0.2.4`）；用户原图、参考PDF和真实咨询记录保留在私有工作区，不进入公开包：

- `pathway-atlas-clawhub-0.2.4.zip`：204文件，SHA-256为`24cbfd611d245f0fb4d255ce7734470ea761a5ec61d36b25f4a6e747fd591a8e`。官方文件指纹为`7b9889c69f974d679957297258971af79e785664456bc43f6a5649fbf3aece01`。
- `pathway-atlas-skillhub-1.1.4-runtime.zip`：100文件，SHA-256为`2db7e7012047512200347237da803c42e4ec0b6f2752a453da8090a820251f62`。78个运行、配置、参考及Skill入口文件与源逐字节相同，仅沿用下述腾讯包装适配。

全国路径比较包固定到 `965f7e4e397c9586d14117f5675e656d2e175d6f`（主源 `0.2.2`），后续仅更新本分发记录不改变包内运行源：

- `pathway-atlas-clawhub-0.2.2.zip`：198文件，SHA-256为`46d85c1ec1f4f115a51bd028bc4ea84ce6c7acdb3a070472016e7f6f93ec8556`，全部保留文件逐字节等于源提交。官方文件指纹为`6e5c2f31d4b95b63318458e4f4439bdeb909975920fda61f8ebf796894e160eb`。
- `pathway-atlas-skillhub-1.1.2-runtime.zip`：96文件，SHA-256为`bf282d0bc8a98e6fa6f8365fa0d728e999491b132d26f70ab60694e8fb91ce39`。只沿用下述腾讯包装适配；74个运行、配置、参考及Skill入口文件与0.2.2源逐字节相同。SKILL入口SHA-256为`445d37733842970c89f6d6d6f03d3df97fa03faa79ca7c3e75546a49ef7e0b0f`。

喜报参考包固定到 `6d872a8d22deae5117dadd2430616e871f649fc8`（主源 `0.2.1`），后续仅更新本分发记录不改变包内运行源：

- `pathway-atlas-clawhub-0.2.1.zip`：196文件，SHA-256为`dc512bd3944bd897dd90a153cd55fa54111a32ef398c241782afb0b3bc62f056`，全部保留文件逐字节等于源提交。官方文件指纹为`6f536c7f3f8457f1238d58deda84514be9852b9eb79f20aba67da2bdb7db8494`。
- `pathway-atlas-skillhub-1.1.1-runtime.zip`：95文件，SHA-256为`89d600470c138a6e16d6ab268043d57cb0bccff7a2e6465fb4f5d5dfa768670a`。只沿用下述腾讯包装适配；73个运行、配置、参考及Skill入口文件与0.2.1源逐字节相同。SKILL入口SHA-256为`bf2390c0e4259a836dbd9f73e92a33cf81883fbbccca69246c1ae29742cccb8b`。

快速规划包固定到 `40150845bfa785ebd45831421aa3410289390ecc`（主源 `0.2.0`），后续仅更新本分发记录不改变包内运行源：

- `pathway-atlas-clawhub-0.2.0.zip`：193文件，SHA-256为`10a80a8bca76f1cf8488deb6e2cad43fcdc131fa4b1a29cfd77cd4496c736500`，全部保留文件逐字节等于源提交。官方文件指纹为`d47599948210c599b479a42982dbf13d5e41d83731b42cf3061952de84cb2999`。
- `pathway-atlas-skillhub-1.1.0-runtime.zip`：93文件，SHA-256为`932484cbb6d56ea8d40a8c701e48a334f1b5c638e4761f1cd4e35435b18a1149`。只沿用下述腾讯包装适配；71个运行、配置、参考及Skill入口文件与0.2.0源逐字节相同。SKILL入口SHA-256为`3ea27a973c91ab6aac4a34ffd50b5c1d9972e6548e0840925a66c3409826aa3a`。

以下为历史更新包记录。

摘要修复包固定到 `76be5b279e848d5d8da3b7259461a1263c8c16c8`：

- `pathway-atlas-clawhub-0.1.7.zip`：190文件，SHA-256为`61dde8ba506c5531da9581986683004fbc280cb2143957ad9102697da4d970d4`，全部保留文件逐字节等于源提交。
- `pathway-atlas-skillhub-1.0.8-runtime.zip`：91文件，SHA-256为`a20d94551c446b4eea5a85356119d897defd00996cb4963c3d233e1fe5c145c6`。只沿用下述腾讯包装适配；69个运行、配置、参考及Skill入口文件与修复源逐字节相同。

品牌首次更新源为 `fc7cec4b52ee21764fc0f221805ee57a354067a1`。完整 PNG 的 SHA-256 为 `ec14043003e517138e19b97f2d46c186af7cf6afd01bc1079358557e0429b2c7`；ClawHub 目录图标采用102562字节的 WebP 压缩副本，由 `agents/openai.yaml` 指定。以下品牌包固定到该提交；0.1.7修复包沿用同样的筛选和运行文件逐字节核验规则。

- `pathway-atlas-clawhub-0.1.6.zip` 含189文件，所有保留文件与品牌源逐字节一致，含原 PNG、WebP 与图标配置。69个运行、配置、参考及 Skill 入口文件保持源文件原样。
- `pathway-atlas-skillhub-1.0.7-runtime.zip` 含91个兼容文件，同一组69个运行与入口文件保持原样。排除测试、隐藏开发目录、PNG/WebP及只用于目录图标的配置；将README与品牌说明中的图片地址改为公开源地址，LICENSE改名及pyproject路径适配沿用此前规则。平台独立图标需另行上传。

以下为历史首发记录：

- GitHub Release 原包包含 191 个文件，SHA-256 为 `91c6f89ba78b232df3f4e0714a6ee2b619b2acffa8c84a47990acb145fa4dee6`。
- 腾讯 SkillHub 使用 `pathway-atlas-skillhub-1.0.1-runtime.zip`，包含 87 个文件。其 43 个 scripts 文件、11 个 schemas 文件、9 个 references 文件及 SKILL.md 与原包逐字节一致。排除 Git 配置、隐藏开发目录、测试目录和 PNG；保留平台支持的 SVG。许可证原文改名为 LICENSE.txt，pyproject.toml 只调整对应许可证路径。SHA-256 为 `31370ea40d25b0573a8ceea0737b9c1768dbb47c2731f55cf065eb22c2cd69a3`。
- 腾讯已有平台版本 1.0.0，本次以 1.0.1 更新；它对应主源 v0.1.0，不代表另一套产品版本。
- ClawHub 官方 CLI 收集 177 个文件，仅排除 14 个 Git 配置与隐藏开发目录文件；每个保留文件与原包一致。指纹为 `a401a05512ea5ef41f543ffb7ad18acc160b3e002394400aed1b5f31713cb734`。其额外 MIT-0 授权仅针对该平台，主源许可证继续为 MIT。
- main 在发布后修正了测试临时目录的跨系统兼容性，并把 CI 与发布任务的有限超时调整为 90 分钟。该变更不改变运行代码，也不改写 v0.1.0 标签或原发布附件。

## 核验原则

完整介绍、短介绍及各页面的文案来源见[平台介绍文案](docs/platform-introductions.md)。skills.sh 的仓库分组由根 `skills.sh.json` 提供，详情页仍使用 SKILL.md；第三方页面需要另行核对缓存刷新。

- indexed 必须有可公开访问的详情页、名称、来源及安装方式的直接验证；提交成功、登录成功与审核通过分别记录。
- 平台筛选后的运行文件必须逐字节等于固定主源；只允许记录在本页的包装差异。完整开发与测试内容以 GitHub/Gitee 为准。
- 本页不保存登录凭据、验证码、实名材料、私有路径或后台截图。
- 平台新版本尚未公开时保留 submitted；不以旧版详情页或 latest 标签替代新版下载验证。
