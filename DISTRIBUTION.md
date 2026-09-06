# 多元星途 · PathwayAtlas 分发状态

本页记录 `pathway-atlas` 的公开主源、镜像与第三方目录状态。GitHub `main` 是唯一可信发布源；Gitee 镜像同一提交。正式分发包固定到 `v0.1.0`（`5711107e45d3f679f60538d8e1904a465e887ccf`）。平台安装包只做已列明的文件筛选与打包元数据适配，不维护不同的运行代码。

状态只使用以下五种取值：`pending`（尚未提交或验证）、`submitted`（已提交，等待公开收录）、`indexed`（已直接验证公开详情页）、`rejected`（平台明确拒绝）、`unavailable`（平台或官方提交入口不可用）。`submitted` 不等同于已经公开收录；已有旧版本的条目也不代表本次更新已公开。

| Platform | Official URL | Method | Version/Commit | Status | Listing URL | Last verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub | https://github.com/sarry12227/pathway-atlas | Public repository and release | v0.1.0 / main | indexed | https://github.com/sarry12227/pathway-atlas/releases/tag/v0.1.0 | 2026-09-06 | 默认 main 已同步；Release 可下载，下载回读 SHA-256 与已验收原包一致。 |
| Gitee | https://gitee.com/sarry1/pathway-atlas | Public mirror and tag | v0.1.0 / main | indexed | https://gitee.com/sarry1/pathway-atlas | 2026-09-06 | main 与 GitHub 同步，v0.1.0 标签一致；未单独上传 Gitee Release 附件。 |
| SkillsMP | https://skillsmp.com | Public GitHub discovery | v0.1.0 | pending | — | 2026-09-06 | 官方说明采集公开 GitHub SKILL.md；尚未检索到本项目，无已验证的手动上传入口。 |
| skills.sh | https://skills.sh | npx skills add discovery | v0.1.0 / 5711107 | indexed | https://www.skills.sh/sarry12227/pathway-atlas/pathway-atlas | 2026-09-06 | 从主源完成真实安装，191 个文件逐字节一致，公开详情页已验证。 |
| skills.homes | https://skills.homes | Official listing inquiry | v0.1.0 | pending | — | 2026-09-06 | 官方 contact 页面仅提供邮件收录咨询；申请草稿已准备，尚未发送。 |
| skillhub.club | https://www.skillhub.club | Official ClawHub synchronization | v0.1.0 | pending | — | 2026-09-06 | 官方说明同步 ClawHub。直接发布 CLI 的 100 KiB 单文件限制会跳过两个必要模块，因此等待完整上游收录。 |
| SkillHub.cn | https://skillhub.cn | Owner website version update | 1.0.1 (source v0.1.0) | submitted | https://skillhub.cn/skills/user_d9d3e443/pathway-atlas | 2026-09-06 | 所有者确认上传成功，API 已记录 latest 标签 1.0.1；公开文件接口尚未提供该版，当前仍返回旧版。 |
| SkillsCat | https://skills.cat | Official repository submission | v0.1.0 / 5711107 | indexed | https://skills.cat/skills/sarry12227/pathway-atlas | 2026-09-06 | 官方提交已受理；公开搜索、详情页与 registry 均已验证，SKILL.md 内容与交付包完全一致。 |
| ClawHub | https://clawhub.ai | Official CLI registry publish | v0.1.0 / 5711107 | submitted | — | 2026-09-06 | 注册表确认同版本、同 177 文件指纹已经存在；verify 返回 pending.publication，待平台安全审核。已获版权所有者对 ClawHub 的额外 MIT-0 授权。 |

## 安装包与平台差异

- GitHub Release 原包包含 191 个文件，SHA-256 为 `91c6f89ba78b232df3f4e0714a6ee2b619b2acffa8c84a47990acb145fa4dee6`。
- 腾讯 SkillHub 使用 `pathway-atlas-skillhub-1.0.1-runtime.zip`，包含 87 个文件。其 43 个 scripts 文件、11 个 schemas 文件、9 个 references 文件及 SKILL.md 与原包逐字节一致。排除 Git 配置、隐藏开发目录、测试目录和 PNG；保留平台支持的 SVG。许可证原文改名为 LICENSE.txt，pyproject.toml 只调整对应许可证路径。SHA-256 为 `31370ea40d25b0573a8ceea0737b9c1768dbb47c2731f55cf065eb22c2cd69a3`。
- 腾讯已有平台版本 1.0.0，本次以 1.0.1 更新；它对应主源 v0.1.0，不代表另一套产品版本。
- ClawHub 官方 CLI 收集 177 个文件，仅排除 14 个 Git 配置与隐藏开发目录文件；每个保留文件与原包一致。指纹为 `a401a05512ea5ef41f543ffb7ad18acc160b3e002394400aed1b5f31713cb734`。其额外 MIT-0 授权仅针对该平台，主源许可证继续为 MIT。
- main 在发布后修正了测试临时目录的跨系统兼容性，并把 CI 与发布任务的有限超时调整为 90 分钟。该变更不改变运行代码，也不改写 v0.1.0 标签或原发布附件。

## 核验原则

- indexed 必须有可公开访问的详情页、名称、来源及安装方式的直接验证；提交成功、登录成功与审核通过分别记录。
- 平台筛选后的运行文件必须逐字节等于固定主源；只允许记录在本页的包装差异。完整开发与测试内容以 GitHub/Gitee 为准。
- 本页不保存登录凭据、验证码、实名材料、私有路径或后台截图。
- 平台新版本尚未公开时保留 submitted；不以旧版详情页或 latest 标签替代新版下载验证。
