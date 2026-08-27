# 多元星途 · PathwayAtlas 分发状态

本页记录 `pathway-atlas` 的公开主源、镜像与第三方目录状态。GitHub `main` 是唯一可信发布源；Gitee 只镜像同一提交树；第三方平台只引用主源或确定性发布包，不维护分叉版本。

状态只使用以下五种取值：`pending`（尚未提交或验证）、`submitted`（已提交，等待公开收录）、`indexed`（已直接验证公开详情页）、`rejected`（平台明确拒绝）、`unavailable`（平台或官方提交入口不可用）。`submitted` 不等同于已经公开收录。

| Platform | Official URL | Method | Version/Commit | Status | Listing URL | Last verified | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub | https://github.com/sarry12227/pathway-atlas | Public repository and release | — | pending | — | — | 待验证公开 `main`、首页首行提示词和发布包。 |
| Gitee | https://gitee.com/sarry1/pathway-atlas | Public mirror | — | pending | — | — | 待验证与 GitHub `main` 的提交树一致。 |
| SkillsMP | https://skillsmp.com | Public GitHub discovery | — | pending | — | — | 只记录可公开打开的详情页。 |
| skills.sh | https://skills.sh | `npx skills add` discovery | — | pending | — | — | 待从公开 GitHub 主源执行安装与检索。 |
| skills.homes | https://skills.homes | Public directory discovery | — | pending | — | — | 待确认官方收录或提交入口。 |
| skillhub.club | https://www.skillhub.club | GitHub import or platform submission | — | pending | — | — | 如遇登录或条款确认，由仓库所有者在平台完成。 |
| SkillHub.cn | https://skillhub.cn | GitHub import | — | pending | — | — | 如需登录、实名或条款确认，仅由仓库所有者在平台完成。 |
| SkillsCat | https://skills.cat | Public repository submission | — | pending | — | — | 优先提交 GitHub 仓库 URL，不上传分叉副本。 |
| ClawHub | https://hub.openclaw.ai | GitHub import or registry publish | — | pending | — | — | 如遇登录或所有权验证，由仓库所有者在平台完成。 |

## 核验原则

- 只有直接打开公开仓库或目录详情页，并核对名称、来源、版本与安装方式后，状态才能改为 `indexed`。
- 平台只收到 GitHub 主源 URL 或该主源生成的确定性发布包；不会接收不同内容的专用版本。
- 本页不保存账号、密码、验证码、实名材料、私有路径或后台截图。
- 发现平台展示内容与 GitHub `main` 不一致时，保持非 `indexed` 状态并停止传播该副本。
