# 多元升学方案 Skill

一个开源的 AI Skill：在 Claude Code / Kimi Code 等兼容 Agent 中，免费生成专属多元升学方案——普通批冲稳保推荐 + 强基计划/综合评价/港澳院校路径 + 按年级的执行建议。

**公开版采用省份元数据与校验契约，不绑定湖北或其他单省真实数据；省份接入方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。**

## 设计原则

- **数据边界明确**：院校名、分数、位次仅由通过校验的数据与确定性脚本计算，AI（Agent）不得凭记忆补全任何院校数据
- **零第三方依赖**：核心功能仅需 Python 3.10+ 标准库；DOCX 导出为可选能力（python-docx）
- **Agent 宿主检索、本地确定性计算**：宿主负责实时搜索与浏览公开来源；进入计算和报告的事实必须先形成证据包并通过本地校验，脚本本身不补造数据
- **纯公益**：不含任何产品推介、价格、机构信息；方案经确定性合规扫描后才交付

## 安装

将本目录复制到 Agent 的 skills 目录（如 `~/.agents/skills/shengxue`），重启会话即可。对 Agent 说"帮我做升学方案"触发。

## 功能

| 能力 | 说明 |
|---|---|
| 分数直达查冲稳保 | 输入分数（自动反查省排名）或省排名 → 冲≤3/稳≤4/保≤5 院校名单 |
| 校排名估分（可选） | 出分前只有校排名时，仅以通过证据门槛的可比喜报锚点估算区间；证据不足或冲突时停止折算 |
| 多元路径推荐 | 强基/综评/港澳，等效位次修正；港澳意愿三态；限报政策提示 |
| 完整方案报告 | Markdown 四部分：成绩定位 / 院校推荐 / 多元路径 / 年级执行建议 |
| DOCX 导出（可选） | 从统一报告模型生成匿名、中性的 Word 方案 |

## 命令行用法（Agent 内部调用，用户无需记忆）

```bash
python scripts/preflight.py --host-capability search --host-capability browse
python scripts/validate_data.py tests/fixtures/provinces/demo-312
python scripts/validate_evidence.py tests/fixtures/evidence/three-source-consensus
python scripts/generate_report.py --dataset tests/fixtures/provinces/demo-312 --profile tests/fixtures/profiles/demo.json --evidence tests/fixtures/evidence/three-source-consensus
python scripts/docx_export.py --dataset tests/fixtures/provinces/demo-312 --profile tests/fixtures/profiles/demo.json --evidence tests/fixtures/evidence/three-source-consensus
```

## 公开包数据边界

- `tests/fixtures/provinces/fixture-policy.json` 必须枚举仓库内每套省份测试数据；v0.1 中所有条目均为纯虚构 synthetic fixture，不包含未经确认再分发权利的真实省份数据、学生报告或生成产物。
- 运行时数据需要单独获取、形成来源记录并通过校验；确认来源与再分发权利的真实快照可以作为独立授权的可选数据包发布，不由代码的 MIT 许可证自动覆盖。
- 本 Skill 不依赖同级私有仓库或机构内部系统。公开脚本可在独立仓库中运行，无法获得合格数据时必须明确降级，不得补造结果。

## 免责声明

本工具生成的方案仅为历史公开数据参考，**不构成任何录取承诺**；志愿填报与路径申报以省教育考试院及各高校官方发布的当年信息为准。

## 测试

```bash
python -m unittest discover -s tests -v
```

## 贡献

- 为新省份接入数据：[CONTRIBUTING.md](CONTRIBUTING.md)（四步：备数据 → 写配置 → 校验 → 自查）
- 问题与建议：GitHub Issues

## License

见 [LICENSE](LICENSE)。
