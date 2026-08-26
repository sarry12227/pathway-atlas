---
name: pathway-atlas
description: Use when a user needs evidence-backed Chinese gaokao admission planning, school recommendations, rank interpretation, or diversified admission pathways.
---

# 多元星图 · PathwayAtlas

按以下六个阶段完成一次匿名、证据优先的升学规划；每阶段达到所述交接条件后再继续。

## 信息采集

只采集会改变决策的字段，省份先问；未知值保持未知，选填项可以留空。

| 顺序 | 字段 | 边界 |
|---:|---|---|
| 1 | 省份 | 先问；未知时暂停省份相关分支 |
| 2 | 选科 | 可未知 |
| 3 | 学校全称 | 可未知 |
| 4 | 分数 | 可未知 |
| 5 | 年级排名 | 可未知 |
| 6 | 意向院校 | 选填 |
| 7 | 意向地区/城市 | 选填 |
| 8 | 意向专业 | 选填 |
| 9 | 港澳意愿 | 选填 |
| 10 | 奖项/活动 | 选填 |

拒绝收集学生姓名、电话、地址、班级、通信 ID、凭证或本地路径；画像、查询和输出只用匿名字段。加载并验证省份配置后，从 `ProvinceConfig.mode` 得到模式，用 canonical subject key 表示选科，不写死省份模式。

完成：匿名画像仅含上表字段，省份和未知项均已明确。

## 能力预检

选择当前 host 对应指南：[generic](references/hosts/generic.md)、[Codex](references/hosts/codex.md)、[Claude Code](references/hosts/claude-code.md) 或 [Kimi](references/hosts/kimi.md)。只把当前可调用的 search、browse、vision 传给 `python scripts/preflight.py`；`local_exec` 与 `file_output` 是单独记录的 workflow gates。

接受机器档位 `full`、`standard`、`offline`。能力损失只能降低 coverage，不能改变证据规则。退出码 `2` 表示无效输入或证据；退出码 `3` 表示缺少 DOCX/XLSX/PDF 等可选能力。

完成：保存 preflight 结果、workflow gates 与全部 degradation。

## 查询计划

用 `python scripts/query_plan.py` 构建并读取确定性计划，再按[检索流程](references/retrieval-playbook.md)逐任务执行。模式来自已验证的 `ProvinceConfig.mode`；每个 `QueryTask` 保持规范化 `subject_group`、`required_extraction_fields`、`availability`、`freshness` 与有界 `max_candidates`，不得另设固定 Top-N 候选数。

offline 仅消费已认证的用户提供本地材料，不声称当前或实时验证；没有静默联网回退。

完成：每个 task 都有计划规定的终止状态，或显式 unavailable 与 degradation。

## 证据归一化

HTML、XLSX、PDF、OCR、QR 分别进入匹配 adapter；下载只走 secure downloader。每项事实保存 year、method、locator、source provenance，并按[信源规范](references/source-policy.md)完成分级、独立性、去重、采纳与冲突处理；冲突不得取平均或挑选方便值。

普通批投档行只经 `scripts.adapters.admission_bridge` 组合 exact adapter row、对应 `QueryTask`、验证器返回的 `ValidatedAdmissionRow` 与 extraction coverage；整行绑定委托公共 `admission_row_hash`，并单独保留 `coverage_status`。

依次运行 `python scripts/validate_data.py` 和 `python scripts/validate_evidence.py`。形成 authenticated snapshot 之前不得给出数字或开始计算；按信源规范仍未达到采纳门槛时保留 `partial`、`conflict` 或 `missing`，不降低门槛。

完成：证据包已最终化且验证成功，或依赖该事实的输出明确 unavailable。

## 确定性计算

只消费 validated snapshots、validated dataset/config、规范化行、recommendation profile 与省份 policy/config；计算阶段不联网，也不使用内嵌默认值、固定位次偏移或 legacy adapter。

使用省份配置生成 3+3 或 3+1+2 canonical selection key；结果同时携带 evidence status 与 coverage。

完成：每个计算结果都能重放到已验证输入，缺失输入没有被估算替代。

## 报告输出

用共享证据入口 `python scripts/generate_report.py` 生成 Markdown；可选能力存在时用 `python scripts/docx_export.py` 生成 DOCX。DOCX 能力缺失时保留 Markdown 并返回退出码 `3`，不安装依赖或伪造文件。

报告逐项披露 `reference`、`inferred`、`partial`、`conflict`、`missing`、`masked`，以及 coverage、method、bounds。输出保持匿名、确定性、path-neutral，并以 exclusive/原子发布避免覆盖；不承诺录取或投资结果。用户明确授权之前，不发布、上传或 push 任何产物。

完成：已生成的文件通过合规检查并仅引用 snapshot，所有不可用项和降级均可见。
