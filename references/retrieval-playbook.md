# 可复现检索流程

## 统一规范入口

所有 publisher tier、independence、deduplication、admission、conflict 和 evidence-state 决策只使用[信源规范](source-policy.md)。本流程只规定执行顺序、有限边界和完成标准；能力损失改变 coverage，不改变 truth rules。

公开命令探针如下；先确认 seam 存在，再按当前任务提供经过验证的参数。

```text
$ python -m scripts.preflight --help
$ python -m scripts.query_plan --help
$ python -m scripts.validate_evidence --help
```

## 1. 能力预检

在任何 discovery 前调用 `scripts.preflight`。只传入运行时声明的 host capability，并让 preflight 探测有限的 optional modules；使用返回的机器档位，不另造能力名或档位别名。

| 类型 | 有限值 |
|---|---|
| host_capabilities | search,browse,vision |
| optional_modules | docx,openpyxl,pdfplumber |
| capability_tier | full,standard,offline |

完成标准：机器 capability report 已存在，档位与每项 degradation 都有显式值。

## 2. 构建并读取确定性查询计划

先加载经过验证的 `ProvinceConfig.mode`，再调用 `scripts.query_plan` 生成 deterministic query plan。逐个独立处理 `task_id`，读取并保持任务声明的 `province`、`subject_group`、`year`、`kind`、`required_extraction_fields` 与 `max_candidates`；不合并不同事实、年份、省份或 subject context。当前年份 availability 以计划结果为准。

完成标准：每个 task_id 已排入独立队列，或以明确 unavailable reason 结束。

## 3. 开始检索并枚举候选

只在步骤 1–2 完成后开始 discovery。依照统一规范入口给出的检索优先级枚举实际可访问候选直到任务上限；首个看似可信页面只进入候选集，不结束枚举。每次失败的 network action 执行一次初始尝试，最多再 retry 一次，然后该 branch 降级或停止。

| 控制项 | 值 | 完成记录 |
|---|---:|---|
| candidate-cap | 10 | 保存实际考察数量与达到上限与否 |
| retry-per-network-action | 1 | 保存 initial attempt 与 retry 的结果 |
| first-plausible-stop | 禁止 | 保存候选枚举继续或停止的理由 |

完成标准：该 task 的候选不超过 10 个，所有 network action 已在 retry 边界内结束，并记录枚举结果。

## 4. 分类并去重

调用 `scripts.source_policy.py` 的 canonicalization 与 `deduplicate_candidates`；本流程不重述或重算规范中的独立性判定。

| 输入状态 | 动作 | 输出状态 |
|---|---|---|
| candidates-enumerated | deduplicate_candidates | independence-components |

完成标准：每个候选恰好处于 kept-once 或 rejected-with-stable-reason 状态。

## 5. 通过匹配适配器提取

按媒体类型选择 HTML、spreadsheet、PDF、OCR-row 或 QR adapter。保存字段级 `page/sheet/table/row` 或 `page/image/bbox` locator、coverage 和 ordered warnings。QR 输入是 host-decoded text，并只通过 secure downloader 获取目标；adapter 不自行上传图片或创建隐藏识别路径。

完成标准：每个 kept candidate 都产生显式 extraction coverage/warnings，或一个受控 missing result。

## 6. 证据采纳

调用 `scripts.source_policy.py` 得到规范结果，并把该结果连同 candidates 与 rejections 通过 `EvidenceStore` 持久化；本流程不解释或改写采纳判定。

| 输入状态 | 动作 | 输出状态 |
|---|---|---|
| extraction-results | persist-source-policy-result | EvidenceStore-persisted |

完成标准：每个 required field 恰好具有 accepted、partial/masked、conflict 或 missing 状态。

## 7. 最终化并验证证据

最终化 `EvidenceStore`，然后运行 `scripts.validate_evidence`。validator 成功后才接受 authenticated immutable evidence snapshot；任何 validation failure 都停止依赖该事实的 calculation，并把 dependent output 标成 unavailable。

| 输入状态 | 动作 | 输出状态 |
|---|---|---|
| EvidenceStore-persisted | finalize-then-validate | authenticated-snapshot |

完成标准：authenticated immutable evidence snapshot 已存在，或 dependent output 已显式 unavailable；不存在未验证的计算输入。

## 8. 为每个查询任务停止

每个 task 只允许以下四种 stop condition。记录 `停止 ID`，并保持原 fact/year/province；不无限循环，也不静默扩大问题。

| 停止 ID | 触发条件 | 记录 |
|---|---|---|
| accepted | qualifying fact 已按统一规范入口采纳 | fact ID 与状态 |
| candidate-cap | 已考察候选达到 10 | 实际候选数与未覆盖项 |
| variants-exhausted | 所有 query variants 及各自 single retry 已耗尽 | variants 与失败原因 |
| unavailable | capability 或 authority availability 使任务不可执行 | unavailable reason 与 degradation |

完成标准：每个 task_id 恰好记录一个停止 ID；没有仍在运行或被静默扩大的分支。

## 9. 交接确定性引擎与报告

只把 validated snapshots 与已声明 datasets/config 交给 deterministic engine/report。Markdown 与可选 DOCX 显示 `reference`、`inferred`、`partial`、`masked`、`conflict`、`missing` 及 coverage；推断显示 interval 和 method，不新增 snapshot 外的事实。

完成标准：deterministic engine/report 只消费已验证输入，且报告显式披露所有非 official/corroborated 状态与 unavailable output。

## 能力降级分支

| 机器档位 | 人类标签 | 可用能力与动作 | 证据规范 | 实时声明 |
|---|---|---|---|---|
| full | 完整档 | 执行当前 capability report 允许的所有步骤 | [同一信源规范](source-policy.md) | 只声明 snapshot 实际验证的年份与 coverage |
| standard | 标准档 | 只执行 capability report 允许的分支，其余记录 degradation | [同一信源规范](source-policy.md) | 只声明实际验证的 coverage |
| offline | 离线档 | 只使用用户提供且 authenticated 的 local fixtures/evidence；跳过 live discovery | [同一信源规范](source-policy.md) | 禁止声称当前或实时验证 |

三种分支都只调用同一规范入口；capability loss 只能减少 coverage。
