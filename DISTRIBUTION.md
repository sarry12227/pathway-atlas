# 多元星途 · PathwayAtlas 分发状态

本页记录 `pathway-atlas` 的公开主源、镜像与第三方目录状态。GitHub `main` 是唯一可信发布源；Gitee 镜像同一提交。历史正式分发包固定到 `v0.1.0`（`5711107e45d3f679f60538d8e1904a465e887ccf`），不随 main 修改。平台安装包只做已列明的文件筛选与打包元数据适配，不维护不同的运行代码。

主源 `0.2.0` 默认先交付快速升学参考：用可比学校出口锚点估算省排并反查高考分数，固定本省冲3稳4保5、强基/综评/港澳各冲1稳1保1及行动安排，直接在聊天中展示。首轮研究有预算，旧证据与深度研究任务继续保留；真实缺额与未校准部分分别说明。此前 `0.1.7` 的多专业展示摘要修复、逐题问答、暖色书页与多条星途 Logo 均保留。完整介绍以 README 为准，部分平台展示 SKILL.md，两处均同步维护。下表逐行记录各平台最近一次单独核验的版本；条目已收录不表示缓存已刷新。腾讯兼容包使用平台版本 `1.1.0`，对应主源 `0.2.0`，需在已有条目中更新，独立技能图标也需单独上传。

状态只使用以下五种取值：`pending`（尚未提交或验证）、`submitted`（已提交，等待公开收录）、`indexed`（已直接验证公开详情页）、`rejected`（平台明确拒绝）、`unavailable`（平台或官方提交入口不可用）。`submitted` 不等同于已经公开收录；已有旧版本的条目也不代表本次更新已公开。

| Platform | Official URL | Method | Version/Commit | Status | Listing URL | Last verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub | https://github.com/sarry12227/pathway-atlas | Public repository and release | 0.2.0 / runtime 4015084 | indexed | https://github.com/sarry12227/pathway-atlas | 2026-09-11 | 快速规划与固定正文已推送；匿名回读SKILL、新规划模块、门面、快速规划说明及版本文件，均与功能提交逐字节一致。历史v0.1.0 Release保留原内容。 |
| Gitee | https://gitee.com/sarry1/pathway-atlas | Public mirror and tag | 0.2.0 / runtime 4015084 | indexed | https://gitee.com/sarry1/pathway-atlas | 2026-09-11 | 与GitHub相同功能提交；同一组5个公开文件匿名回读逐字节一致。镜像默认入口使用0.2.0，历史标签保留。 |
| SkillsMP | https://skillsmp.com | Public GitHub discovery | cached source | indexed | https://skillsmp.com/creators/sarry12227/pathway-atlas/skill | 2026-09-07 | 已核验公开名称、主源与安装方式；当前图片是作者头像，正文缓存尚未出现新版项目 Logo。官方自动采集，不提供手动上传。 |
| skills.sh | https://skills.sh | npx skills add discovery | cached source | indexed | https://www.skills.sh/sarry12227/pathway-atlas/pathway-atlas | 2026-09-07 | 公开详情存在；当前未展示项目图，配置无独立 Logo 字段。主源 SKILL 已加新版图，页面缓存尚未刷新。 |
| skills.homes | https://skills.homes | Official listing inquiry | v0.1.0 | pending | — | 2026-09-06 | 官方 contact 页面仅提供邮件收录咨询；申请草稿已准备，尚未发送。 |
| skillhub.club | https://www.skillhub.club | Official ClawHub synchronization | v0.1.0 | pending | — | 2026-09-06 | 官方说明同步 ClawHub。直接发布 CLI 的 100 KiB 单文件限制会跳过两个必要模块，因此等待完整上游收录。 |
| SkillHub.cn | https://skillhub.cn | Owner website version update | prepared 1.1.0 / source 0.2.0 | pending | https://skillhub.cn/skills/user_d9d3e443/pathway-atlas | 2026-09-11 | 1.1.0兼容包已完成字节核验，沿用用户自行上传方式，尚未提交本次更新。此前09-07回读详情为1.0.4、latest标签为1.0.5，不能当作本次公开版本。需在已有条目更新文件；独立图标另行上传。 |
| SkillsCat | https://skills.cat | Official repository submission | cached source | indexed | https://skills.cat/skills/sarry12227/pathway-atlas | 2026-09-07 | 本次官方 submit 返回条目已存在；公开详情仍为旧正文缓存、作者头像，尚未出现新版项目 Logo。 |
| ClawHub | https://clawhub.ai | Official registry publish | public latest 0.1.7 / submitted 0.2.0 | submitted | https://clawhub.ai/sarry12227/pathway-atlas | 2026-09-11 | 0.2.0的193文件完整上传，10:32（北京时间）单次最终发布获得ok回执，publicationStatus为pending；10:33匿名回读latest仍为0.1.7、0.2.0版本接口404，尚未确认公开。此前0.1.7现已可见。目录icon仍为空，额外MIT-0授权沿用版权所有者确认。 |

## 安装包与平台差异

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
