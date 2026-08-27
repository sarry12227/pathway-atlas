# 贡献指南

感谢你帮助改进多元星途（PathwayAtlas）。请先阅读 [README](README.md)、
[信源规范](references/source-policy.md) 和 [数据来源政策](DATA_SOURCES.md)。本项目接受
代码、文档、虚构回放、来源目录和省份规则改进；默认不接受真实数据快照。

## 开始前

- 从一个范围清楚的问题开始，说明预期行为、证据边界和降级结果。
- 不提交真实学生信息、报告、凭证、营销材料、私有系统导出或未经确认授权的数据。
- 测试 fixture 必须是虚构测试数据，或附有维护者可核验的明确再分发许可；虚构内容要使用
  `.test` 域名、匿名标识和不对应真实个体的数值。
- 不绕过登录墙、验证码、访问控制、付费墙或站点限制。
- 安全问题按 [安全政策](SECURITY.md) 私下报告，不要用公开 Issue 披露。

## 证据与数据贡献

来源目录、结构化事实或导入规则的 Pull Request 必须同时给出：

1. 每个来源稳定且不含个人信息的 source ID；
2. 发布者、原始 URL、适用年份、检索时间、内容哈希和引用链；
3. 每个事实到 source ID 的字段级绑定，以及提取方法和逻辑 locator；
4. 独立性、冲突、覆盖范围和时效判断；
5. 一份数据权利声明，说明贡献者为何有权提交、修改并允许本仓库再分发这些内容。

只有“网页公开可见”不等于获得再分发权。权利不清楚时，只贡献 URL、最小结构化事实、
内容哈希和提取工具，不复制网页、附件或完整表格。A/B/C 分级与采纳门槛以
[信源规范](references/source-policy.md) 为准，不在 Pull Request 中自定义更低门槛。

## TDD 工作流

所有行为变更都使用 TDD：先写失败测试并运行它，确认失败来自缺失行为；再写最小实现使其
通过；最后在保持绿色的前提下重构。修复缺陷时，测试必须先复现原始症状。测试应使用真实
代码和虚构输入，不能用只验证 mock 或源文本没有变化的断言代替行为验证。

常用检查：

```bash
python -m unittest discover -s tests -v
python scripts/validate_data.py tests/fixtures/provinces/demo-312
python scripts/validate_data.py tests/fixtures/provinces/demo-33
python scripts/validate_evidence.py tests/fixtures/evidence/three-source-consensus
```

如果更改可选 XLSX、PDF 或 DOCX 路径，还要安装对应 extra，并确认相关测试没有被全部跳过。

## Pull Request 清单

- [ ] 测试经历了可解释的 RED、GREEN，并在说明中记录命令与结果。
- [ ] 新增 fixture 是虚构测试数据；任何例外都有逐项数据权利声明与再分发审查。
- [ ] 新增事实包含 source ID、字段级来源、适用年份、URL、检索时间和内容哈希。
- [ ] 文档、Schema、示例和行为同步更新，所有相对 Markdown 链接可解析。
- [ ] 已运行完整测试、受影响的 validator、隐私/许可扫描以及当前分支提供的全部发布检查。
- [ ] 没有真实学生信息、凭证、营销内容、本地绝对路径、生成报告或原始下载进入变更。

发布分支必须运行仓库提供的全部 release checks。`scripts/release_check.py` 纳入分支后，执行
`python scripts/release_check.py --expected-version 0.1.0`；门禁脚本尚未存在或未通过时，
发布被阻止而不是豁免。维护者会按正确性、证据质量、数据权利、隐私和可重放性评审变更。

参与社区时还须遵守 [行为准则](CODE_OF_CONDUCT.md)。
